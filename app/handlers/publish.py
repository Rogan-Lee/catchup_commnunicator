from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import JiraError, NotFoundError
from app.core.logging import get_logger
from app.core.metrics import emit as emit_metric
from app.db.models import StandupEntry, WorkItem
from app.handlers.extract import is_issue_key
from app.services.atlassian.jira_issues import (
    CreatedIssue,
    CreateIssuePayload,
    JiraIssueService,
    task_type_to_issue_type,
)
from app.services.atlassian.jira_search import JiraSearchService
from app.services.naming.template import NamingRuleEngine
from app.services.slack.client import SlackClient
from sqlalchemy import select

log = get_logger(__name__)


class PublishHandler:
    """Modal submit → Jira issue → Slack thread reply."""

    def __init__(
        self,
        *,
        jira_svc: JiraIssueService,
        search_svc: JiraSearchService,
        slack: SlackClient,
    ):
        self.jira_svc = jira_svc
        self.search_svc = search_svc
        self.slack = slack

    async def publish(
        self,
        session: AsyncSession,
        *,
        work_item_id: uuid.UUID,
        confirmed_slots: dict,
        notify: bool = True,
    ) -> CreatedIssue:
        wi = await session.scalar(
            select(WorkItem)
            .where(WorkItem.id == work_item_id)
            .options(selectinload(WorkItem.standup_entry))
        )
        if not wi:
            raise NotFoundError(f"WorkItem {work_item_id}")
        entry: StandupEntry = wi.standup_entry

        _apply_slots(wi, confirmed_slots)
        wi.status = "publishing"
        wi.confirmed_at = datetime.now(tz=timezone.utc)
        await session.flush()

        if not wi.jira_project_key:
            wi.status = "failed"
            wi.error_message = "missing project_key"
            await session.commit()
            await self._reply(entry, f"❌ 발행 실패: 프로젝트가 지정되지 않았습니다.")
            raise JiraError("missing project_key")
        if not wi.task_content:
            wi.status = "failed"
            wi.error_message = "missing task_content"
            await session.commit()
            await self._reply(entry, f"❌ 발행 실패: 작업 내용이 비어있습니다.")
            raise JiraError("missing task_content")

        try:
            naming = NamingRuleEngine(session)
            summary = await naming.build_summary(
                project_key=wi.jira_project_key,
                task_type=wi.task_type,
                slots=confirmed_slots,
            )

            # Assignee: the user picked in the modal, else the standup author.
            assignee_account_id = None
            assignee_slack_id = (
                confirmed_slots.get("assignee_slack_id") or entry.author_slack_id
            )
            try:
                email = await self.slack.get_user_email(assignee_slack_id)
                if email:
                    user = await self.search_svc.lookup_account_by_email(email)
                    if user:
                        assignee_account_id = user.account_id
            except Exception as e:
                log.warning("publish.assignee.lookup_failed", error=str(e))

            # Prefer the issue type the user picked in the modal; fall back to
            # the task-type → issue-type mapping when the dropdown was absent.
            issue_type = confirmed_slots.get("issue_type") or task_type_to_issue_type(
                wi.task_type
            )
            payload = CreateIssuePayload(
                project_key=wi.jira_project_key,
                summary=summary,
                description=_build_description(entry, wi),
                issue_type=issue_type,
                parent_key=wi.parent_issue_key,
                assignee_account_id=assignee_account_id,
                team_id=wi.jira_team_id,
            )
            created = await self.jira_svc.create_issue(payload)

            wi.jira_issue_key = created.key
            wi.jira_issue_url = created.url
            wi.status = "created"
            wi.jira_created_at = datetime.now(tz=timezone.utc)
            await session.commit()

            emit_metric(
                "publish.created",
                issue_key=created.key,
                project_key=wi.jira_project_key,
                task_type=wi.task_type,
                edited=_was_edited(wi),
            )
            if notify:
                await self._reply(
                    entry, f"✅ <{created.url}|{created.key}> 생성됨\n> {summary}"
                )
                await self._post_status_controls(entry, created)
            return created

        except Exception as e:
            log.exception("publish.failed", error=str(e), work_item_id=str(wi.id))
            wi.status = "failed"
            wi.error_message = str(e)[:500]
            await session.commit()
            emit_metric("publish.failed", error=str(e)[:100])
            await self._reply(entry, f"❌ 티켓 생성 실패: {str(e)[:200]}")
            raise

    async def _reply(self, entry: StandupEntry, text: str) -> None:
        try:
            await self.slack.post_message(
                channel=entry.channel_id,
                thread_ts=entry.slack_message_ts,
                text=text,
            )
        except Exception as e:
            log.warning("publish.reply.failed", error=str(e))

    async def _post_status_controls(
        self, entry: StandupEntry, created: CreatedIssue
    ) -> None:
        """A separate message with 진행/완료 transition buttons."""
        from app.services.slack.status_card import build_status_message

        text, blocks = build_status_message(
            issue_key=created.key,
            issue_url=created.url,
            status_name="할 일",
            category="new",
            summary=created.summary,
            issue_type=created.issue_type,
        )
        try:
            await self.slack.post_message(
                channel=entry.channel_id,
                thread_ts=entry.slack_message_ts,
                text=text,
                blocks=blocks,
            )
        except Exception as e:
            log.warning("publish.status_controls.failed", error=str(e))


def _apply_slots(wi: WorkItem, slots: dict) -> None:
    wi.confirmed_slots = dict(slots)
    if slots.get("team_id"):
        wi.jira_team_id = slots["team_id"]
    if slots.get("project_key"):
        wi.jira_project_key = slots["project_key"]
    if slots.get("task_type"):
        wi.task_type = slots["task_type"]
    if "parent_feature" in slots:
        wi.parent_feature = slots.get("parent_feature")
    if slots.get("task_content"):
        wi.task_content = slots["task_content"]
    parent = slots.get("parent_issue_key")
    if parent:
        wi.parent_issue_key = parent if is_issue_key(parent) else None
    elif "parent_issue_key" in slots:
        # Empty string means user cleared it.
        wi.parent_issue_key = None


def _was_edited(wi: WorkItem) -> bool:
    """True when the user changed any meaningful slot before submitting."""
    extracted = wi.extracted_slots or {}
    confirmed = wi.confirmed_slots or {}
    for key, ex_key in (
        ("task_content", "task_content"),
        ("task_type", "task_type"),
        ("parent_feature", "parent_feature"),
        ("parent_issue_key", "parent_issue_hint"),
    ):
        if (confirmed.get(key) or None) != (extracted.get(ex_key) or None):
            return True
    return False


def _build_description(entry: StandupEntry, wi: WorkItem) -> str:
    lines = [
        f"Slack 스탠드업에서 자동 생성됨.",
        "",
        f"원문 메시지:",
        entry.raw_text,
    ]
    if wi.parent_feature:
        lines.append("")
        lines.append(f"상위 기능: {wi.parent_feature}")
    return "\n".join(lines)
