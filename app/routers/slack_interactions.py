from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.errors import SlackVerificationError
from app.core.logging import get_logger
from app.config import get_settings
from app.db.models import WorkItem
from app.db.session import session_scope
from app.deps import get_container
from app.services.slack.modal_builder import (
    build_work_item_modal,
    parse_modal_values,
    unpack_metadata,
)
from app.services.slack.verify import verify_slack_signature

router = APIRouter(prefix="/slack", tags=["slack"])
log = get_logger(__name__)


@router.post("/interactions")
async def slack_interactions(
    request: Request,
    background_tasks: BackgroundTasks,
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
        log.warning("slack.interaction.verify_failed", error=str(e))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature") from e

    # Slack sends interactivity as application/x-www-form-urlencoded with payload=<json>.
    form = await request.form()
    raw = form.get("payload")
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing payload")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid payload")

    ptype = payload.get("type")

    if ptype == "block_actions":
        return await _handle_block_actions(payload, background_tasks)
    if ptype == "view_submission":
        return await _handle_view_submission(payload, background_tasks)
    if ptype == "view_closed":
        return JSONResponse({})

    log.info("slack.interaction.unhandled", type=ptype)
    return JSONResponse({})


async def _handle_block_actions(payload: dict, bg: BackgroundTasks) -> Any:
    actions = payload.get("actions") or []
    if not actions:
        return JSONResponse({})
    action = actions[0]
    action_id = action.get("action_id")
    trigger_id = payload.get("trigger_id")

    if action_id == "open_work_item_modal":
        if not trigger_id:
            raise HTTPException(status_code=400, detail="missing trigger_id")
        try:
            work_item_id = uuid.UUID(action.get("value", ""))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid work_item id")
        # Defer modal open: must complete within ~3s; views.open is fast but
        # the DB read can stall, so we acknowledge first.
        bg.add_task(_open_modal_task, work_item_id=work_item_id, trigger_id=trigger_id)
        return JSONResponse({})

    if action_id == "discard_work_item":
        try:
            work_item_id = uuid.UUID(action.get("value", ""))
        except ValueError:
            return JSONResponse({})
        bg.add_task(_discard_task, work_item_id=work_item_id)
        return JSONResponse({})

    log.info("slack.action.unhandled", action_id=action_id)
    return JSONResponse({})


async def _handle_view_submission(payload: dict, bg: BackgroundTasks) -> Any:
    view = payload.get("view") or {}
    if view.get("callback_id") != "work_item_submit":
        return JSONResponse({})

    slots = parse_modal_values(view)
    errors: dict[str, str] = {}
    from app.services.slack.modal_builder import BID_TASK_CONTENT, BID_PROJECT

    if not slots.get("task_content"):
        errors[BID_TASK_CONTENT] = "작업 내용을 입력해주세요."
    if not slots.get("project_key"):
        errors[BID_PROJECT] = "프로젝트를 선택해주세요."

    if errors:
        return JSONResponse({"response_action": "errors", "errors": errors})

    meta = unpack_metadata(view)
    try:
        work_item_id = uuid.UUID(meta.get("work_item_id", ""))
    except ValueError:
        return JSONResponse(
            {
                "response_action": "errors",
                "errors": {BID_TASK_CONTENT: "잘못된 요청입니다. 다시 시도해주세요."},
            }
        )

    bg.add_task(_publish_task, work_item_id=work_item_id, slots=slots)
    # Close the modal immediately; result is posted to the thread.
    return JSONResponse({"response_action": "clear"})


# --- background tasks


async def _open_modal_task(*, work_item_id: uuid.UUID, trigger_id: str) -> None:
    container = get_container()
    try:
        async with session_scope() as session:
            wi = await session.scalar(
                select(WorkItem)
                .where(WorkItem.id == work_item_id)
                .options(selectinload(WorkItem.standup_entry))
            )
            if not wi:
                log.warning("modal.open.workitem_missing", id=str(work_item_id))
                return
            try:
                teams = await container.teams_svc.list_teams()
            except Exception as e:
                log.warning("modal.open.teams_failed", error=str(e))
                teams = []
            project_keys = sorted(set(container.settings.team_to_project_map.values()))
            initial_project = wi.jira_project_key or (
                project_keys[0] if project_keys else None
            )
            issue_types: list[str] = []
            if initial_project:
                issue_types = await _issue_types_for(container, initial_project)
            stored = (wi.extracted_slots or {}).get("parent_candidates") or []
            candidates = [(c["key"], c.get("summary", "")) for c in stored]
            view = build_work_item_modal(
                wi,
                teams=teams,
                project_keys=project_keys,
                parent_candidates=candidates,
                issue_types=issue_types,
            )
        await container.slack.web.views_open(trigger_id=trigger_id, view=view)
    except Exception as e:
        log.exception("modal.open.failed", error=str(e))


async def _issue_types_for(container, project_key: str) -> list[str]:
    """Issue types for a project, cached for an hour (createmeta is stable)."""
    cache_key = f"issue_types:{project_key}"
    try:
        cached = await container.cache.get(cache_key)
        if cached is not None:
            return cached
    except Exception:
        cached = None
    try:
        types = await container.issue_svc.list_issue_types(project_key)
    except Exception as e:
        log.warning("modal.open.issue_types_failed", project=project_key, error=str(e))
        return []
    try:
        await container.cache.set(cache_key, types, ttl=3600)
    except Exception:
        pass
    return types


async def _discard_task(*, work_item_id: uuid.UUID) -> None:
    try:
        async with session_scope() as session:
            wi = await session.get(WorkItem, work_item_id)
            if wi and wi.status == "pending":
                wi.status = "discarded"
                await session.commit()
    except Exception as e:
        log.exception("modal.discard.failed", error=str(e))


async def _publish_task(*, work_item_id: uuid.UUID, slots: dict) -> None:
    container = get_container()
    if not container.publish_handler:
        log.error("publish.handler_unavailable")
        return
    try:
        async with session_scope() as session:
            await container.publish_handler.publish(
                session, work_item_id=work_item_id, confirmed_slots=slots
            )
    except Exception as e:
        # Already logged + Slack-notified inside the handler; swallow here.
        log.warning("publish.task.error", error=str(e))
