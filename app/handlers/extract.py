from __future__ import annotations

import re
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
from app.services.atlassian.types import AtlassianUser
from app.services.llm.base import LLMExtractor
from app.services.llm.schemas import ExtractedWorkItem, ExtractionContext
from app.services.slack.client import SlackClient
from app.services.slack.preview_card import build_preview_blocks

log = get_logger(__name__)

_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")


def is_issue_key(value: str | None) -> bool:
    return bool(value) and bool(_ISSUE_KEY_RE.match(value or ""))


class ExtractHandler:
    """Pipeline: Slack message → LLM extraction → DB → preview card."""

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
        existing = await session.scalar(
            select(StandupEntry).where(
                StandupEntry.channel_id == channel_id,
                StandupEntry.slack_message_ts == message_ts,
            )
        )
        if existing:
            log.info("extract.duplicate.skipped", channel=channel_id, ts=message_ts)
            return

        entry = StandupEntry(
            channel_id=channel_id,
            slack_message_ts=message_ts,
            author_slack_id=author_slack_id,
            raw_text=text,
            posted_at=posted_at,
            extraction_status="extracting",
        )
        session.add(entry)
        await session.flush()

        try:
            atlassian_user = await self._slack_to_atlassian(author_slack_id)
            team = None
            if atlassian_user:
                try:
                    team = await self.teams_svc.get_user_primary_team(
                        atlassian_user.account_id
                    )
                except Exception as e:
                    log.warning("extract.team.lookup_failed", error=str(e))

            project_key = (
                self.team_to_project_map.get(team.id) if team else None
            )

            active_epics: list[tuple[str, str]] = []
            if team and project_key:
                try:
                    epics = await self.search_svc.get_active_epics_for_team(
                        project_key, team.id, limit=10
                    )
                    active_epics = [(e.key, e.summary) for e in epics]
                except Exception as e:
                    log.warning("extract.epics.lookup_failed", error=str(e))

            try:
                all_teams = await self.teams_svc.list_teams()
            except Exception as e:
                log.warning("extract.teams.list_failed", error=str(e))
                all_teams = []

            context = ExtractionContext(
                user_primary_team_id=team.id if team else None,
                user_primary_team_name=team.name if team else None,
                available_teams=[(t.id, t.name) for t in all_teams],
                active_epics=active_epics,
                project_keys=list(set(self.team_to_project_map.values())),
            )

            extracted_items: list[ExtractedWorkItem]
            try:
                result = await self.extractor.extract(text, context)
                extracted_items = list(result.items) or [
                    ExtractedWorkItem(task_content=text[:200])
                ]
                emit_metric("llm.extract.ok", item_count=len(extracted_items))
            except LLMError as e:
                log.warning("extract.llm.failed", error=str(e))
                entry.extraction_error = str(e)[:500]
                extracted_items = [ExtractedWorkItem(task_content=text[:200])]
                emit_metric("llm.extract.fallback", reason=str(e)[:100])

            for idx, ex_item in enumerate(extracted_items, start=1):
                ex_team_id = ex_item.team_id or (team.id if team else None)
                ex_project_key = (
                    self.team_to_project_map.get(ex_team_id)
                    if ex_team_id
                    else project_key
                )
                hint = ex_item.parent_issue_hint
                slot_dict = ex_item.model_dump(mode="json")

                candidates = []
                if ex_project_key:
                    try:
                        candidates = await self.parent_resolver.resolve_candidates(
                            project_key=ex_project_key,
                            hint=hint,
                            author_account_id=(
                                atlassian_user.account_id if atlassian_user else None
                            ),
                        )
                    except Exception as e:
                        log.warning("extract.parent_resolve.failed", error=str(e))
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
                    jira_team_id=ex_team_id,
                    jira_project_key=ex_project_key,
                    task_type=ex_item.task_type.value if ex_item.task_type else None,
                    parent_feature=ex_item.parent_feature,
                    task_content=ex_item.task_content,
                    parent_issue_key=resolved_parent,
                    status="pending",
                )
                session.add(wi)

            entry.extraction_status = "completed"
            await session.flush()
            await session.refresh(entry, attribute_names=["work_items"])
            await session.commit()

            fallback, blocks = build_preview_blocks(
                entry, list(entry.work_items), publish_enabled=self.publish_enabled
            )
            try:
                await self.slack.post_message(
                    channel=channel_id,
                    thread_ts=message_ts,
                    text=fallback,
                    blocks=blocks,
                )
            except Exception as e:
                log.error("extract.slack.post_failed", error=str(e))

        except Exception as e:
            log.exception("extract.failed", error=str(e))
            entry.extraction_status = "failed"
            entry.extraction_error = str(e)[:500]
            await session.commit()
            raise

    async def _slack_to_atlassian(self, slack_user_id: str) -> AtlassianUser | None:
        email = await self.slack.get_user_email(slack_user_id)
        if not email:
            return None
        try:
            return await self.search_svc.lookup_account_by_email(email)
        except Exception as e:
            log.warning("extract.atlassian.lookup_failed", error=str(e))
            return None
