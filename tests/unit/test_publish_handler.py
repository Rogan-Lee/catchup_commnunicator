from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.errors import JiraError
from app.db.models import Base, StandupEntry, WorkItem
from app.handlers.publish import PublishHandler
from app.services.atlassian.jira_issues import CreatedIssue


@pytest_asyncio.fixture
async def session():
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


@pytest_asyncio.fixture
async def work_item(session):
    entry = StandupEntry(
        channel_id="C1",
        slack_message_ts="1700000000.000100",
        author_slack_id="U1",
        raw_text="OAuth 마무리",
        posted_at=datetime.now(tz=timezone.utc),
        extraction_status="completed",
    )
    session.add(entry)
    await session.flush()
    wi = WorkItem(
        standup_entry_id=entry.id,
        sequence_no=1,
        extracted_slots={"task_content": "OAuth"},
        task_content="OAuth",
        task_type="Feature",
        jira_team_id="ari:team/abc",
        jira_project_key="CATCHUP",
        status="pending",
    )
    session.add(wi)
    await session.commit()
    return wi


class FakeJira:
    def __init__(self, *, fail: Exception | None = None):
        self.fail = fail
        self.calls: list[Any] = []

    async def create_issue(self, payload):
        self.calls.append(payload)
        if self.fail:
            raise self.fail
        return CreatedIssue(
            key="CATCHUP-1",
            id="10001",
            url="https://x.atlassian.net/browse/CATCHUP-1",
        )


class FakeSearch:
    async def lookup_account_by_email(self, email):
        return None


class FakeSlack:
    def __init__(self):
        self.posted: list[dict[str, Any]] = []
        self.email = None

    async def get_user_email(self, slack_user_id):
        return self.email

    async def post_message(self, **kwargs):
        self.posted.append(kwargs)
        return {"ok": True}


@pytest.mark.asyncio
async def test_publish_success_creates_issue_and_replies(session, work_item):
    jira = FakeJira()
    slack = FakeSlack()
    handler = PublishHandler(jira_svc=jira, search_svc=FakeSearch(), slack=slack)

    created = await handler.publish(
        session,
        work_item_id=work_item.id,
        confirmed_slots={
            "team_id": "ari:team/abc",
            "project_key": "CATCHUP",
            "task_type": "Feature",
            "parent_feature": "로그인",
            "task_content": "OAuth 통합",
            "parent_issue_key": "CATCHUP-42",
        },
    )

    assert created.key == "CATCHUP-1"
    await session.refresh(work_item)
    assert work_item.status == "created"
    assert work_item.jira_issue_key == "CATCHUP-1"
    assert work_item.parent_issue_key == "CATCHUP-42"
    assert work_item.confirmed_slots["task_content"] == "OAuth 통합"
    assert work_item.task_content == "OAuth 통합"

    assert jira.calls[0].summary == "[로그인] Feature | OAuth 통합"
    assert jira.calls[0].issue_type == "Story"

    assert len(slack.posted) == 1
    assert "CATCHUP-1" in slack.posted[0]["text"]


@pytest.mark.asyncio
async def test_publish_invalid_parent_key_is_dropped(session, work_item):
    jira = FakeJira()
    handler = PublishHandler(jira_svc=jira, search_svc=FakeSearch(), slack=FakeSlack())

    await handler.publish(
        session,
        work_item_id=work_item.id,
        confirmed_slots={
            "team_id": "ari:team/abc",
            "project_key": "CATCHUP",
            "task_type": "Feature",
            "task_content": "X",
            "parent_issue_key": "not a key",
        },
    )

    await session.refresh(work_item)
    assert work_item.parent_issue_key is None
    assert jira.calls[0].parent_key is None


@pytest.mark.asyncio
async def test_publish_marks_failed_on_jira_error(session, work_item):
    jira = FakeJira(fail=JiraError("Bad request"))
    slack = FakeSlack()
    handler = PublishHandler(jira_svc=jira, search_svc=FakeSearch(), slack=slack)

    with pytest.raises(JiraError):
        await handler.publish(
            session,
            work_item_id=work_item.id,
            confirmed_slots={
                "project_key": "CATCHUP",
                "task_type": "Feature",
                "task_content": "X",
            },
        )

    await session.refresh(work_item)
    assert work_item.status == "failed"
    assert "Bad request" in work_item.error_message
    assert "실패" in slack.posted[-1]["text"]


@pytest.mark.asyncio
async def test_publish_rejects_missing_project(session, work_item):
    work_item.jira_project_key = None
    await session.commit()

    handler = PublishHandler(jira_svc=FakeJira(), search_svc=FakeSearch(), slack=FakeSlack())

    with pytest.raises(JiraError):
        await handler.publish(
            session,
            work_item_id=work_item.id,
            confirmed_slots={"task_content": "X", "task_type": "Feature"},
        )
    await session.refresh(work_item)
    assert work_item.status == "failed"
