from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from app.services.atlassian.jira_search import JiraSearchService

_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")


def is_issue_key(value: str | None) -> bool:
    return bool(value) and bool(_ISSUE_KEY_RE.match(value or ""))


class ParentCandidate(BaseModel):
    key: str
    summary: str
    source: Literal["llm_direct", "llm_search", "recent_activity", "live_search"]


class ParentTicketResolver:
    """Resolve parent-ticket candidates for a work item.

    Priority:
      1. Direct LLM hint that matches a real Jira key
      2. Text search using the LLM hint
      3. Author's recent activity (assignee or commenter)
    """

    def __init__(self, search: JiraSearchService, max_candidates: int = 5):
        self.search = search
        self.max_candidates = max_candidates

    async def resolve_candidates(
        self,
        *,
        project_key: str | None,
        hint: str | None,
        author_account_id: str | None,
    ) -> list[ParentCandidate]:
        candidates: list[ParentCandidate] = []
        seen: set[str] = set()

        if hint and is_issue_key(hint):
            try:
                issue = await self.search.get_issue(hint)
            except Exception:
                issue = None
            if issue:
                candidates.append(
                    ParentCandidate(
                        key=issue.key, summary=issue.summary, source="llm_direct"
                    )
                )
                seen.add(issue.key)

        if hint and not is_issue_key(hint) and project_key:
            try:
                results = await self.search.search_parent_candidates(
                    project_key, hint, limit=self.max_candidates
                )
            except Exception:
                results = []
            for r in results:
                if r.key in seen:
                    continue
                candidates.append(
                    ParentCandidate(key=r.key, summary=r.summary, source="llm_search")
                )
                seen.add(r.key)
                if len(candidates) >= self.max_candidates:
                    break

        if len(candidates) < self.max_candidates and author_account_id:
            try:
                recent = await self.search.get_user_recent_activity(
                    author_account_id, days=14, limit=10
                )
            except Exception:
                recent = []
            for r in recent:
                if r.key in seen:
                    continue
                candidates.append(
                    ParentCandidate(
                        key=r.key, summary=r.summary, source="recent_activity"
                    )
                )
                seen.add(r.key)
                if len(candidates) >= self.max_candidates:
                    break

        return candidates

    async def live_search(
        self,
        *,
        project_key: str | None,
        query: str,
        limit: int = 10,
    ) -> list[ParentCandidate]:
        if not query:
            return []
        if is_issue_key(query):
            try:
                issue = await self.search.get_issue(query)
            except Exception:
                issue = None
            if issue:
                return [
                    ParentCandidate(
                        key=issue.key, summary=issue.summary, source="live_search"
                    )
                ]
        if not project_key:
            return []
        try:
            results = await self.search.search_parent_candidates(
                project_key, query, limit=limit
            )
        except Exception:
            return []
        return [
            ParentCandidate(key=r.key, summary=r.summary, source="live_search")
            for r in results
        ]
