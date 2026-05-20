from __future__ import annotations

import re

from app.services.llm.base import LLMExtractor
from app.services.llm.schemas import (
    ExtractedWorkItem,
    ExtractionContext,
    ExtractionResult,
)

_ISSUE_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9_]+-\d+\b")
_BULLET_RE = re.compile(r"^\s*(?:[-*•·–]|\d+[.)])\s+(.*\S)\s*$")
_SECTION_RE = re.compile(r"^\s*\[(?P<title>[^\]]+)\]\s*$")

# Section headers _compose_text emits; we only turn "today" into work items.
_TODAY_HINTS = ("오늘", "today", "할 일", "할일")
_SKIP_HINTS = ("어제", "yesterday", "차단", "blocker", "blockers")


class RuleBasedExtractor(LLMExtractor):
    """Deterministic, no-API extractor.

    Splits a standup message into work items by bullet points. Each bullet (or
    each line when there are no bullets) becomes one work item. A Jira issue key
    in the text is captured as a parent hint. Never calls an external service,
    so it is free and has no rate limits.
    """

    async def extract(
        self, message: str, context: ExtractionContext
    ) -> ExtractionResult:
        body = _today_section(message)
        items = [_to_item(line) for line in _bullet_lines(body)]
        if not items:
            # Nothing splittable: keep the whole thing as a single item.
            text = body.strip() or message.strip()
            items = [_to_item(text)] if text else []
        return ExtractionResult(items=items)


def _today_section(message: str) -> str:
    """Return the '오늘 할 일' section if the message is sectioned, else all text.

    _compose_text labels sections like '[오늘 할 일]'. When present we only want
    today's plan to become tickets — yesterday/blocker are context only.
    """
    lines = message.splitlines()
    has_sections = any(_SECTION_RE.match(ln) for ln in lines)
    if not has_sections:
        return message

    collecting = False
    collected: list[str] = []
    for ln in lines:
        m = _SECTION_RE.match(ln)
        if m:
            title = m.group("title").lower()
            collecting = any(h in title for h in _TODAY_HINTS) and not any(
                s in title for s in _SKIP_HINTS
            )
            continue
        if collecting:
            collected.append(ln)
    return "\n".join(collected).strip()


def _bullet_lines(text: str) -> list[str]:
    """Bullet contents if the text is a bullet list, else each non-empty line."""
    raw = text.splitlines()
    bullets = [m.group(1).strip() for ln in raw if (m := _BULLET_RE.match(ln))]
    if bullets:
        return bullets
    return [ln.strip() for ln in raw if ln.strip()]


def _to_item(text: str) -> ExtractedWorkItem:
    hint = None
    key = _ISSUE_KEY_RE.search(text)
    if key:
        hint = key.group(0)
    return ExtractedWorkItem(task_content=text[:200], parent_issue_hint=hint)
