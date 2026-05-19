from __future__ import annotations

import httpx

from app.core.logging import get_logger
from app.services.atlassian.types import Team, TeamMember
from app.services.cache import CacheLayer

log = get_logger(__name__)

_TEAMS_LIST_PATH = "/gateway/api/public/teams/v1/teams"
_TEAM_MEMBERS_PATH = "/gateway/api/public/teams/v1/teams/{team_id}/members"


class AtlassianTeamsService:
    def __init__(
        self,
        http: httpx.AsyncClient,
        cache: CacheLayer | None = None,
        teams_ttl: int = 3600,
    ):
        self.http = http
        self.cache = cache
        self.teams_ttl = teams_ttl

    async def list_teams(self) -> list[Team]:
        if self.cache:
            cached = await self.cache.get("teams:all")
            if cached:
                return [Team.model_validate(t) for t in cached]

        resp = await self.http.get(_TEAMS_LIST_PATH)
        resp.raise_for_status()
        entities = resp.json().get("entities", [])
        teams = [Team.from_api(e) for e in entities]

        if self.cache:
            await self.cache.set(
                "teams:all", [t.model_dump() for t in teams], ttl=self.teams_ttl
            )
        return teams

    async def get_team_members(self, team_id: str) -> list[TeamMember]:
        cache_key = f"team_members:{team_id}"
        if self.cache:
            cached = await self.cache.get(cache_key)
            if cached:
                return [TeamMember.model_validate(m) for m in cached]

        resp = await self.http.get(_TEAM_MEMBERS_PATH.format(team_id=team_id))
        resp.raise_for_status()
        results = resp.json().get("results", [])
        members = [TeamMember.from_api(m) for m in results]

        if self.cache:
            await self.cache.set(
                cache_key, [m.model_dump() for m in members], ttl=self.teams_ttl
            )
        return members

    async def get_user_primary_team(self, account_id: str) -> Team | None:
        cache_key = f"user_team:{account_id}"
        if self.cache:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                return Team.model_validate(cached) if cached else None

        teams = await self.list_teams()
        for team in teams:
            try:
                members = await self.get_team_members(team.id)
            except httpx.HTTPError as e:
                log.warning("teams.members.failed", team_id=team.id, error=str(e))
                continue
            if any(m.account_id == account_id for m in members):
                if self.cache:
                    await self.cache.set(
                        cache_key, team.model_dump(), ttl=self.teams_ttl
                    )
                return team

        if self.cache:
            await self.cache.set(cache_key, None, ttl=self.teams_ttl)
        return None
