from __future__ import annotations

from app.services.atlassian.jira_issues import Transition
from app.services.slack.status_card import (
    AID_DONE,
    AID_PROGRESS,
    build_status_message,
    pick_transition,
)


def _t(id, name, to_status, cat):
    return Transition(id=id, name=name, to_status=to_status, to_category=cat)


def test_pick_by_category_when_no_name_match():
    transitions = [
        _t("11", "Start Progress", "개발중", "indeterminate"),
        _t("31", "Done", "완료", "done"),
    ]
    picked = pick_transition(transitions, "indeterminate", [])
    assert picked.id == "11"


def test_pick_prefers_configured_name():
    transitions = [
        _t("11", "리뷰중으로", "리뷰중", "indeterminate"),
        _t("12", "개발중으로", "개발중", "indeterminate"),
    ]
    picked = pick_transition(transitions, "indeterminate", ["개발중"])
    assert picked.to_status == "개발중"


def test_pick_name_match_is_case_insensitive():
    transitions = [_t("11", "Start", "In Progress", "indeterminate")]
    picked = pick_transition(transitions, "indeterminate", ["in progress"])
    assert picked.id == "11"


def test_pick_returns_none_when_category_absent():
    transitions = [_t("31", "Done", "완료", "done")]
    assert pick_transition(transitions, "indeterminate", []) is None


def test_status_message_new_has_both_buttons():
    _, blocks = build_status_message(
        issue_key="CAM-1", issue_url="http://x/browse/CAM-1",
        status_name="할 일", category="new",
    )
    actions = [b for b in blocks if b["type"] == "actions"][0]
    ids = [e["action_id"] for e in actions["elements"]]
    assert ids == [AID_PROGRESS, AID_DONE]


def test_status_message_shows_summary_and_type():
    _, blocks = build_status_message(
        issue_key="CAM-1", issue_url="http://x/browse/CAM-1",
        status_name="할 일", category="new",
        summary="OAuth 구글 연동", issue_type="Story",
    )
    text = blocks[0]["text"]["text"]
    assert "`Story`" in text
    assert "OAuth 구글 연동" in text


def test_status_board_shows_summary_and_type():
    from app.services.slack.status_card import build_status_board

    tickets = [
        {"key": "CAM-1", "status_name": "할 일", "category": "new",
         "summary": "결제 버그", "issue_type": "Bug"},
    ]
    _, blocks = build_status_board(tickets, base_url="https://x.atlassian.net")
    section = next(b for b in blocks if b["type"] == "section")
    assert "`Bug`" in section["text"]["text"]
    assert "결제 버그" in section["text"]["text"]


def test_status_message_in_progress_only_done():
    _, blocks = build_status_message(
        issue_key="CAM-1", issue_url="http://x/browse/CAM-1",
        status_name="개발중", category="indeterminate",
    )
    actions = [b for b in blocks if b["type"] == "actions"][0]
    ids = [e["action_id"] for e in actions["elements"]]
    assert ids == [AID_DONE]


def test_status_board_per_ticket_and_bulk_buttons():
    from app.services.slack.status_card import (
        AID_BOARD_BULK_DONE,
        AID_BOARD_BULK_PROGRESS,
        AID_BOARD_DONE,
        AID_BOARD_PROGRESS,
        build_status_board,
        extract_board_keys,
    )

    tickets = [
        {"key": "CAM-1", "status_name": "할 일", "category": "new"},
        {"key": "CAM-2", "status_name": "개발중", "category": "indeterminate"},
        {"key": "CAM-3", "status_name": "완료", "category": "done"},
    ]
    _, blocks = build_status_board(tickets, base_url="https://x.atlassian.net")

    # Bulk buttons present
    bulk_ids = [
        e["action_id"]
        for b in blocks
        if b["type"] == "actions"
        for e in b["elements"]
        if e["action_id"].startswith("board_bulk")
    ]
    assert AID_BOARD_BULK_PROGRESS in bulk_ids
    assert AID_BOARD_BULK_DONE in bulk_ids

    # Done ticket has no per-ticket buttons; in-progress has only 완료
    per_ticket = [
        [e["action_id"] for e in b["elements"]]
        for b in blocks
        if b["type"] == "actions"
        and not any(e["action_id"].startswith("board_bulk") for e in b["elements"])
    ]
    assert [AID_BOARD_PROGRESS, AID_BOARD_DONE] in per_ticket  # CAM-1
    assert [AID_BOARD_DONE] in per_ticket  # CAM-2

    # Round-trip: keys recoverable from rendered board
    assert extract_board_keys(blocks) == ["CAM-1", "CAM-2", "CAM-3"]


def test_build_transition_modal_lists_candidates():
    from app.services.slack.status_card import build_transition_modal

    cands = [
        _t("11", "리뷰중", "리뷰중", "indeterminate"),
        _t("12", "개발중", "개발중", "indeterminate"),
    ]
    view = build_transition_modal(
        issue_key="CAM-1", channel="C1", message_ts="1.2", candidates=cands
    )
    assert view["callback_id"] == "jira_transition_select"
    import json as _json

    meta = _json.loads(view["private_metadata"])
    assert meta["issue_key"] == "CAM-1"
    opts = view["blocks"][0]["element"]["options"]
    assert [o["value"] for o in opts] == ["11", "12"]


def test_status_message_done_has_no_buttons():
    _, blocks = build_status_message(
        issue_key="CAM-1", issue_url="http://x/browse/CAM-1",
        status_name="완료", category="done",
    )
    assert all(b["type"] != "actions" for b in blocks)
