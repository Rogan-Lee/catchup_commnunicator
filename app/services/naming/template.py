from __future__ import annotations

import string
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NamingRule

DEFAULT_TEMPLATE = "[{parent_feature}] {task_type} | {task_content}"


class _SafeDict(dict):
    """Format mapping that returns empty string for missing keys."""

    def __missing__(self, key):
        return ""


class NamingRuleEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def build_summary(
        self,
        project_key: str,
        task_type: str | None,
        slots: dict[str, Any],
    ) -> str:
        template = await self._get_template(project_key, task_type)
        return self._render(template, slots, task_type)

    async def _get_template(self, project_key: str, task_type: str | None) -> str:
        if task_type:
            rule = await self.db.scalar(
                select(NamingRule).where(
                    NamingRule.project_key == project_key,
                    NamingRule.task_type == task_type,
                )
            )
            if rule:
                return rule.template

        rule = await self.db.scalar(
            select(NamingRule).where(
                NamingRule.project_key == project_key,
                NamingRule.task_type.is_(None),
            )
        )
        if rule:
            return rule.template

        return DEFAULT_TEMPLATE

    @staticmethod
    def _render(template: str, slots: dict[str, Any], task_type: str | None) -> str:
        values = _SafeDict(
            parent_feature=slots.get("parent_feature") or "기타",
            task_type=task_type or "Task",
            task_content=slots.get("task_content") or "",
            version=slots.get("version") or "",
            target_feature=slots.get("target_feature") or "",
        )
        try:
            rendered = string.Formatter().vformat(template, (), values)
        except (KeyError, IndexError, ValueError):
            rendered = template
        return _collapse_whitespace(rendered)


def _collapse_whitespace(s: str) -> str:
    return " ".join(s.split()).strip()
