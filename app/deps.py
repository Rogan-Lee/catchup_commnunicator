from __future__ import annotations

from functools import lru_cache

from app.config import Settings, get_settings
from app.handlers.extract import ExtractHandler
from app.services.atlassian.http import build_jira_client, build_teams_client
from app.services.atlassian.jira_search import JiraSearchService
from app.services.atlassian.teams import AtlassianTeamsService
from app.services.cache import CacheLayer, build_redis_client
from app.services.llm.base import LLMExtractor
from app.services.llm.gemini import GeminiExtractor
from app.services.slack.client import SlackClient


class Container:
    """Holds long-lived clients and services for the running app.

    Singletons here are intentional: HTTP / Redis clients should be reused.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._redis = build_redis_client(settings.redis_url)
        self.cache = CacheLayer(self._redis, default_ttl=settings.redis_default_ttl)

        self.jira_http = build_jira_client(
            settings.atlassian_base_url,
            settings.atlassian_email,
            settings.atlassian_api_token,
        )
        self.teams_http = build_teams_client(
            settings.atlassian_email,
            settings.atlassian_api_token,
        )

        self.teams_svc = AtlassianTeamsService(self.teams_http, cache=self.cache)
        self.search_svc = JiraSearchService(
            self.jira_http, team_field_id=settings.atlassian_team_field_id
        )

        self.slack = SlackClient(settings.slack_bot_token)
        self.extractor: LLMExtractor = _build_extractor(settings)

        self.extract_handler = ExtractHandler(
            teams_svc=self.teams_svc,
            search_svc=self.search_svc,
            extractor=self.extractor,
            slack=self.slack,
            team_to_project_map=settings.team_to_project_map,
            publish_enabled=settings.enable_auto_publish,
        )

    async def close(self) -> None:
        await self.jira_http.aclose()
        await self.teams_http.aclose()
        try:
            await self._redis.aclose()
        except Exception:
            pass


def _build_extractor(settings: Settings) -> LLMExtractor:
    if not settings.enable_llm_extraction or not settings.gemini_api_key:
        return _NullExtractor()
    try:
        from google import genai  # type: ignore
    except Exception:
        return _NullExtractor()
    client = genai.Client(api_key=settings.gemini_api_key)
    return GeminiExtractor(client=client, model=settings.gemini_model)


class _NullExtractor(LLMExtractor):
    """Used when LLM is disabled or unavailable; always raises so the
    handler falls back to a single task_content slot."""

    async def extract(self, message, context):  # type: ignore[override]
        from app.core.errors import LLMError

        raise LLMError("LLM extraction disabled")


@lru_cache
def get_container() -> Container:
    return Container(get_settings())
