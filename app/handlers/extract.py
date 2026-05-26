from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import LLMError
from app.core.logging import get_logger
from app.core.metrics import emit as emit_metric
from app.db.models import StandupEntry, WorkItem
from app.services.atlassian.jira_search import JiraSearchService
from app.services.atlassian.parent_resolver import ParentTicketResolver
from app.services.atlassian.teams import AtlassianTeamsService
from app.services.atlassian.types import AtlassianUser, Team
from app.services.llm.base import LLMExtractor
from app.services.llm.schemas import ExtractedWorkItem, ExtractionContext
from app.services.slack.client import SlackClient
from app.services.slack.preview_card import build_preview_blocks

log = get_logger(__name__)

_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")


def is_issue_key(value: str | None) -> bool:
    return bool(value) and bool(_ISSUE_KEY_RE.match(value or ""))


@dataclass
class _Actor:
    user: AtlassianUser | None
    team: Team | None
    project_key: str | None


class ExtractHandler:
    """Pipeline: Slack message → work items → preview card.

    Two entry points share the per-item + preview logic:
      - handle_message: free text → extractor → items
      - handle_structured: pre-structured items from a modal (no extraction)
    """

    def __init__(
        self,
        *,
        teams_svc: AtlassianTeamsService,
        search_svc: JiraSearchService,
        extractor: LLMExtractor,
        slack: SlackClient,
        team_to_project_map: dict[str, str],
        publish_enabled: bool = False,
        parent_resolver: ParentTicketResolver | None = None,
    ):
        self.teams_svc = teams_svc
        self.search_svc = search_svc
        self.extractor = extractor
        self.slack = slack
        self.team_to_project_map = team_to_project_map
        self.publish_enabled = publish_enabled
        self.parent_resolver = parent_resolver or ParentTicketResolver(search_svc)

    async def handle_message(
        self,
        session: AsyncSession,
        *,
        channel_id: str,
        message_ts: str,
        author_slack_id: str,
        text: str,
        posted_at: datetime,
    ) -> None:
        entry = await self._begin_entry(
            session,
            channel_id=channel_id,
            message_ts=message_ts,
            author_slack_id=author_slack_id,
            raw_text=text,
            posted_at=posted_at,
        )
        if entry is None:
            return

        try:
            actor = await self._resolve_actor(author_slack_id)
            context = await self._build_context(actor)
            items, error = await self._extract_items(text, context, actor)
            if error:
                entry.extraction_error = error
            await self._finalize(
                session,
                entry=entry,
                channel_id=channel_id,
                message_ts=message_ts,
                actor=actor,
                items=items,
            )
        except Exception as e:
            await self._fail(session, entry, e)
            raise

    async def handle_structured(
        self,
        session: AsyncSession,
        *,
        channel_id: str,
        message_ts: str,
        author_slack_id: str,
        items: list[dict],
        posted_at: datetime,
    ) -> None:
        """Items already structured by a modal — skip extraction entirely.

        Each item dict may carry: task_content, task_type, parent_feature,
        parent_issue_hint.
        """
        normalized = [
            _normalize_item(it)
            for it in items
            if (it.get("task_content") or "").strip()
        ]
        if not normalized:
            log.info("structured.no_items", channel=channel_id, ts=message_ts)
            return

        entry = await self._begin_entry(
            session,
            channel_id=channel_id,
            message_ts=message_ts,
            author_slack_id=author_slack_id,
            raw_text=_compose_raw_text(normalized),
            posted_at=posted_at,
        )
        if entry is None:
            return

        try:
            t0 = time.perf_counter()
            actor = await self._resolve_actor(author_slack_id)
            t1 = time.perf_counter()
            await self._finalize(
                session,
                entry=entry,
                channel_id=channel_id,
                message_ts=message_ts,
                actor=actor,
                items=normalized,
            )
            t2 = time.perf_counter()
            log.info(
                "structured.timing",
                resolve_actor_ms=int((t1 - t0) * 1000),
                finalize_ms=int((t2 - t1) * 1000),
                total_ms=int((t2 - t0) * 1000),
                item_count=len(normalized),
            )
            emit_metric("structured.ok", item_count=len(normalized))
        except Exception as e:
            await self._fail(session, entry, e)
            raise

    # --- shared steps

    async def _begin_entry(
        self,
        session: AsyncSession,
        *,
        channel_id: str,
        message_ts: str,
        author_slack_id: str,
        raw_text: str,
        posted_at: datetime,
    ) -> StandupEntry | None:
        existing = await session.scalar(
            select(StandupEntry).where(
                StandupEntry.channel_id == channel_id,
                StandupEntry.slack_message_ts == message_ts,
            )
        )
        if existing:
            log.info("extract.duplicate.skipped", channel=channel_id, ts=message_ts)
            return None

        entry = StandupEntry(
            channel_id=channel_id,
            slack_message_ts=message_ts,
            author_slack_id=author_slack_id,
            raw_text=raw_text,
            posted_at=posted_at,
            extraction_status="extracting",
        )
        session.add(entry)
        await session.flush()
        return entry

    async def _resolve_actor(self, author_slack_id: str) -> _Actor:
        t0 = time.perf_counter()
        user = await self._slack_to_atlassian(author_slack_id)
        t1 = time.perf_counter()
        team = None
        if user:
            try:
                team = await self.teams_svc.get_user_primary_team(user.account_id)
            except Exception as e:
                log.warning("extract.team.lookup_failed", error=str(e))
        t2 = time.perf_counter()
        project_key = self.team_to_project_map.get(team.id) if team else None
        log.info(
            "actor.timing",
            slack_to_atlassian_ms=int((t1 - t0) * 1000),
            team_lookup_ms=int((t2 - t1) * 1000),
        )
        return _Actor(user=user, team=team, project_key=project_key)

    async def _build_context(self, actor: _Actor) -> ExtractionContext:
        active_epics: list[tuple[str, str]] = []
        if actor.team and actor.project_key:
            try:
                epics = await self.search_svc.get_active_epics_for_team(
                    actor.project_key, actor.team.id, limit=10
                )
                active_epics = [(e.key, e.summary) for e in epics]
            except Exception as e:
                log.warning("extract.epics.lookup_failed", error=str(e))

        try:
            all_teams = await self.teams_svc.list_teams()
        except Exception as e:
            log.warning("extract.teams.list_failed", error=str(e))
            all_teams = []

        return ExtractionContext(
            user_primary_team_id=actor.team.id if actor.team else None,
            user_primary_team_name=actor.team.name if actor.team else None,
            available_teams=[(t.id, t.name) for t in all_teams],
            active_epics=active_epics,
            project_keys=list(set(self.team_to_project_map.values())),
        )

    async def _extract_items(
        self, text: str, context: ExtractionContext, actor: _Actor
    ) -> tuple[list[dict], str | None]:
        try:
            result = await self.extractor.extract(text, context)
            items = list(result.items) or [ExtractedWorkItem(task_content=text[:200])]
            emit_metric("llm.extract.ok", item_count=len(items))
            return [_item_from_extracted(i) for i in items], None
        except Exception as e:
            error_type = type(e).__name__
            if not isinstance(e, LLMError):
                log.exception("extract.llm.unexpected", error_type=error_type)
            else:
                log.warning("extract.llm.failed", error=str(e), error_type=error_type)
            emit_metric("llm.extract.fallback", reason=error_type)
            fallback = [_normalize_item({"task_content": text[:200]})]
            return fallback, f"{error_type}: {str(e)[:400]}"

    async def _finalize(
        self,
        session: AsyncSession,
        *,
        entry: StandupEntry,
        channel_id: str,
        message_ts: str,
        actor: _Actor,
        items: list[dict],
    ) -> None:
        parent_resolve_ms_total = 0
        for idx, item in enumerate(items, start=1):
            team_id = item.get("team_id") or (actor.team.id if actor.team else None)
            project_key = (
                self.team_to_project_map.get(team_id)
                if team_id
                else actor.project_key
            )
            hint = item.get("parent_issue_hint")

            slot_dict = dict(item)
            candidates = []
            if project_key:
                p0 = time.perf_counter()
                try:
                    candidates = await self.parent_resolver.resolve_candidates(
                        project_key=project_key,
                        hint=hint,
                        author_account_id=actor.user.account_id if actor.user else None,
                    )
                except Exception as e:
                    log.warning("extract.parent_resolve.failed", error=str(e))
                parent_resolve_ms_total += int((time.perf_counter() - p0) * 1000)
            slot_dict["parent_candidates"] = [
                {"key": c.key, "summary": c.summary, "source": c.source}
                for c in candidates
            ]
            resolved_parent = (
                hint if is_issue_key(hint) else (candidates[0].key if candidates else None)
            )

            wi = WorkItem(
                standup_entry_id=entry.id,
                sequence_no=idx,
                extracted_slots=slot_dict,
                jira_team_id=team_id,
                jira_project_key=project_key,
                task_type=item.get("task_type"),
                parent_feature=item.get("parent_feature"),
                task_content=item.get("task_content"),
                parent_issue_key=resolved_parent,
                status="pending",
            )
            session.add(wi)

        entry.extraction_status = "completed"
        db_t0 = time.perf_counter()
        await session.flush()
        await session.refresh(entry, attribute_names=["work_items"])
        await session.commit()
        db_ms = int((time.perf_counter() - db_t0) * 1000)

        fallback, blocks = build_preview_blocks(
            entry, list(entry.work_items), publish_enabled=self.publish_enabled
        )
        slack_t0 = time.perf_counter()
        try:
            await self.slack.post_message(
                channel=channel_id,
                thread_ts=message_ts,
                text=fallback,
                blocks=blocks,
            )
        except Exception as e:
            log.error("extract.slack.post_failed", error=str(e))
        slack_ms = int((time.perf_counter() - slack_t0) * 1000)
        log.info(
            "finalize.timing",
            parent_resolve_ms=parent_resolve_ms_total,
            db_ms=db_ms,
            slack_post_ms=slack_ms,
            item_count=len(items),
        )

    async def _fail(
        self, session: AsyncSession, entry: StandupEntry | None, e: Exception
    ) -> None:
        log.exception("extract.failed", error=str(e))
        if entry is not None:
            entry.extraction_status = "failed"
            entry.extraction_error = str(e)[:500]
            await session.commit()

    async def _slack_to_atlassian(self, slack_user_id: str) -> AtlassianUser | None:
        email = await self.slack.get_user_email(slack_user_id)
        if not email:
            return None
        try:
            return await self.search_svc.lookup_account_by_email(email)
        except Exception as e:
            log.warning("extract.atlassian.lookup_failed", error=str(e))
            return None


