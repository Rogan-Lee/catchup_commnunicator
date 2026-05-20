from __future__ import annotations

import pytest

from app.services.llm.rule_based import RuleBasedExtractor
from app.services.llm.schemas import ExtractionContext

ctx = ExtractionContext()


@pytest.mark.asyncio
async def test_splits_bullets_into_items():
    msg = "[오늘 할 일]\n- OAuth 구글 연동\n- 결제 모듈 리팩토링\n- 로그인 버그 수정"
    result = await RuleBasedExtractor().extract(msg, ctx)
    contents = [i.task_content for i in result.items]
    assert contents == ["OAuth 구글 연동", "결제 모듈 리팩토링", "로그인 버그 수정"]


@pytest.mark.asyncio
async def test_only_today_section_becomes_items():
    msg = (
        "[어제]\n- 회의 참석\n\n"
        "[오늘 할 일]\n- API 명세 작성\n- 코드 리뷰\n\n"
        "[차단 요소]\n- SDK 응답 지연"
    )
    result = await RuleBasedExtractor().extract(msg, ctx)
    contents = [i.task_content for i in result.items]
    assert contents == ["API 명세 작성", "코드 리뷰"]


@pytest.mark.asyncio
async def test_issue_key_captured_as_parent_hint():
    msg = "[오늘 할 일]\n- CAM-12 관련 결제 화면 수정"
    result = await RuleBasedExtractor().extract(msg, ctx)
    assert result.items[0].parent_issue_hint == "CAM-12"


@pytest.mark.asyncio
async def test_no_bullets_falls_back_to_lines():
    msg = "[오늘 할 일]\nOAuth 연동 마무리"
    result = await RuleBasedExtractor().extract(msg, ctx)
    assert len(result.items) == 1
    assert result.items[0].task_content == "OAuth 연동 마무리"


@pytest.mark.asyncio
async def test_unsectioned_text_uses_whole_message():
    msg = "- 첫 번째 작업\n- 두 번째 작업"
    result = await RuleBasedExtractor().extract(msg, ctx)
    assert [i.task_content for i in result.items] == ["첫 번째 작업", "두 번째 작업"]


@pytest.mark.asyncio
async def test_numbered_list():
    msg = "1. 작업 하나\n2) 작업 둘"
    result = await RuleBasedExtractor().extract(msg, ctx)
    assert [i.task_content for i in result.items] == ["작업 하나", "작업 둘"]


@pytest.mark.asyncio
async def test_empty_message_yields_no_items():
    result = await RuleBasedExtractor().extract("   ", ctx)
    assert result.items == []
