from __future__ import annotations

from abc import ABC, abstractmethod

from app.services.llm.schemas import ExtractionContext, ExtractionResult


class LLMExtractor(ABC):
    @abstractmethod
    async def extract(
        self,
        message: str,
        context: ExtractionContext,
    ) -> ExtractionResult:
        """Extract work-item slots from free text.

        Raises LLMError after all retries are exhausted.
        """
