from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.config import get_settings
from app.core.errors import SlackVerificationError
from app.core.logging import get_logger
from app.db.models import WorkItem
from app.db.session import session_scope
from app.deps import get_container
from app.services.slack.modal_builder import (
    AID_PARENT_ISSUE,
    candidate_options,
    unpack_metadata,
)
from app.services.slack.verify import verify_slack_signature

router = APIRouter(prefix="/slack", tags=["slack"])
log = get_logger(__name__)


@router.post("/options")
async def slack_options(
    request: Request,
    x_slack_signature: str | None = Header(default=None),
    x_slack_request_timestamp: str | None = Header(default=None),
) -> Any:
    settings = get_settings()
    body = await request.body()

    try:
        verify_slack_signature(
            signing_secret=settings.slack_signing_secret,
            timestamp=x_slack_request_timestamp,
            signature=x_slack_signature,
            body=body,
        )
    except SlackVerificationError as e:
        log.warning("slack.options.verify_failed", error=str(e))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature") from e

    form = await request.form()
    raw = form.get("payload")
    if not raw:
        return JSONResponse({"options": []})
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return JSONResponse({"options": []})

    action_id = payload.get("action_id")
    query = (payload.get("value") or "").strip()

    if action_id != AID_PARENT_ISSUE:
        return JSONResponse({"options": []})
    if not query:
        return JSONResponse({"options": []})

    project_key = await _resolve_project_key(payload)

    container = get_container()
    cache_key = f"parent_search:{project_key or '*'}:{query.lower()}"
    cached = await container.cache.get(cache_key)
    if cached is not None:
        return JSONResponse({"options": candidate_options(cached)})

    try:
        candidates = await container.parent_resolver.live_search(
            project_key=project_key, query=query, limit=10
        )
    except Exception as e:
        log.warning("slack.options.search_failed", error=str(e))
        return JSONResponse({"options": []})

    serialised = [(c.key, c.summary) for c in candidates]
    await container.cache.set(cache_key, serialised, ttl=60)
    return JSONResponse({"options": candidate_options(serialised)})


async def _resolve_project_key(payload: dict) -> str | None:
    """Use the view's stored work_item to determine the project for search."""
    view = payload.get("view") or {}
    meta = unpack_metadata(view)
    raw_id = meta.get("work_item_id")
    if not raw_id:
        return None
    try:
        work_item_id = uuid.UUID(raw_id)
    except ValueError:
        return None
    try:
        async with session_scope() as session:
            wi = await session.scalar(
                select(WorkItem).where(WorkItem.id == work_item_id)
            )
            return wi.jira_project_key if wi else None
    except Exception:
        return None
