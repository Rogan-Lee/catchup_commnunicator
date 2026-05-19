from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import get_settings
from app.core.errors import SlackVerificationError
from app.core.logging import get_logger
from app.services.slack.verify import verify_slack_signature

router = APIRouter(prefix="/slack", tags=["slack"])
log = get_logger(__name__)


@router.post("/events")
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_signature: str | None = Header(default=None),
    x_slack_request_timestamp: str | None = Header(default=None),
) -> Any:
    settings = get_settings()
    body = await request.body()

    try:
        verify_slack_signature(
            signing_secret=settings.slack_signing_secret,
            timestamp=x_slack_request_timestamp,
            signature=x_slack_signature,
            body=body,
        )
    except SlackVerificationError as e:
        log.warning("slack.verify.failed", error=str(e))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature") from e

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid json")

    event_type = payload.get("type")

    # 1. URL verification handshake (Slack app registration)
    if event_type == "url_verification":
        return PlainTextResponse(payload.get("challenge", ""))

    # 2. Event callback
    if event_type == "event_callback":
        event = payload.get("event") or {}
        if _should_process_message(event, settings.standup_channel_ids):
            background_tasks.add_task(
                _enqueue_message,
                channel_id=event["channel"],
                message_ts=event["ts"],
                author_slack_id=event.get("user", ""),
                text=event.get("text", ""),
                event_ts=event.get("event_ts"),
            )
        else:
            log.debug(
                "slack.event.skipped",
                subtype=event.get("subtype"),
                channel=event.get("channel"),
                bot_id=event.get("bot_id"),
                thread_ts=event.get("thread_ts"),
            )
        # Always 200 OK quickly; processing is deferred.
        return JSONResponse({"ok": True})

    log.info("slack.event.unhandled", type=event_type)
    return JSONResponse({"ok": True})


def _should_process_message(event: dict[str, Any], allowed_channels: set[str]) -> bool:
    """Filter to top-level user messages in configured standup channels."""
    if event.get("type") != "message":
        return False
    # Bot and app messages
    if event.get("bot_id") or event.get("subtype") in {
        "bot_message",
        "message_changed",
        "message_deleted",
        "channel_join",
        "channel_leave",
    }:
        return False
    # Threaded reply (we only react to top-level standup posts)
    if event.get("thread_ts") and event.get("thread_ts") != event.get("ts"):
        return False
    if allowed_channels and event.get("channel") not in allowed_channels:
        return False
    if not event.get("user"):
        return False
    if not event.get("text"):
        return False
    return True


async def _enqueue_message(
    *,
    channel_id: str,
    message_ts: str,
    author_slack_id: str,
    text: str,
    event_ts: str | None,
) -> None:
    """Placeholder for the extraction pipeline.

    Wired up to ExtractHandler in the next slice. For now we just log so we can
    confirm the event arrives end-to-end.
    """
    log.info(
        "slack.message.received",
        channel=channel_id,
        ts=message_ts,
        author=author_slack_id,
        text_len=len(text),
        event_ts=event_ts,
    )
