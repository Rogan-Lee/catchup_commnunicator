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

    if action_id == "publish_all_work_items":
        if not trigger_id:
            raise HTTPException(status_code=400, detail="missing trigger_id")
        try:
            entry_id = uuid.UUID(action.get("value", ""))
        except ValueError:
            return JSONResponse({})
        bg.add_task(_open_batch_modal_task, entry_id=entry_id, trigger_id=trigger_id)
        return JSONResponse({})

    if action_id in ("jira_status_progress", "jira_status_done"):
        issue_key = (action.get("value") or "").strip()
        channel = (payload.get("channel") or {}).get("id")
        message_ts = (payload.get("message") or {}).get("ts")
        if not (issue_key and channel and message_ts):
            return JSONResponse({})
        target = "done" if action_id == "jira_status_done" else "indeterminate"
        return await _start_transition(
            issue_key=issue_key,
            target_category=target,
            channel=channel,
            message_ts=message_ts,
            trigger_id=trigger_id,
            bg=bg,
        )

    from app.services.slack.status_card import (
        AID_BOARD_BULK_DONE,
        AID_BOARD_BULK_PROGRESS,
        AID_BOARD_DONE,
        AID_BOARD_PROGRESS,
        BOARD_ACTION_IDS,
        extract_board_keys,
    )

    if action_id in BOARD_ACTION_IDS:
        channel = (payload.get("channel") or {}).get("id")
        message_ts = (payload.get("message") or {}).get("ts")
        all_keys = extract_board_keys((payload.get("message") or {}).get("blocks") or [])
        if not (channel and message_ts and all_keys):
            return JSONResponse({})
        if action_id in (AID_BOARD_BULK_PROGRESS, AID_BOARD_BULK_DONE):
            targets = all_keys
        else:
            targets = [(action.get("value") or "").strip()]
        target = (
            "done"
            if action_id in (AID_BOARD_DONE, AID_BOARD_BULK_DONE)
            else "indeterminate"
        )
        bg.add_task(
            _board_transition_task,
            channel=channel,
            message_ts=message_ts,
            all_keys=all_keys,
            targets=[t for t in targets if t],
            target_category=target,
        )
        return JSONResponse({})

    log.info("slack.action.unhandled", action_id=action_id)
    return JSONResponse({})


async def _handle_view_submission(payload: dict, bg: BackgroundTasks) -> Any:
    view = payload.get("view") or {}
    callback = view.get("callback_id")

    if callback == "work_item_batch_submit":
        return await _handle_batch_submission(view, bg)
    if callback == "jira_transition_select":
        return await _handle_transition_submission(view, bg)
    if callback != "work_item_submit":
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


async def _handle_batch_submission(view: dict, bg: BackgroundTasks) -> Any:
    from app.services.slack.modal_builder import BID_PROJECT, parse_batch_values

    rows = parse_batch_values(view)
    valid = [r for r in rows if r.get("task_content")]
    if not valid:
        return JSONResponse(
            {"response_action": "errors", "errors": {BID_PROJECT: "등록할 작업이 없습니다."}}
        )
    bg.add_task(_publish_batch_task, rows=valid)
    return JSONResponse({"response_action": "clear"})


async def _handle_transition_submission(view: dict, bg: BackgroundTasks) -> Any:
    from app.services.slack.status_card import AID_TRANSITION, BID_TRANSITION

    meta = unpack_metadata(view)
    values = view.get("state", {}).get("values", {})
    option = (values.get(BID_TRANSITION, {}).get(AID_TRANSITION) or {}).get(
        "selected_option"
    )
    transition_id = option.get("value") if option else None
    if not (transition_id and meta.get("issue_key")):
        return JSONResponse({})
    bg.add_task(
        _apply_transition_task,
        issue_key=meta["issue_key"],
        transition_id=transition_id,
        channel=meta.get("channel"),
        message_ts=meta.get("message_ts"),
    )
    return JSONResponse({"response_action": "clear"})


# --- background tasks


async def _open_modal_task(*, work_item_id: uuid.UUID, trigger_id: str) -> None:
    container = get_container()

    # trigger_id is only valid for ~3s, and building the real modal needs DB +
    # Jira round-trips. Open a lightweight loading modal first to consume the
    # trigger_id, then swap in the full view with views.update (no trigger_id).
    try:
        resp = await container.slack.web.views_open(
            trigger_id=trigger_id, view=_loading_modal()
        )
        view_id = resp["view"]["id"]
    except Exception as e:
        log.exception("modal.open.failed", error=str(e))
        return

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
                task_types=container.settings.task_type_list,
            )
        await container.slack.web.views_update(view_id=view_id, view=view)
    except Exception as e:
        log.exception("modal.update.failed", error=str(e))


def _loading_modal() -> dict:
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "작업 등록"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": ":hourglass_flowing_sand: 불러오는 중..."},
            }
        ],
    }


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


