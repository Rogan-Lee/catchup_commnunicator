from __future__ import annotations

from typing import Any

from app.db.models import StandupEntry, WorkItem


def build_preview_blocks(
    entry: StandupEntry,
    items: list[WorkItem],
    *,
    publish_enabled: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    """Build the thread-reply preview card.

    Returns (fallback_text, blocks).
    """
    if not items:
        text = "추출된 작업 항목이 없습니다."
        blocks = [_section(text)]
        return text, blocks

    header_text = f"추출된 작업 {len(items)}건"
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header_text},
        }
    ]

    if publish_enabled and len(items) > 1:
        blocks.append(_publish_all_block(entry, len(items)))

    for item in items:
        blocks.extend(_item_blocks(item, publish_enabled=publish_enabled))

    if entry.extraction_error:
        blocks.append(
            _section(f":warning: 추출 중 일부 실패: {entry.extraction_error[:200]}")
        )

    return header_text, blocks


def _publish_all_block(entry: StandupEntry, count: int) -> dict[str, Any]:
    # Opens a batch-edit modal listing all items; review/edit then submit.
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": f"✏️ 전체 검토·등록 ({count}건)"},
                "style": "primary",
                "action_id": "publish_all_work_items",
                "value": str(entry.id),
            }
        ],
    }


def _item_blocks(item: WorkItem, *, publish_enabled: bool) -> list[dict[str, Any]]:
    slots = item.extracted_slots or {}
    lines = [
        f"*#{item.sequence_no}* — {_field('내용', slots.get('task_content') or item.task_content)}",
        _field("작업 구분", slots.get("task_type") or item.task_type),
        _field("상위 기능", slots.get("parent_feature") or item.parent_feature),
        _field("팀", slots.get("team_name") or slots.get("team_id") or item.jira_team_id),
        _field("부모 티켓", slots.get("parent_issue_hint") or item.parent_issue_key),
    ]
    section = _section("\n".join(lines))

    actions = {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "티켓 생성"},
                "style": "primary",
                "action_id": "open_work_item_modal",
                "value": str(item.id),
                **({} if publish_enabled else {"confirm": _disabled_confirm()}),
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "무시"},
                "action_id": "discard_work_item",
                "value": str(item.id),
            },
        ],
    }
    return [section, actions, {"type": "divider"}]


def _field(label: str, value: Any) -> str:
    if value in (None, "", []):
        return f":red_circle: {label}: _누락_"
    return f"{label}: {value}"


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _disabled_confirm() -> dict[str, Any]:
    # W1 has no publish path yet — surface that to the user explicitly.
    return {
        "title": {"type": "plain_text", "text": "준비 중"},
        "text": {
            "type": "plain_text",
            "text": "발행 기능은 다음 단계(W2)에서 활성화됩니다.",
        },
        "confirm": {"type": "plain_text", "text": "확인"},
        "deny": {"type": "plain_text", "text": "닫기"},
    }
