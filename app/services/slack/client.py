from __future__ import annotations

from typing import Any

from slack_sdk.web.async_client import AsyncWebClient

from app.core.logging import get_logger

log = get_logger(__name__)


class SlackClient:
    """Slim wrapper around slack_sdk's AsyncWebClient.

    Centralizes error logging and adds the few helpers our handlers need.
    """

    def __init__(self, bot_token: str):
        self.web = AsyncWebClient(token=bot_token)

    async def post_message(
        self,
        *,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        if blocks:
            kwargs["blocks"] = blocks
        response = await self.web.chat_postMessage(**kwargs)
        return response.data  # type: ignore[return-value]

    async def get_user_email(self, slack_user_id: str) -> str | None:
        try:
            response = await self.web.users_info(user=slack_user_id)
        except Exception as e:
            log.warning("slack.users_info.failed", user=slack_user_id, error=str(e))
            return None
        profile = (response.data or {}).get("user", {}).get("profile", {})
        return profile.get("email")
