from __future__ import annotations

import pytest

from app.services.atlassian.parent_resolver import ParentTicketResolver
from app.services.atlassian.types import Issue


class FakeSearch:
    def __init__(
        self,
        *,
        by_key: dict[str, Issue] | None = None,
        text_results: list[Issue] | None = None,
        recent: list[Issue] | None = None,
    ):
        self.by_key = by_key or {}
        self.text_results = text_results or []
        self.recent = recent or []

    async def get_issue(self, key):
        return self.by_key.get(key)

    async def search_parent_candidates(self, project_key, query, limit=10):
        return self.text_results[:limit]

    async def get_user_recent_activity(self, account_id, days=14, limit=10):
        return self.recent[:limit]


@pytest.mark.asyncio
async def test_direct_key_hint_takes_priority():
    issue = Issue(key="CATCHUP-42", summary="OAuth 통합")
    resolver = ParentTicketResolver(
        FakeSearch(
            by_key={"CATCHUP-42": issue},
            recent=[Issue(key="CATCHUP-99", summary="other")],
        )
    )
    cands = await resolver.resolve_candidates(
        project_key="CATCHUP",
        hint="CATCHUP-42",
        author_account_id="acc-1",
    )
    assert cands[0].key == "CATCHUP-42"
    assert cands[0].source == "llm_direct"


@pytest.mark.asyncio
async def test_text_hint_uses_search():
    resolver = ParentTicketResolver(
        FakeSearch(
            text_results=[
                Issue(key="CATCHUP-10", summary="로그인 개선"),
                Issue(key="CATCHUP-11", summary="로그인 리팩토링"),
            ]
        )
    )
    cands = await resolver.resolve_candidates(
        project_key="CATCHUP",
        hint="로그인",
        author_account_id=None,
    )
    assert [c.key for c in cands] == ["CATCHUP-10", "CATCHUP-11"]
    assert all(c.source == "llm_search" for c in cands)


@pytest.mark.asyncio
async def test_falls_back_to_recent_activity():
    resolver = ParentTicketResolver(
        FakeSearch(
            recent=[Issue(key="X-1", summary="최근 작업")],
        )
    )
    cands = await resolver.resolve_candidates(
        project_key="X", hint=None, author_account_id="acc"
    )
    assert cands[0].key == "X-1"
    assert cands[0].source == "recent_activity"


@pytest.mark.asyncio
async def test_dedupes_across_sources():
    resolver = ParentTicketResolver(
        FakeSearch(
            text_results=[Issue(key="X-1", summary="shared")],
            recent=[Issue(key="X-1", summary="shared")],
        )
    )
    cands = await resolver.resolve_candidates(
        project_key="X", hint="shared", author_account_id="acc"
    )
    assert [c.key for c in cands] == ["X-1"]


@pytest.mark.asyncio
async def test_live_search_handles_issue_key_directly():
    resolver = ParentTicketResolver(
        FakeSearch(by_key={"X-9": Issue(key="X-9", summary="direct")})
    )
    cands = await resolver.live_search(project_key=None, query="X-9")
    assert cands[0].key == "X-9"


@pytest.mark.asyncio
async def test_live_search_empty_query():
    resolver = ParentTicketResolver(FakeSearch())
    assert await resolver.live_search(project_key="X", query="") == []
