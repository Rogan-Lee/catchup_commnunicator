from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import get_container
from app.main import create_app
from app.routers import standup_ingest as ingest_module


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-secret")
    monkeypatch.setenv("SLACK_STANDUP_CHANNELS", "C123")
    monkeypatch.setenv("STANDUP_INGEST_TOKEN", "ingest-secret")
    get_settings.cache_clear()
    get_container.cache_clear()
    app = create_app()
    return TestClient(app)


def _payload(**overrides) -> dict:
    base = {
        "modal_type": "todo",
        "slack_user_id": "U1",
        "channel_id": "C123",
        "message_ts": "1700000000.000100",
        "text": "OAuth 통합 마무리",
    }
    base.update(overrides)
    return base


def test_ingest_rejects_missing_bearer(client):
    r = client.post("/standup/ingest", json=_payload())
    assert r.status_code == 401


def test_ingest_rejects_wrong_bearer(client):
    r = client.post(
        "/standup/ingest",
        json=_payload(),
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


def test_ingest_rejects_when_token_not_configured(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-secret")
    monkeypatch.setenv("STANDUP_INGEST_TOKEN", "")
    get_settings.cache_clear()
    get_container.cache_clear()
    app = create_app()
    c = TestClient(app)
    r = c.post(
        "/standup/ingest",
        json=_payload(),
        headers={"Authorization": "Bearer anything"},
    )
    assert r.status_code == 503


def test_ingest_accepts_and_dispatches(client, monkeypatch):
    seen: list = []

    async def fake_process(payload):
        seen.append(payload)

    monkeypatch.setattr(ingest_module, "_process", fake_process)

    r = client.post(
        "/standup/ingest",
        json=_payload(blocker="SDK 응답 지연"),
        headers={"Authorization": "Bearer ingest-secret"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert len(seen) == 1
    assert seen[0].text == "OAuth 통합 마무리"
    assert seen[0].blocker == "SDK 응답 지연"


def test_ingest_rejects_when_neither_items_nor_text(client):
    # Neither items nor text provided
    r = client.post(
        "/standup/ingest",
        json={
            "modal_type": "todo",
            "slack_user_id": "U1",
            "channel_id": "C123",
            "message_ts": "1.2",
        },
        headers={"Authorization": "Bearer ingest-secret"},
    )
    assert r.status_code == 422


def test_ingest_accepts_structured_items(client, monkeypatch):
    seen: list = []

    async def fake_process(payload):
        seen.append(payload)

    monkeypatch.setattr(ingest_module, "_process", fake_process)

    r = client.post(
        "/standup/ingest",
        json={
            "modal_type": "todo",
            "slack_user_id": "U1",
            "channel_id": "C123",
            "message_ts": "1700000000.000100",
            "items": [
                {"task_content": "OAuth 연동", "task_type": "기능", "parent_feature": "로그인"},
                {"task_content": "결제 버그 수정", "task_type": "버그"},
            ],
        },
        headers={"Authorization": "Bearer ingest-secret"},
    )
    assert r.status_code == 200
    assert len(seen) == 1
    assert len(seen[0].items) == 2
    assert seen[0].items[0].parent_feature == "로그인"
