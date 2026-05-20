from __future__ import annotations

from typing import Any

from app.services.atlassian.jira_issues import Transition

CATEGORY_PROGRESS = "indeterminate"
CATEGORY_DONE = "done"

AID_PROGRESS = "jira_status_progress"
AID_DONE = "jira_status_done"


def pick_transition(
    transitions: list[Transition],
    target_category: str,
    preferred_names: list[str],
) -> Transition | None:
    """Choose the transition that lands in target_category.

    Preference: a transition (or its target status) whose name matches the
    configured list, else the first transition into the target category.
    Matching is case-insensitive. Returns None when no transition reaches it
    (e.g. the workflow forbids that jump from the current status).
    """
    lowered = {n.lower() for n in preferred_names}
    in_category = [t for t in transitions if t.to_category == target_category]
    for t in in_category:
        if t.name.lower() in lowered or t.to_status.lower() in lowered:
            return t
    return in_category[0] if in_category else None


def _ticket_line(issue_url: str, issue_key: str, status_name: str, summary: str, issue_type: str) -> str:
    head = f"🎫 <{issue_url}|{issue_key}> · 상태: *{status_name}*"
    type_part = f"`{issue_type}` " if issue_type else ""
    detail = f"{type_part}{summary}".strip()
    return f"{head}\n{detail}" if detail else head


def build_status_message(
    *,
    issue_key: str,
    issue_url: str,
    status_name: str,
    category: str,
    summary: str = "",
    issue_type: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    """Status line + transition buttons, rebuilt on every state change.

    Buttons shown depend on the current category:
      new           → [진행] [완료]
      indeterminate → [완료]
      done          → (none)
    """
    fallback = f"{issue_key} · 상태: {status_name}"
    section = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": _ticket_line(issue_url, issue_key, status_name, summary, issue_type),
        },
    }
    blocks: list[dict[str, Any]] = [section]

    elements: list[dict[str, Any]] = []
    if category not in (CATEGORY_PROGRESS, CATEGORY_DONE):
        elements.append(_button("진행", AID_PROGRESS, issue_key, style="primary"))
    if category != CATEGORY_DONE:
        elements.append(_button("완료", AID_DONE, issue_key))
    if elements:
        blocks.append({"type": "actions", "elements": elements})

    return fallback, blocks


AID_BOARD_PROGRESS = "board_status_progress"
AID_BOARD_DONE = "board_status_done"
AID_BOARD_BULK_PROGRESS = "board_bulk_progress"
AID_BOARD_BULK_DONE = "board_bulk_done"

BOARD_ACTION_IDS = (
    AID_BOARD_PROGRESS,
    AID_BOARD_DONE,
    AID_BOARD_BULK_PROGRESS,
    AID_BOARD_BULK_DONE,
)


def build_status_board(
    tickets: list[dict[str, Any]], *, base_url: str
) -> tuple[str, list[dict[str, Any]]]:
    """Consolidated status board for a batch of created tickets.

    tickets: [{key, status_name, category}]. Per-ticket 진행/완료 buttons plus
    bulk 전체 진행/전체 완료. Rebuilt in place after each change.
    """
    import json

    base = base_url.rstrip("/")
    keys = [t["key"] for t in tickets]
    bulk_value = json.dumps({"keys": keys})

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📋 발행 완료 ({len(tickets)}건)"},
        }
    ]
    for t in tickets:
        url = f"{base}/browse/{t['key']}"
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _ticket_line(
                        url,
                        t["key"],
                        t.get("status_name") or "?",
                        t.get("summary") or "",
                        t.get("issue_type") or "",
                    ),
                },
            }
        )
        category = t.get("category", "")
        elements: list[dict[str, Any]] = []
        if category not in (CATEGORY_PROGRESS, CATEGORY_DONE):
            elements.append(_button("진행", AID_BOARD_PROGRESS, t["key"], style="primary"))
        if category != CATEGORY_DONE:
            elements.append(_button("완료", AID_BOARD_DONE, t["key"]))
        if elements:
            blocks.append({"type": "actions", "elements": elements})

    blocks.append({"type": "divider"})
    blocks.append(
        {
            "type": "actions",
            "elements": [
                _button("⏩ 전체 진행", AID_BOARD_BULK_PROGRESS, bulk_value, style="primary"),
                _button("✅ 전체 완료", AID_BOARD_BULK_DONE, bulk_value),
            ],
        }
    )
    return f"발행 완료 {len(tickets)}건", blocks


def extract_board_keys(blocks: list[dict[str, Any]]) -> list[str]:
    """Recover the full key list from a rendered board (bulk button value)."""
    import json

    for b in blocks:
        if b.get("type") != "actions":
            continue
        for e in b.get("elements", []):
            if e.get("action_id") in (AID_BOARD_BULK_PROGRESS, AID_BOARD_BULK_DONE):
                try:
                    return list(json.loads(e.get("value") or "{}").get("keys") or [])
                except (json.JSONDecodeError, AttributeError):
                    return []
    return []


TRANSITION_SELECT_CALLBACK = "jira_transition_select"
BID_TRANSITION = "transition_block"
AID_TRANSITION = "transition_select"


def build_transition_modal(
    *,
    issue_key: str,
    channel: str,
    message_ts: str,
    candidates: list[Transition],
) -> dict[str, Any]:
    """Modal to choose among multiple transitions in the same category."""
    import json

    options = [
        {
            "text": {"type": "plain_text", "text": f"{t.name} → {t.to_status}"[:75]},
            "value": t.id,
        }
        for t in candidates
    ]
    return {
        "type": "modal",
        "callback_id": TRANSITION_SELECT_CALLBACK,
        "private_metadata": json.dumps(
            {"issue_key": issue_key, "channel": channel, "message_ts": message_ts}
        ),
        "title": {"type": "plain_text", "text": "상태 변경"},
        "submit": {"type": "plain_text", "text": "변경"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": [
            {
                "type": "input",
                "block_id": BID_TRANSITION,
                "label": {"type": "plain_text", "text": f"{issue_key} 전환 선택"},
                "element": {
                    "type": "static_select",
                    "action_id": AID_TRANSITION,
                    "options": options,
                    "initial_option": options[0],
                },
            }
        ],
    }


def _button(label: str, action_id: str, value: str, style: str | None = None) -> dict[str, Any]:
    btn: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": label},
        "action_id": action_id,
        "value": value,
    }
    if style:
        btn["style"] = style
    return btn
