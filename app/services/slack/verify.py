from __future__ import annotations

import hashlib
import hmac
import time

from app.core.errors import SlackVerificationError

# Slack recommends rejecting requests older than 5 minutes to prevent replay.
_MAX_SKEW_SECONDS = 60 * 5


def verify_slack_signature(
    *,
    signing_secret: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: float | None = None,
) -> None:
    """Verify a Slack request signature.

    Raises SlackVerificationError on any mismatch.
    """
    if not signing_secret:
        raise SlackVerificationError("signing secret not configured")
    if not timestamp or not signature:
        raise SlackVerificationError("missing signature headers")

    try:
        ts_int = int(timestamp)
    except ValueError as e:
        raise SlackVerificationError("invalid timestamp") from e

    current = now if now is not None else time.time()
    if abs(current - ts_int) > _MAX_SKEW_SECONDS:
        raise SlackVerificationError("request timestamp out of range")

    basestring = b"v0:" + timestamp.encode("utf-8") + b":" + body
    digest = hmac.new(
        signing_secret.encode("utf-8"),
        basestring,
        hashlib.sha256,
    ).hexdigest()
    expected = f"v0={digest}"

    if not hmac.compare_digest(expected, signature):
        raise SlackVerificationError("signature mismatch")
