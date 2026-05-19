from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from app.core.errors import SlackVerificationError
from app.services.slack.verify import verify_slack_signature


def _sign(secret: str, ts: str, body: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        b"v0:" + ts.encode("utf-8") + b":" + body,
        hashlib.sha256,
    ).hexdigest()
    return f"v0={digest}"


def test_verify_accepts_valid_signature():
    secret = "shhh"
    ts = str(int(time.time()))
    body = b'{"hello":"world"}'
    sig = _sign(secret, ts, body)

    verify_slack_signature(
        signing_secret=secret,
        timestamp=ts,
        signature=sig,
        body=body,
    )


def test_verify_rejects_bad_signature():
    secret = "shhh"
    ts = str(int(time.time()))
    body = b'{"hello":"world"}'

    with pytest.raises(SlackVerificationError):
        verify_slack_signature(
            signing_secret=secret,
            timestamp=ts,
            signature="v0=deadbeef",
            body=body,
        )


def test_verify_rejects_old_timestamp():
    secret = "shhh"
    ts = str(int(time.time()) - 3600)
    body = b'{}'
    sig = _sign(secret, ts, body)

    with pytest.raises(SlackVerificationError):
        verify_slack_signature(
            signing_secret=secret,
            timestamp=ts,
            signature=sig,
            body=body,
        )


def test_verify_requires_headers():
    with pytest.raises(SlackVerificationError):
        verify_slack_signature(
            signing_secret="x",
            timestamp=None,
            signature=None,
            body=b"",
        )


def test_verify_requires_secret():
    with pytest.raises(SlackVerificationError):
        verify_slack_signature(
            signing_secret="",
            timestamp="1",
            signature="v0=x",
            body=b"",
        )
