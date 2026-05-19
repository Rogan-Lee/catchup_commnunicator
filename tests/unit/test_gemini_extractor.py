from __future__ import annotations

import sys
import types

import pytest

from app.core.errors import LLMError
from app.services.llm.gemini import GeminiExtractor
from app.services.llm.schemas import (
    ExtractedWorkItem,
    ExtractionContext,
    ExtractionResult,
    TaskType,
)


def _install_fake_genai_types():
    """The extractor imports google.genai.types lazily; provide a stub."""
    pkg = types.ModuleType("google")
    sub = types.ModuleType("google.genai")
    tmod = types.ModuleType("google.genai.types")

    class GenerateContentConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    tmod.GenerateContentConfig = GenerateContentConfig
    sys.modules["google"] = pkg
    sys.modules["google.genai"] = sub
    sys.modules["google.genai.types"] = tmod


class _FakeResp:
    def __init__(self, parsed=None, text=None):
        self.parsed = parsed
        self.text = text


class _FakeModels:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = 0

    async def generate_content(self, *, model, contents, config):
        self.calls += 1
        result = self.behaviour(self.calls)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeAio:
    def __init__(self, models):
        self.models = models


class _FakeClient:
    def __init__(self, models):
        self.aio = _FakeAio(models)


@pytest.fixture(autouse=True)
def fake_genai():
    _install_fake_genai_types()
    yield


async def test_extract_returns_parsed_pydantic():
    expected = ExtractionResult(
        items=[
            ExtractedWorkItem(task_content="OAuth", task_type=TaskType.FEATURE),
        ]
    )
    models = _FakeModels(lambda _: _FakeResp(parsed=expected))
    ex = GeminiExtractor(_FakeClient(models), "gemini-2.0-flash-exp")

    out = await ex.extract("OAuth 마무리", ExtractionContext())
    assert out == expected
    assert models.calls == 1


async def test_extract_falls_back_to_text_json():
    raw = '{"items":[{"task_content":"X"}]}'
    models = _FakeModels(lambda _: _FakeResp(text=raw))
    ex = GeminiExtractor(_FakeClient(models), "m")
    out = await ex.extract("X", ExtractionContext())
    assert out.items[0].task_content == "X"


async def test_extract_retries_on_resource_exhausted():
    class ResourceExhausted(Exception):
        pass

    expected = ExtractionResult(items=[ExtractedWorkItem(task_content="ok")])

    def behaviour(call_num):
        if call_num < 3:
            return ResourceExhausted("slow down")
        return _FakeResp(parsed=expected)

    models = _FakeModels(behaviour)
    ex = GeminiExtractor(_FakeClient(models), "m", max_retries=3)
    # Replace asyncio.sleep with a no-op via monkeypatch in conftest? Inline here.
    import asyncio

    orig = asyncio.sleep
    asyncio.sleep = lambda _d: orig(0)  # type: ignore[assignment]
    try:
        out = await ex.extract("X", ExtractionContext())
    finally:
        asyncio.sleep = orig  # type: ignore[assignment]
    assert out == expected
    assert models.calls == 3


async def test_extract_raises_after_max_retries():
    class ResourceExhausted(Exception):
        pass

    models = _FakeModels(lambda _: ResourceExhausted("nope"))
    ex = GeminiExtractor(_FakeClient(models), "m", max_retries=2)
    import asyncio

    orig = asyncio.sleep
    asyncio.sleep = lambda _d: orig(0)  # type: ignore[assignment]
    try:
        with pytest.raises(LLMError):
            await ex.extract("X", ExtractionContext())
    finally:
        asyncio.sleep = orig  # type: ignore[assignment]
    assert models.calls == 2


async def test_extract_non_retryable_error_fails_fast():
    models = _FakeModels(lambda _: ValueError("bad request"))
    ex = GeminiExtractor(_FakeClient(models), "m", max_retries=3)
    with pytest.raises(LLMError):
        await ex.extract("X", ExtractionContext())
    assert models.calls == 1