def _normalize_item(raw: dict) -> dict:
    """Coerce a structured/modal item into the slot dict _finalize consumes."""
    content = (raw.get("task_content") or "").strip()
    hint = raw.get("parent_issue_hint")
    if not hint:
        key = re.search(r"\b[A-Z][A-Z0-9_]+-\d+\b", content)
        hint = key.group(0) if key else None
    return {
        "task_content": content[:200],
        "task_type": (raw.get("task_type") or None),
        "parent_feature": (raw.get("parent_feature") or None),
        "parent_issue_hint": hint,
        "team_id": raw.get("team_id") or None,
        "team_name": raw.get("team_name") or None,
    }


def _item_from_extracted(it: ExtractedWorkItem) -> dict:
    return _normalize_item(
        {
            "task_content": it.task_content,
            "task_type": it.task_type.value if it.task_type else None,
            "parent_feature": it.parent_feature,
            "parent_issue_hint": it.parent_issue_hint,
            "team_id": it.team_id,
            "team_name": it.team_name,
        }
    )


def _compose_raw_text(items: list[dict]) -> str:
    lines = []
    for it in items:
        parts = []
        if it.get("parent_feature"):
            parts.append(f"[{it['parent_feature']}]")
        if it.get("task_type"):
            parts.append(it["task_type"])
        parts.append(it.get("task_content") or "")
        lines.append("- " + " ".join(p for p in parts if p))
    return "\n".join(lines)
