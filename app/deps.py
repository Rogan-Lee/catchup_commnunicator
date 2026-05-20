from __future__ import annotations

from functools import lru_cache

from app.config import Settings, get_settings
from app.handlers.extract import ExtractHandler
from app.handlers.publish import PublishHandler
from app.services.atlassian.http import build_jira_client, build_teams_client
from app.services.atlassian.jira_issues import JiraIssueService
from app.services.atlassian.jira_search import JiraSearchService
from app.services.atlassian.parent_resolver import ParentTicketResolver
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
        self.issue_svc = JiraIssueService(
            self.jira_http, team_field_id=settings.atlassian_team_field_id or None
        )
        self.parent_resolver = ParentTicketResolver(self.search_svc)

        self.slack = SlackClient(settings.slack_bot_token)
        self.extractor: LLMExtractor = _build_extractor(settings)

        self.extract_handler = ExtractHandler(
            teams_svc=self.teams_svc,
            search_svc=self.search_svc,
            extractor=self.extractor,
            slack=self.slack,
            team_to_project_map=settings.team_to_project_map,
            publish_enabled=True,
            parent_resolver=self.parent_resolver,
        )
        self.publish_handler = PublishHandler(
            jira_svc=self.issue_svc,
            search_svc=self.search_svc,
            slack=self.slack,
        )

    async def close(self) -> None:
        await self.jira_http.aclose()
        await self.teams_http.aclose()
        try:
            await self._redis.aclose()
        except Exception:
            pass


def _build_extractor(settings: Settings) -> LLMExtractor:
    from app.services.llm.rule_based import RuleBasedExtractor

    # Default to the free, deterministic bullet parser. Only use Gemini when
    # explicitly enabled and configured.
    if settings.extraction_mode != "gemini" or not settings.enable_llm_extraction:
        return RuleBasedExtractor()
    if not settings.gemini_api_key:
        return RuleBasedExtractor()
    try:
        from google import genai  # type: ignore
    except Exception:
        return RuleBasedExtractor()
    client = genai.Client(api_key=settings.gemini_api_key)
    return GeminiExtractor(client=client, model=settings.gemini_model)


@lru_cache
def get_container() -> Container:
    return Container(get_settings())
