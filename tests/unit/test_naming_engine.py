from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, NamingRule
from app.services.naming.template import NamingRuleEngine


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


@pytest.mark.asyncio
async def test_global_default_when_no_rule(session):
    engine = NamingRuleEngine(session)
    out = await engine.build_summary(
        "ANY",
        "Feature",
        {"parent_feature": "로그인", "task_content": "OAuth 통합"},
    )
    assert out == "[로그인] Feature | OAuth 통합"


@pytest.mark.asyncio
async def test_project_default_template(session):
    session.add(
        NamingRule(
            project_key="CATCHUP",
            task_type=None,
            template="<{task_type}> {task_content}",
        )
    )
    await session.commit()

    engine = NamingRuleEngine(session)
    out = await engine.build_summary(
        "CATCHUP", "Bug", {"task_content": "결제 실패"}
    )
    assert out == "<Bug> 결제 실패"


@pytest.mark.asyncio
async def test_project_and_task_type_specific_template(session):
    session.add_all(
        [
            NamingRule(
                project_key="CATCHUP",
                task_type=None,
                template="DEFAULT {task_content}",
            ),
            NamingRule(
                project_key="CATCHUP",
                task_type="Bug",
                template="[BUG] {task_content}",
            ),
        ]
    )
    await session.commit()

    engine = NamingRuleEngine(session)
    bug = await engine.build_summary("CATCHUP", "Bug", {"task_content": "X"})
    feat = await engine.build_summary("CATCHUP", "Feature", {"task_content": "Y"})
    assert bug == "[BUG] X"
    assert feat == "DEFAULT Y"


@pytest.mark.asyncio
async def test_missing_slots_use_defaults(session):
    engine = NamingRuleEngine(session)
    out = await engine.build_summary("ANY", "Task", {"task_content": "X"})
    assert out == "[기타] Task | X"
