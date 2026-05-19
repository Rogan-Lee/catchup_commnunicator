from __future__ import annotations

import uuid

from app.db.models import StandupEntry, WorkItem
from app.services.slack.preview_card import build_preview_blocks


def _entry(error: str | None = None) -> StandupEntry:
    e = StandupEntry(
        channel_id="C1",
        slack_message_ts="1700000000.000100",
        author_slack_id="U1",
        raw_text="...",
        posted_at=None,  # type: ignore[arg-type]
        extraction_status="completed",
    )
    e.id = uuid.uuid4()
    e.extraction_error = error
    return e


def _item(seq: int, **slots) -> WorkItem:
    wi = WorkItem(
        standup_entry_id=uuid.uuid4(),
        sequence_no=seq,
        extracted_slots=slots,
        task_content=slots.get("task_content"),
        task_type=slots.get("task_type"),
    )
    wi.id = uuid.uuid4()
    return wi


def test_card_has_header_and_action_per_item():
    entry = _entry()
    items = [
        _item(1, task_content="OAuth 마무리", task_type="Feature", team_name="Backend"),
        _item(2, task_content="결제 QA"),
    ]
    fallback, blocks = build_preview_blocks(entry, items)

    assert "2건" in fallback
    headers = [b for b in blocks if b["type"] == "header"]
    actions = [b for b in blocks if b["type"] == "actions"]
    assert len(headers) == 1
    assert len(actions) == 2
    # Each item has a [티켓 생성] and [무시] button
    for a in actions:
        labels = [e["text"]["text"] for e in a["elements"]]
        assert "티켓 생성" in labels
        assert "무시" in labels


def test_card_marks_missing_slots():
    entry = _entry()
    items = [_item(1, task_content="something")]
    _, blocks = build_preview_blocks(entry, items)
    section_text = next(
        b["text"]["text"] for b in blocks if b["type"] == "section"
    )
    # Missing task_type / parent_feature / team / parent should be marked.
    assert ":red_circle:" in section_text


def test_card_surfaces_extraction_error():
    entry = _entry(error="rate limit")
    items = [_item(1, task_content="x")]
    _, blocks = build_preview_blocks(entry, items)
    warning_section = blocks[-1]
    assert warning_section["type"] == "section"
    assert "추출 중 일부 실패" in warning_section["text"]["text"]


def test_empty_items_shows_message():
    entry = _entry()
    fallback, blocks = build_preview_blocks(entry, [])
    assert "없습니다" in fallback
    assert len(blocks) == 1


def test_publish_disabled_adds_confirm():
    entry = _entry()
    items = [_item(1, task_content="x")]
    _, blocks = build_preview_blocks(entry, items, publish_enabled=False)
    actions = next(b for b in blocks if b["type"] == "actions")
    create_btn = next(
        e for e in actions["elements"] if e.get("action_id") == "open_work_item_modal"
    )
    assert "confirm" in create_btn


def test_publish_enabled_no_confirm():
    entry = _entry()
    items = [_item(1, task_content="x")]
    _, blocks = build_preview_blocks(entry, items, publish_enabled=True)
    actions = next(b for b in blocks if b["type"] == "actions")
    create_btn = next(
        e for e in actions["elements"] if e.get("action_id") == "open_work_item_modal"
    )
    assert "confirm" not in create_btn
