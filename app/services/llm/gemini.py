from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.errors import LLMError
from app.core.logging import get_logger
from app.services.llm.base import LLMExtractor
from app.services.llm.prompts import build_extraction_prompt
from app.services.llm.schemas import ExtractionContext, ExtractionResult

log = get_logger(__name__)


class GeminiExtractor(LLMExtractor):
    """Gemini 2.0 Flash extractor.

    The SDK import lives inside __init__ so unit tests don't need google-genai
    installed and can use a fake client.
    """

    def __init__(self, client: Any, model: str, max_retries: int = 3):
        self.client = client
        self.model = model
        self.max_retries = max_retries

    async def extract(self, message: str, context: ExtractionContext) -> ExtractionResult:
        prompt = build_extraction_prompt(message, context)

        # Lazy import so this module is importable without the SDK in tests.
        try:
            from google.genai import types  # type: ignore
        except Exception as e:  # pragma: no cover
            raise LLMError(f"google-genai not available: {e}") from e

        config = types.GenerateContentConfig(
            system_instruction=prompt.system,
            response_mime_type="application/json",
            response_schema=ExtractionResult,
            temperature=0.1,
            max_output_tokens=2000,
        )

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=[prompt.user],
                    config=config,
                )
                parsed = self._coerce_response(response)
                return parsed
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1 and _is_retryable(e):
                    delay = 2**attempt
                    log.warning(
                        "llm.gemini.retry",
                        attempt=attempt + 1,
                        delay=delay,
                        error=str(e),
                    )
                    await asyncio.sleep(delay)
                    continue
                break

        raise LLMError(f"Gemini extraction failed: {last_error}") from last_error

    @staticmethod
    def _coerce_response(response: Any) -> ExtractionResult:
        # Prefer SDK's pre-parsed shape, fall back to manual JSON parse.
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, ExtractionResult):
            return parsed
        if isinstance(parsed, dict):
            return ExtractionResult.model_validate(parsed)

        text = getattr(response, "text", None)
        if not text:
            raise LLMError("Gemini returned empty response")
        try:
            return ExtractionResult.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValueError) as e:
            raise LLMError(f"Could not parse Gemini response: {e}") from e


def _is_retryable(exc: Exception) -> bool:
    name = exc.__class__.__name__
    return name in {
        "ResourceExhausted",
        "ServiceUnavailable",
        "DeadlineExceeded",
        "InternalServerError",
        "TimeoutError",
        "ConnectError",
    }
