from __future__ import annotations

import json
from typing import Any

from app.db.models import WorkItem
from app.services.atlassian.types import Team
from app.services.llm.schemas import TaskType

CALLBACK_ID = "work_item_submit"

# Block IDs (used by the submit handler to read values back).
BID_TEAM = "team_block"
BID_PROJECT = "project_block"
BID_TASK_TYPE = "task_type_block"
BID_PARENT_FEATURE = "parent_feature_block"
BID_TASK_CONTENT = "task_content_block"
BID_PARENT_ISSUE = "parent_issue_block"

AID_TEAM = "team_select"
AID_PROJECT = "project_select"
AID_TASK_TYPE = "task_type_select"
AID_PARENT_FEATURE = "parent_feature_input"
AID_TASK_CONTENT = "task_content_input"
AID_PARENT_ISSUE = "parent_issue_input"


def build_work_item_modal(
    work_item: WorkItem,
    *,
    teams: list[Team],
    project_keys: list[str],
    parent_candidates: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Build a `views.open` payload for the work-item modal.

    private_metadata carries work_item_id plus the original Slack channel/ts
    so the submit handler can reply to the thread without another DB roundtrip.
    """
    slots = work_item.extracted_slots or {}
    initial_team_id = work_item.jira_team_id or slots.get("team_id")
    initial_project = work_item.jira_project_key or _first(project_keys)
    initial_task_type = work_item.task_type or slots.get("task_type")
    initial_parent_feature = work_item.parent_feature or slots.get("parent_feature") or ""
    initial_task_content = work_item.task_content or slots.get("task_content") or ""
    initial_parent_key = work_item.parent_issue_key or slots.get("parent_issue_hint") or ""

    blocks: list[dict[str, Any]] = []

    if teams:
        blocks.append(
            _static_select_input(
                block_id=BID_TEAM,
                action_id=AID_TEAM,
                label="팀",
                options=[(t.id, t.name) for t in teams],
                initial_value=initial_team_id,
            )
        )

    if project_keys:
        blocks.append(
            _static_select_input(
                block_id=BID_PROJECT,
                action_id=AID_PROJECT,
                label="Jira 프로젝트",
                options=[(k, k) for k in project_keys],
                initial_value=initial_project,
            )
        )

    blocks.append(
        _static_select_input(
            block_id=BID_TASK_TYPE,
            action_id=AID_TASK_TYPE,
            label="작업 구분",
            options=[(t.value, t.value) for t in TaskType],
            initial_value=initial_task_type,
        )
    )

    blocks.append(
        _text_input(
            block_id=BID_PARENT_FEATURE,
            action_id=AID_PARENT_FEATURE,
            label="상위 기능",
            initial_value=initial_parent_feature,
            optional=True,
        )
    )

    blocks.append(
        _text_input(
            block_id=BID_TASK_CONTENT,
            action_id=AID_TASK_CONTENT,
            label="작업 내용",
            initial_value=initial_task_content,
            multiline=True,
        )
    )

    # Parent ticket: plain text input for now (W3 swaps to external_select).
    blocks.append(
        _text_input(
            block_id=BID_PARENT_ISSUE,
            action_id=AID_PARENT_ISSUE,
            label="부모 티켓 (Jira 키, 예: CATCHUP-42)",
            initial_value=initial_parent_key,
            optional=True,
        )
    )

    if parent_candidates:
        suggestions = "\n".join(f"• {k} — {s}" for k, s in parent_candidates[:5])
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"_추천 부모 티켓_\n{suggestions}"}
                ],
            }
        )

    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": _pack_metadata(work_item),
        "title": {"type": "plain_text", "text": "작업 등록"},
        "submit": {"type": "plain_text", "text": "등록"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": blocks,
    }


def parse_modal_values(view: dict[str, Any]) -> dict[str, Any]:
    """Pull confirmed slot values out of a view_submission payload."""
    values = view.get("state", {}).get("values", {})

    def selected(block_id: str, action_id: str) -> str | None:
        block = values.get(block_id) or {}
        option = (block.get(action_id) or {}).get("selected_option")
        return option.get("value") if option else None

    def text(block_id: str, action_id: str) -> str | None:
        block = values.get(block_id) or {}
        return ((block.get(action_id) or {}).get("value") or "").strip() or None

    return {
        "team_id": selected(BID_TEAM, AID_TEAM),
        "project_key": selected(BID_PROJECT, AID_PROJECT),
        "task_type": selected(BID_TASK_TYPE, AID_TASK_TYPE),
        "parent_feature": text(BID_PARENT_FEATURE, AID_PARENT_FEATURE),
        "task_content": text(BID_TASK_CONTENT, AID_TASK_CONTENT),
        "parent_issue_key": text(BID_PARENT_ISSUE, AID_PARENT_ISSUE),
    }


def unpack_metadata(view: dict[str, Any]) -> dict[str, str]:
    raw = view.get("private_metadata") or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


# --- helpers


def _pack_metadata(wi: WorkItem) -> str:
    return json.dumps(
        {
            "work_item_id": str(wi.id),
            "channel_id": wi.standup_entry.channel_id if wi.standup_entry else "",
            "thread_ts": wi.standup_entry.slack_message_ts if wi.standup_entry else "",
        }
    )


def _first(items: list[str]) -> str | None:
    return items[0] if items else None


def _static_select_input(
    *,
    block_id: str,
    action_id: str,
    label: str,
    options: list[tuple[str, str]],
    initial_value: str | None,
) -> dict[str, Any]:
    opt_list = [
        {"text": {"type": "plain_text", "text": name}, "value": val}
        for val, name in options
    ]
    element: dict[str, Any] = {
        "type": "static_select",
        "action_id": action_id,
        "options": opt_list,
    }
    initial = next(
        (o for o in opt_list if o["value"] == initial_value), None
    ) if initial_value else None
    if initial:
        element["initial_option"] = initial
    return {
        "type": "input",
        "block_id": block_id,
        "label": {"type": "plain_text", "text": label},
        "element": element,
    }


def _text_input(
    *,
    block_id: str,
    action_id: str,
    label: str,
    initial_value: str,
    optional: bool = False,
    multiline: bool = False,
) -> dict[str, Any]:
    element: dict[str, Any] = {
        "type": "plain_text_input",
        "action_id": action_id,
    }
    if initial_value:
        element["initial_value"] = initial_value
    if multiline:
        element["multiline"] = True
    block: dict[str, Any] = {
        "type": "input",
        "block_id": block_id,
        "label": {"type": "plain_text", "text": label},
        "element": element,
    }
    if optional:
        block["optional"] = True
    return block
