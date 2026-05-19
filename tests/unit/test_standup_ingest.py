from __future__ import annotations

from app.routers.standup_ingest import IngestPayload, _compose_text, _valid_bearer


def test_valid_bearer_accepts_matching_token():
    assert _valid_bearer("Bearer abc123", "abc123") is True


def test_valid_bearer_rejects_wrong_token():
    assert _valid_bearer("Bearer wrong", "abc123") is False


def test_valid_bearer_rejects_missing_or_malformed_header():
    assert _valid_bearer(None, "abc123") is False
    assert _valid_bearer("abc123", "abc123") is False
    assert _valid_bearer("Token abc123", "abc123") is False
    assert _valid_bearer("Bearer ", "abc123") is False


def test_compose_text_todo_only():
    payload = IngestPayload(
        modal_type="todo",
        slack_user_id="U1",
        channel_id="C1",
        message_ts="1.2",
        text="OAuth 통합 마무리",
    )
    assert _compose_text(payload) == "[오늘 할 일]\nOAuth 통합 마무리"


def test_compose_text_with_blocker_and_yesterday():
    payload = IngestPayload(
        modal_type="standup",
        slack_user_id="U1",
        channel_id="C1",
        message_ts="1.2",
        text="결제 모듈 리팩토링",
        blocker="SDK 응답 지연",
        yesterday="로그인 버그 수정",
    )
    composed = _compose_text(payload)
    assert "[어제]\n로그인 버그 수정" in composed
    assert "[오늘 할 일]\n결제 모듈 리팩토링" in composed
    assert "[차단 요소]\nSDK 응답 지연" in composed
    # Section order: yesterday → today → blocker
    assert composed.index("[어제]") < composed.index("[오늘 할 일]") < composed.index("[차단 요소]")


def test_compose_text_strips_whitespace_only_optionals():
    payload = IngestPayload(
        modal_type="todo",
        slack_user_id="U1",
        channel_id="C1",
        message_ts="1.2",
        text="할 일",
        blocker="   ",
        yesterday="\n\n",
    )
    assert _compose_text(payload) == "[오늘 할 일]\n할 일"