async def _start_transition(
    *,
    issue_key: str,
    target_category: str,
    channel: str,
    message_ts: str,
    trigger_id: str | None,
    bg: BackgroundTasks,
) -> Any:
    """Decide 진행/완료: execute directly when unambiguous, else open a picker.

    The transitions GET runs synchronously so we can branch before the
    trigger_id (needed for the modal) expires.
    """
    from app.services.slack.status_card import build_transition_modal

    container = get_container()
    settings = container.settings
    names = (
        settings.status_done_name_list
        if target_category == "done"
        else settings.status_progress_name_list
    )

    try:
        transitions = await container.issue_svc.get_transitions(issue_key)
    except Exception as e:
        log.warning("transition.list_failed", issue=issue_key, error=str(e))
        bg.add_task(_notify_task, channel=channel, thread_ts=message_ts, text=f"❌ {issue_key} 상태 조회 실패")
        return JSONResponse({})

    candidates = [t for t in transitions if t.to_category == target_category]
    if not candidates:
        available = ", ".join(t.to_status for t in transitions) or "없음"
        bg.add_task(
            _notify_task,
            channel=channel,
            thread_ts=message_ts,
            text=f":warning: {issue_key} 현재 상태에서 변경할 수 없습니다. 가능한 전환: {available}",
        )
        return JSONResponse({})

    lowered = {n.lower() for n in names}
    named = [t for t in candidates if t.name.lower() in lowered or t.to_status.lower() in lowered]

    chosen = None
    if len(named) == 1:
        chosen = named[0]
    elif len(candidates) == 1:
        chosen = candidates[0]

    if chosen is not None:
        bg.add_task(
            _apply_transition_task,
            issue_key=issue_key,
            transition_id=chosen.id,
            channel=channel,
            message_ts=message_ts,
        )
        return JSONResponse({})

    # Ambiguous: more than one candidate. Let the user choose.
    options = named if len(named) > 1 else candidates
    if not trigger_id:
        bg.add_task(
            _apply_transition_task,
            issue_key=issue_key,
            transition_id=options[0].id,
            channel=channel,
            message_ts=message_ts,
        )
        return JSONResponse({})

    view = build_transition_modal(
        issue_key=issue_key, channel=channel, message_ts=message_ts, candidates=options
    )
    try:
        await container.slack.web.views_open(trigger_id=trigger_id, view=view)
    except Exception as e:
        log.warning("transition.modal_open_failed", issue=issue_key, error=str(e))
    return JSONResponse({})


async def _apply_transition_task(
    *, issue_key: str, transition_id: str, channel: str | None, message_ts: str | None
) -> None:
    from app.services.slack.status_card import build_status_message

    container = get_container()
    try:
        await container.issue_svc.transition_issue(issue_key, transition_id)
    except Exception as e:
        log.warning("transition.apply_failed", issue=issue_key, error=str(e))
        if channel and message_ts:
            await _notify_task(
                channel=channel, thread_ts=message_ts,
                text=f"❌ {issue_key} 상태 변경 실패: {str(e)[:150]}",
            )
        return

    if not (channel and message_ts):
        return
    try:
        brief = await container.issue_svc.get_issue_brief(issue_key)
    except Exception:
        brief = {"status_name": "변경됨", "category": "", "summary": "", "issue_type": ""}
    base = str(container.settings.atlassian_base_url).rstrip("/")
    text, blocks = build_status_message(
        issue_key=issue_key,
        issue_url=f"{base}/browse/{issue_key}",
        status_name=brief.get("status_name") or "변경됨",
        category=brief.get("category") or "",
        summary=brief.get("summary") or "",
        issue_type=brief.get("issue_type") or "",
    )
    try:
        await container.slack.web.chat_update(
            channel=channel, ts=message_ts, text=text, blocks=blocks
        )
    except Exception as e:
        log.warning("transition.update_failed", issue=issue_key, error=str(e))


async def _board_transition_task(
    *,
    channel: str,
    message_ts: str,
    all_keys: list[str],
    targets: list[str],
    target_category: str,
) -> None:
    """Transition targets (auto-pick by category+name), then rebuild the board."""
    from app.services.slack.status_card import build_status_board, pick_transition

    container = get_container()
    settings = container.settings
    names = (
        settings.status_done_name_list
        if target_category == "done"
        else settings.status_progress_name_list
    )

    failures: list[str] = []
    for key in targets:
        try:
            transitions = await container.issue_svc.get_transitions(key)
            picked = pick_transition(transitions, target_category, names)
            if not picked:
                avail = (
                    ", ".join(f"{t.to_status}({t.to_category})" for t in transitions)
                    or "없음"
                )
                failures.append(f"{key}: 대상 상태로 가는 전환이 없음 (가능: {avail})")
                continue
            await container.issue_svc.transition_issue(key, picked.id)
        except Exception as e:
            log.warning("board.transition_failed", issue=key, error=str(e))
            failures.append(f"{key}: 전환 실패 — {str(e)[:150]}")

    tickets = []
    for key in all_keys:
        try:
            brief = await container.issue_svc.get_issue_brief(key)
        except Exception as e:
            log.warning("board.status_failed", issue=key, error=str(e))
            brief = {"status_name": "?", "category": "", "summary": "", "issue_type": ""}
        tickets.append({"key": key, **brief})

    base = str(settings.atlassian_base_url)
    fallback, blocks = build_status_board(tickets, base_url=base)
    try:
        await container.slack.web.chat_update(
            channel=channel, ts=message_ts, text=fallback, blocks=blocks
        )
    except Exception as e:
        log.warning("board.update_failed", error=str(e))

    if failures:
        await _notify_task(
            channel=channel,
            thread_ts=message_ts,
            text=":warning: 상태 변경 실패\n" + "\n".join(f"• {f}" for f in failures),
        )


