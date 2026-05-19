from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.errors import LLMError
from app.db.models import Base, StandupEntry, WorkItem
from app.handlers.extract import ExtractHandler
from app.services.atlassian.types import AtlassianUser, Team
from app.services.llm.base import LLMExtractor
from app.services.llm.schemas import (
    ExtractedWorkItem,
    ExtractionResult,
    TaskType,
)


# --- SQLite-backed session (UUIDs/JSONB are SQLAlchemy-typed so they work)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sm() as s:
        yield s
    await engine.dispose()


# --- Fakes


class FakeExtractor(LLMExtractor):
    def __init__(self, result: ExtractionResult | Exception):
        self.result = result
        self.calls = 0

    async def extract(self, message, context):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeTeams:
    def __init__(self, primary: Team | None, all_teams: list[Team] | None = None):
        self.primary = primary
        self.all_teams = all_teams if all_teams is not None else (
            [primary] if primary else []
        )

    async def get_user_primary_team(self, account_id):
        return self.primary

    async def list_teams(self):
        return self.all_teams


class FakeSearch:
    def __init__(self, user: AtlassianUser | None = None, epics: list | None = None):
        self.user = user
        self.epics = epics or []

    async def lookup_account_by_email(self, email):
        return self.user

    async def get_active_epics_for_team(self, project_key, team_id, limit=10):
        return self.epics


class FakeSlack:
    def __init__(self, email: str | None = "user@example.com"):
        self.email = email
        self.posted: list[dict[str, Any]] = []

    async def get_user_email(self, slack_user_id):
        return self.email

    async def post_message(self, **kwargs):
        self.posted.append(kwargs)
        return {"ok": True}


# --- Tests


@pytest.mark.asyncio
async def test_handler_stores_entry_and_workitems_and_posts_card(session):
    team = Team(id="ari:team/abc", name="Backend")
    extractor = FakeExtractor(
        ExtractionResult(
            items=[
                ExtractedWorkItem(
                    task_content="OAuth 마무리",
                    task_type=TaskType.FEATURE,
                    parent_issue_hint="CATCHUP-42",
                    parent_feature="로그인",
                ),
                ExtractedWorkItem(task_content="결제 QA"),
            ]
        )
    )
    slack = FakeSlack()
    handler = ExtractHandler(
        teams_svc=FakeTeams(team),
        search_svc=FakeSearch(
            user=AtlassianUser(account_id="acc-1", email="user@example.com")
        ),
        extractor=extractor,
        slack=slack,
        team_to_project_map={team.id: "CATCHUP"},
    )

    await handler.handle_message(
        session,
        channel_id="C1",
        message_ts="1700000000.000100",
        author_slack_id="U1",
        text="OAuth 마무리하고 결제 QA",
        posted_at=datetime.now(tz=timezone.utc),
    )

    entry = await session.scalar(select(StandupEntry))
    assert entry is not None
    assert entry.extraction_status == "completed"

    items = (
        await session.scalars(select(WorkItem).order_by(WorkItem.sequence_no))
    ).all()
    assert len(items) == 2
    assert items[0].task_content == "OAuth 마무리"
    assert items[0].task_type == "Feature"
    assert items[0].parent_issue_key == "CATCHUP-42"
    assert items[0].jira_team_id == team.id
    assert items[0].jira_project_key == "CATCHUP"
    # Second item has no hint, no key
    assert items[1].parent_issue_key is None

    assert len(slack.posted) == 1
    assert slack.posted[0]["thread_ts"] == "1700000000.000100"


@pytest.mark.asyncio
async def test_handler_is_idempotent(session):
    handler = ExtractHandler(
        teams_svc=FakeTeams(None),
        search_svc=FakeSearch(),
        extractor=FakeExtractor(
            ExtractionResult(items=[ExtractedWorkItem(task_content="a")])
        ),
        slack=FakeSlack(),
        team_to_project_map={},
    )
    kwargs = dict(
        channel_id="C1",
        message_ts="1.1",
        author_slack_id="U1",
        text="hello",
        posted_at=datetime.now(tz=timezone.utc),
    )
    await handler.handle_message(session, **kwargs)
    await handler.handle_message(session, **kwargs)

    entries = (await session.scalars(select(StandupEntry))).all()
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_handler_falls_back_when_llm_fails(session):
    handler = ExtractHandler(
        teams_svc=FakeTeams(None),
        search_svc=FakeSearch(),
        extractor=FakeExtractor(LLMError("boom")),
        slack=FakeSlack(),
        team_to_project_map={},
    )

    await handler.handle_message(
        session,
        channel_id="C1",
        message_ts="2.2",
        author_slack_id="U1",
        text="모호한 메시지",
        posted_at=datetime.now(tz=timezone.utc),
    )

    entry = await session.scalar(select(StandupEntry))
    assert entry.extraction_status == "completed"
    assert "boom" in (entry.extraction_error or "")

    items = (await session.scalars(select(WorkItem))).all()
    assert len(items) == 1
    assert items[0].task_content == "모호한 메시지"


@pytest.mark.asyncio
async def test_handler_falls_back_on_unexpected_extractor_exception(session):
    # Non-LLMError (e.g. a transport-level RuntimeError that escaped wrapping)
    # should still produce a usable preview card.
    slack = FakeSlack()
    handler = ExtractHandler(
        teams_svc=FakeTeams(None),
        search_svc=FakeSearch(),
        extractor=FakeExtractor(RuntimeError("transport closed")),
        slack=slack,
        team_to_project_map={},
    )

    await handler.handle_message(
        session,
        channel_id="C1",
        message_ts="3.3",
        author_slack_id="U1",
        text="OAuth 구글 연동 마무리",
        posted_at=datetime.now(tz=timezone.utc),
    )

    entry = await session.scalar(select(StandupEntry))
    assert entry.extraction_status == "completed"
    assert "RuntimeError" in (entry.extraction_error or "")

    items = (await session.scalars(select(WorkItem))).all()
    assert len(items) == 1
    assert items[0].task_content == "OAuth 구글 연동 마무리"

    # Preview card must still be posted so the user can publish manually.
    assert len(slack.posted) == 1
    assert slack.posted[0]["thread_ts"] == "3.3"
