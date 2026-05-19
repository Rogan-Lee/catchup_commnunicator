from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-secret")
    monkeypatch.setenv("SLACK_STANDUP_CHANNELS", "C123")
    get_settings.cache_clear()
    app = create_app()
    return TestClient(app)


def _headers(body: bytes, secret: str = "test-secret") -> dict[str, str]:
    ts = str(int(time.time()))
    digest = hmac.new(
        secret.encode("utf-8"),
        b"v0:" + ts.encode("utf-8") + b":" + body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": f"v0={digest}",
        "Content-Type": "application/json",
    }


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_url_verification(client):
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    r = client.post("/slack/events", content=body, headers=_headers(body))
    assert r.status_code == 200
    assert r.text == "abc123"


def test_event_callback_accepts_valid(client):
    body = json.dumps(
        {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C123",
                "user": "U1",
                "text": "오늘 할 일",
                "ts": "1700000000.000100",
            },
        }
    ).encode()
    r = client.post("/slack/events", content=body, headers=_headers(body))
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_rejects_bad_signature(client):
    body = json.dumps({"type": "url_verification", "challenge": "x"}).encode()
    headers = _headers(body)
    headers["X-Slack-Signature"] = "v0=deadbeef"
    r = client.post("/slack/events", content=body, headers=headers)
    assert r.status_code == 401
