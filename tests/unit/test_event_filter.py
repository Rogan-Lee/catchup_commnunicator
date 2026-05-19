from __future__ import annotations

from app.routers.slack_events import _should_process_message

ALLOWED = {"C123"}


def _msg(**overrides):
    base = {
        "type": "message",
        "channel": "C123",
        "user": "U1",
        "text": "오늘 OAuth 마무리",
        "ts": "1700000000.000100",
    }
    base.update(overrides)
    return base


def test_accept_top_level_user_message():
    assert _should_process_message(_msg(), ALLOWED) is True


def test_reject_other_channel():
    assert _should_process_message(_msg(channel="C999"), ALLOWED) is False


def test_reject_bot_message():
    assert _should_process_message(_msg(bot_id="B1"), ALLOWED) is False


def test_reject_subtype_edit():
    assert _should_process_message(_msg(subtype="message_changed"), ALLOWED) is False


def test_reject_thread_reply():
    assert (
        _should_process_message(
            _msg(thread_ts="1699999999.000100"), ALLOWED
        )
        is False
    )


def test_accept_top_level_with_self_thread_ts():
    # Slack sets thread_ts == ts for messages that have replies but are themselves top-level.
    ts = "1700000000.000100"
    assert _should_process_message(_msg(thread_ts=ts, ts=ts), ALLOWED) is True


def test_reject_empty_text():
    assert _should_process_message(_msg(text=""), ALLOWED) is False


def test_reject_non_message_event():
    assert _should_process_message({"type": "reaction_added"}, ALLOWED) is False
