from __future__ import annotations

import hmac
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from app.config import get_settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.deps import get_container

router = APIRouter(prefix="/standup", tags=["standup"])
log = get_logger(__name__)


class StructuredItem(BaseModel):
    """One pre-structured work item from a multi-field modal."""

    task_content: str = Field(..., min_length=1, max_length=400)
    task_type: str | None = None
    parent_feature: str | None = None
    parent_issue_hint: str | None = None


class IngestPayload(BaseModel):
    """Payload posted by the slash-command edge function after a user submits
    a /오늘할일 (or similar) modal.

    Two shapes are accepted:
      - `items`: pre-structured work items from a multi-field modal (preferred;
        no extraction needed).
      - `text` (+ optional blocker/yesterday): free text, run through the
        rule-based extractor.
    """

    modal_type: Literal["todo", "standup"] = "todo"
    slack_user_id: str = Field(..., min_length=1)
    channel_id: str = Field(..., min_length=1)
    # ts of the message the edge function posted via chat.postMessage; used
    # both as the dedupe key and the thread root for the preview card.
    message_ts: str = Field(..., min_length=1)
    items: list[StructuredItem] | None = None
    text: str | None = Field(default=None, max_length=8000)
    blocker: str | None = None
    # Optional for /현황공유 — included in the extraction prompt but doesn't
    # drive Jira ticket creation by itself.
    yesterday: str | None = None

    @model_validator(mode="after")
    def _require_items_or_text(self) -> IngestPayload:
        if not self.items and not (self.text and self.text.strip()):
            raise ValueError("either items or text is required")
        return self


@router.post("/ingest")
async def standup_ingest(
    payload: IngestPayload,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    settings = get_settings()
    expected = settings.standup_ingest_token
    if not expected:
        log.error("standup.ingest.token_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ingest endpoint not configured",
        )
    if not _valid_bearer(authorization, expected):
        log.warning(
            "standup.ingest.unauthorized",
            channel=payload.channel_id,
            user=payload.slack_user_id,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    log.info(
        "standup.ingest.received",
        modal_type=payload.modal_type,
        channel=payload.channel_id,
        user=payload.slack_user_id,
        ts=payload.message_ts,
        items=len(payload.items) if payload.items else 0,
        text_len=len(payload.text or ""),
    )

    # Run inline so CPU is allocated for the whole job. Cloud Run throttles
    # CPU after the response is sent, which makes BackgroundTasks crawl on
    # request-based billing. The edge function already ACKed Slack, so we
    # have plenty of time to finish here.
    await _process(payload)
    return JSONResponse({"ok": True})


def _valid_bearer(header_value: str | None, expected: str) -> bool:
    if not header_value or not header_value.startswith("Bearer "):
        return False
    presented = header_value[len("Bearer ") :].strip()
    return hmac.compare_digest(presented, expected)


async def _process(payload: IngestPayload) -> None:
    posted_at = _ts_to_datetime(payload.message_ts)
    container = get_container()
    try:
        async with session_scope() as session:
            if payload.items:
                await container.extract_handler.handle_structured(
                    session,
                    channel_id=payload.channel_id,
                    message_ts=payload.message_ts,
                    author_slack_id=payload.slack_user_id,
                    items=[i.model_dump() for i in payload.items],
                    posted_at=posted_at,
                )
            else:
                await container.extract_handler.handle_message(
                    session,
                    channel_id=payload.channel_id,
                    message_ts=payload.message_ts,
                    author_slack_id=payload.slack_user_id,
                    text=_compose_text(payload),
                    posted_at=posted_at,
                )
    except Exception as e:
        log.exception(
            "standup.ingest.failed",
            error=str(e),
            ts=payload.message_ts,
        )


def _compose_text(payload: IngestPayload) -> str:
    """Stitch modal fields into the single text blob the extractor consumes.

    We label sections so the LLM prompt can distinguish "today's plan"
    (which drives Jira tickets) from yesterday/blockers (context only).
    """
    parts: list[str] = []
    if payload.yesterday and payload.yesterday.strip():
        parts.append(f"[어제]\n{payload.yesterday.strip()}")
    parts.append(f"[오늘 할 일]\n{(payload.text or '').strip()}")
    if payload.blocker and payload.blocker.strip():
        parts.append(f"[차단 요소]\n{payload.blocker.strip()}")
    return "\n\n".join(parts)


def _ts_to_datetime(slack_ts: str) -> datetime:
    try:
        return datetime.fromtimestamp(float(slack_ts), tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(tz=timezone.utc)
