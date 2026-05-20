from __future__ import annotations

import json
from typing import Any

from app.db.models import WorkItem
from app.services.atlassian.types import Team
from app.services.llm.schemas import TaskType

CALLBACK_ID = "work_item_submit"
BATCH_CALLBACK_ID = "work_item_batch_submit"

# Block IDs (used by the submit handler to read values back).
BID_TEAM = "team_block"
BID_PROJECT = "project_block"
BID_TASK_TYPE = "task_type_block"
BID_ISSUE_TYPE = "issue_type_block"
BID_PARENT_FEATURE = "parent_feature_block"
BID_TASK_CONTENT = "task_content_block"
BID_PARENT_ISSUE = "parent_issue_block"

AID_TEAM = "team_select"
AID_PROJECT = "project_select"
AID_TASK_TYPE = "task_type_select"
AID_ISSUE_TYPE = "issue_type_select"
AID_PARENT_FEATURE = "parent_feature_input"
AID_TASK_CONTENT = "task_content_input"
AID_PARENT_ISSUE = "parent_issue_input"


def build_work_item_modal(
    work_item: WorkItem,
    *,
    teams: list[Team],
    project_keys: list[str],
    parent_candidates: list[tuple[str, str]] | None = None,
    issue_types: list[str] | None = None,
    task_types: list[str] | None = None,
) -> dict[str, Any]:
    """Build a `views.open` payload for the work-item modal.

    private_metadata carries work_item_id plus the original Slack channel/ts
    so the submit handler can reply to the thread without another DB roundtrip.

    issue_types: Jira issue type names for this project (fetched live). When
    empty the issue-type dropdown is omitted and publish falls back to the
    task-type → issue-type mapping.
    task_types: labels for the 작업 구분 dropdown; defaults to the TaskType enum.
    """
    slots = work_item.extracted_slots or {}
    initial_team_id = work_item.jira_team_id or slots.get("team_id")
    initial_project = work_item.jira_project_key or _first(project_keys)
    initial_task_type = work_item.task_type or slots.get("task_type")
    task_type_options = task_types or [t.value for t in TaskType]
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
            options=[(t, t) for t in task_type_options],
            initial_value=initial_task_type,
        )
    )

    if issue_types:
        initial_issue_type = work_item.confirmed_slots and work_item.confirmed_slots.get(
            "issue_type"
        )
        blocks.append(
            _static_select_input(
                block_id=BID_ISSUE_TYPE,
                action_id=AID_ISSUE_TYPE,
                label="티켓 유형",
                options=[(t, t) for t in issue_types],
                initial_value=initial_issue_type,
            )
        )

    blocks.append(
        _text_input(
            block_id=BID_PARENT_FEATURE,
            action_id=AID_PARENT_FEATURE,
            label="상위 기능",
            initial_value=initial_parent_feature,
            optional=True,
            hint="요약에 [상위기능] 형태로 자동 표시됩니다. 대괄호 없이 입력하세요.",
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

    blocks.append(
        _parent_issue_block(
            candidates=parent_candidates or [],
            initial_key=initial_parent_key if is_issue_key(initial_parent_key) else None,
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
        "issue_type": selected(BID_ISSUE_TYPE, AID_ISSUE_TYPE),
        "parent_feature": text(BID_PARENT_FEATURE, AID_PARENT_FEATURE),
        "task_content": text(BID_TASK_CONTENT, AID_TASK_CONTENT),
        "parent_issue_key": selected(BID_PARENT_ISSUE, AID_PARENT_ISSUE),
    }


def build_batch_modal(
    work_items: list[WorkItem],
    *,
    project_keys: list[str],
    issue_types: list[str] | None = None,
    task_types: list[str] | None = None,
) -> dict[str, Any]:
    """One modal to review/edit every pending work item before publishing.

    A shared project selector at the top, then per-item content / 상위기능 /
    작업구분 / 티켓유형. Parent ticket and team keep their resolved values.
    private_metadata carries the ordered work_item ids so the submit handler
    maps fields back by index.
    """
    task_type_options = task_types or [t.value for t in TaskType]
    initial_project = next(
        (w.jira_project_key for w in work_items if w.jira_project_key),
        _first(project_keys),
    )

    blocks: list[dict[str, Any]] = []
    if project_keys:
        blocks.append(
            _static_select_input(
                block_id=BID_PROJECT,
                action_id=AID_PROJECT,
                label="Jira 프로젝트 (전체 공통)",
                options=[(k, k) for k in project_keys],
                initial_value=initial_project,
            )
        )
        blocks.append({"type": "divider"})

    for idx, wi in enumerate(work_items, start=1):
        slots = wi.extracted_slots or {}
        content = wi.task_content or slots.get("task_content") or ""
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*작업 {idx}*"}}
        )
        blocks.append(
            _text_input(
                block_id=f"b_tc_{idx}",
                action_id="e_tc",
                label="작업 내용",
                initial_value=content,
                multiline=True,
            )
        )
        blocks.append(
            _text_input(
                block_id=f"b_pf_{idx}",
                action_id="e_pf",
                label="상위 기능",
                initial_value=wi.parent_feature or slots.get("parent_feature") or "",
                optional=True,
                hint="요약에 [상위기능] 형태로 자동 표시됩니다. 대괄호 없이 입력하세요.",
            )
        )
        blocks.append(
            _static_select_input(
                block_id=f"b_tt_{idx}",
                action_id="e_tt",
                label="작업 구분",
                options=[(t, t) for t in task_type_options],
                initial_value=wi.task_type or slots.get("task_type"),
                optional=True,
            )
        )
        if issue_types:
            blocks.append(
                _static_select_input(
                    block_id=f"b_it_{idx}",
                    action_id="e_it",
                    label="티켓 유형",
                    options=[(t, t) for t in issue_types],
                    initial_value=None,
                    optional=True,
                )
            )
        blocks.append({"type": "divider"})

    return {
        "type": "modal",
        "callback_id": BATCH_CALLBACK_ID,
        "private_metadata": json.dumps({"item_ids": [str(w.id) for w in work_items]}),
        "title": {"type": "plain_text", "text": "전체 등록"},
        "submit": {"type": "plain_text", "text": "전체 등록"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": blocks,
    }


def parse_batch_values(view: dict[str, Any]) -> list[dict[str, Any]]:
    """Map batch-modal state back to per-item slot dicts (in modal order)."""
    values = view.get("state", {}).get("values", {})
    meta = unpack_metadata(view)
    item_ids = meta.get("item_ids") or []

    def selected(block_id: str, action_id: str) -> str | None:
        option = (values.get(block_id, {}).get(action_id) or {}).get("selected_option")
        return option.get("value") if option else None

    def text(block_id: str, action_id: str) -> str | None:
        return ((values.get(block_id, {}).get(action_id) or {}).get("value") or "").strip() or None

    project_key = selected(BID_PROJECT, AID_PROJECT)
    out = []
    for idx, wid in enumerate(item_ids, start=1):
        out.append(
            {
                "work_item_id": wid,
                "task_content": text(f"b_tc_{idx}", "e_tc"),
                "parent_feature": text(f"b_pf_{idx}", "e_pf"),
                "task_type": selected(f"b_tt_{idx}", "e_tt"),
                "issue_type": selected(f"b_it_{idx}", "e_it"),
                "project_key": project_key,
            }
        )
    return out


def candidate_options(candidates: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Convert (key, summary) tuples to Slack option dicts for external_select."""
    return [
        {
            "text": {
                "type": "plain_text",
                "text": _truncate(f"{key} — {summary}", 75),
            },
            "value": key,
        }
        for key, summary in candidates
    ]


def _truncate(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _parent_issue_block(
    *,
    candidates: list[tuple[str, str]],
    initial_key: str | None,
) -> dict[str, Any]:
    options = candidate_options(candidates)
    element: dict[str, Any] = {
        "type": "external_select",
        "action_id": AID_PARENT_ISSUE,
        "min_query_length": 1,
        "placeholder": {"type": "plain_text", "text": "검색하여 선택..."},
    }
    if initial_key:
        initial = next((o for o in options if o["value"] == initial_key), None)
        if not initial:
            initial = {
                "text": {"type": "plain_text", "text": initial_key},
                "value": initial_key,
            }
        element["initial_option"] = initial
    return {
        "type": "input",
        "block_id": BID_PARENT_ISSUE,
        "label": {"type": "plain_text", "text": "부모 티켓"},
        "element": element,
        "optional": True,
    }


def is_issue_key(value: str | None) -> bool:
    import re

    return bool(value) and bool(re.match(r"^[A-Z][A-Z0-9_]*-\d+$", value or ""))


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
    optional: bool = False,
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
    block: dict[str, Any] = {
        "type": "input",
        "block_id": block_id,
        "label": {"type": "plain_text", "text": label},
        "element": element,
    }
    if optional:
        block["optional"] = True
    return block


def _text_input(
    *,
    block_id: str,
    action_id: str,
    label: str,
    initial_value: str,
    optional: bool = False,
    multiline: bool = False,
    hint: str | None = None,
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
    if hint:
        block["hint"] = {"type": "plain_text", "text": hint}
    return block