async def _notify_task(*, channel: str, thread_ts: str, text: str) -> None:
    container = get_container()
    try:
        await container.slack.post_message(channel=channel, thread_ts=thread_ts, text=text)
    except Exception as e:
        log.warning("transition.notify_failed", error=str(e))


async def _open_batch_modal_task(*, entry_id: uuid.UUID, trigger_id: str) -> None:
    from app.services.slack.modal_builder import build_batch_modal

    container = get_container()
    try:
        resp = await container.slack.web.views_open(
            trigger_id=trigger_id, view=_loading_modal()
        )
        view_id = resp["view"]["id"]
    except Exception as e:
        log.exception("batch_modal.open_failed", error=str(e))
        return

    try:
        async with session_scope() as session:
            pending = (
                await session.scalars(
                    select(WorkItem)
                    .where(
                        WorkItem.standup_entry_id == entry_id,
                        WorkItem.status == "pending",
                    )
                    .order_by(WorkItem.sequence_no)
                )
            ).all()
            if not pending:
                await container.slack.web.views_update(
                    view_id=view_id, view=_info_modal("등록할 작업이 없습니다.")
                )
                return
            project_keys = sorted(set(container.settings.team_to_project_map.values()))
            initial_project = next(
                (w.jira_project_key for w in pending if w.jira_project_key),
                project_keys[0] if project_keys else None,
            )
            issue_types = (
                await _issue_types_for(container, initial_project)
                if initial_project
                else []
            )
            view = build_batch_modal(
                list(pending),
                project_keys=project_keys,
                issue_types=issue_types,
                task_types=container.settings.task_type_list,
            )
        await container.slack.web.views_update(view_id=view_id, view=view)
    except Exception as e:
        log.exception("batch_modal.update_failed", error=str(e))


def _info_modal(message: str) -> dict:
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "전체 등록"},
        "close": {"type": "plain_text", "text": "닫기"},
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": message}}],
    }


async def _publish_batch_task(*, rows: list[dict]) -> None:
    from app.services.slack.status_card import build_status_board

    container = get_container()
    if not container.publish_handler:
        log.error("publish.handler_unavailable")
        return

    projects = set(container.settings.team_to_project_map.values())
    default_project = next(iter(projects)) if len(projects) == 1 else None

    # Channel/thread for the consolidated board (from the first item's entry).
    channel = thread_ts = None
    try:
        first = uuid.UUID(rows[0]["work_item_id"])
        async with session_scope() as session:
            wi0 = await session.scalar(
                select(WorkItem)
                .where(WorkItem.id == first)
                .options(selectinload(WorkItem.standup_entry))
            )
            if wi0 and wi0.standup_entry:
                channel = wi0.standup_entry.channel_id
                thread_ts = wi0.standup_entry.slack_message_ts
    except (ValueError, TypeError, KeyError, IndexError):
        pass

    log.info("publish.batch.start", count=len(rows))
    created = []
    for r in rows:
        try:
            wid = uuid.UUID(r["work_item_id"])
        except (ValueError, TypeError, KeyError):
            continue
        slots = {
            "task_content": r.get("task_content"),
            "parent_feature": r.get("parent_feature"),
            "task_type": r.get("task_type"),
            "issue_type": r.get("issue_type"),
            "project_key": r.get("project_key") or default_project,
        }
        if r.get("parent_issue_key"):
            slots["parent_issue_key"] = r["parent_issue_key"]
        try:
            async with session_scope() as session:
                wi = await session.get(WorkItem, wid)
                if not wi or wi.status != "pending":
                    continue
                issue = await container.publish_handler.publish(
                    session, work_item_id=wid, confirmed_slots=slots, notify=False
                )
                created.append(issue)
        except Exception as e:
            log.warning("publish.batch.item_failed", work_item=str(wid), error=str(e))

    if created and channel:
        base = str(container.settings.atlassian_base_url)
        tickets = [
            {
                "key": c.key,
                "status_name": "할 일",
                "category": "new",
                "summary": c.summary,
                "issue_type": c.issue_type,
            }
            for c in created
        ]
        fallback, blocks = build_status_board(tickets, base_url=base)
        try:
            await container.slack.post_message(
                channel=channel, thread_ts=thread_ts, text=fallback, blocks=blocks
            )
        except Exception as e:
            log.warning("publish.batch.board_failed", error=str(e))


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
