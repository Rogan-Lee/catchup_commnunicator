from __future__ import annotations

import httpx

from app.core.logging import get_logger
from app.services.atlassian.types import AtlassianUser, Issue

log = get_logger(__name__)


class JiraSearchService:
    def __init__(self, http: httpx.AsyncClient, team_field_id: str):
        self.http = http
        self.team_field_id = team_field_id

    async def lookup_account_by_email(self, email: str) -> AtlassianUser | None:
        resp = await self.http.get(
            "/rest/api/3/user/search",
            params={"query": email},
        )
        resp.raise_for_status()
        users = resp.json() or []
        if not users:
            return None
        return AtlassianUser.from_api(users[0])

    async def get_active_epics_for_team(
        self,
        project_key: str,
        team_id: str,
        limit: int = 10,
    ) -> list[Issue]:
        jql = (
            f'project = "{_escape(project_key)}" '
            f"AND issuetype = Epic "
            f"AND statusCategory != Done "
            f'AND "Team[Team]" = "{_escape(team_id)}" '
            f"ORDER BY updated DESC"
        )
        return await self._search(jql, limit, fields=["summary", "status"])

    async def search_parent_candidates(
        self,
        project_key: str,
        query: str,
        limit: int = 10,
    ) -> list[Issue]:
        safe_query = _escape(query)
        jql = (
            f'project = "{_escape(project_key)}" '
            f'AND (summary ~ "{safe_query}*" OR text ~ "{safe_query}") '
            f"AND issuetype in (Epic, Story) "
            f"AND statusCategory != Done "
            f"ORDER BY updated DESC"
        )
        return await self._search(
            jql, limit, fields=["summary", "status", "issuetype"]
        )

    async def get_user_recent_activity(
        self,
        account_id: str,
        days: int = 14,
        limit: int = 10,
    ) -> list[Issue]:
        jql = (
            f'(assignee = "{_escape(account_id)}" '
            f'OR comment[user] = "{_escape(account_id)}") '
            f"AND updated >= -{int(days)}d "
            f"AND statusCategory != Done "
            f"ORDER BY updated DESC"
        )
        return await self._search(
            jql, limit, fields=["summary", "status", "issuetype", "project"]
        )

    async def get_issue(self, key: str) -> Issue | None:
        resp = await self.http.get(
            f"/rest/api/3/issue/{key}",
            params={"fields": "summary,status,issuetype,project"},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return Issue.from_api(resp.json())

    async def _search(self, jql: str, limit: int, fields: list[str]) -> list[Issue]:
        # /rest/api/3/search was removed in 2025; /search/jql is the replacement.
        # Response still exposes `issues`; pagination is token-based (unused here).
        resp = await self.http.get(
            "/rest/api/3/search/jql",
            params={
                "jql": jql,
                "maxResults": limit,
                "fields": ",".join(fields),
            },
        )
        resp.raise_for_status()
        return [Issue.from_api(i) for i in resp.json().get("issues", [])]


def _escape(value: str) -> str:
    """Minimal JQL string escaping. Surround with double quotes at call site."""
    return value.replace("\\", "\\\\").replace('"', '\\"')
