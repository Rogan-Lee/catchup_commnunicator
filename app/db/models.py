from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class StandupEntry(Base):
    __tablename__ = "standup_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    channel_id: Mapped[str] = mapped_column(String(20), nullable=False)
    slack_message_ts: Mapped[str] = mapped_column(String(30), nullable=False)
    author_slack_id: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    extraction_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    work_items: Mapped[list[WorkItem]] = relationship(
        back_populates="standup_entry",
        cascade="all, delete-orphan",
        order_by="WorkItem.sequence_no",
    )

    __table_args__ = (
        UniqueConstraint("channel_id", "slack_message_ts", name="uq_standup_channel_ts"),
        Index("idx_standup_entries_status", "extraction_status"),
        Index(
            "idx_standup_entries_author",
            "author_slack_id",
            "posted_at",
        ),
    )


class WorkItem(Base):
    __tablename__ = "work_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    standup_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("standup_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)

    extracted_slots: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confirmed_slots: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    jira_team_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    jira_project_key: Mapped[str | None] = mapped_column(String(20), nullable=True)
    task_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    parent_feature: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_issue_key: Mapped[str | None] = mapped_column(String(30), nullable=True)

    jira_issue_key: Mapped[str | None] = mapped_column(String(30), nullable=True)
    jira_issue_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    jira_created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    standup_entry: Mapped[StandupEntry] = relationship(back_populates="work_items")

    __table_args__ = (
        UniqueConstraint("standup_entry_id", "sequence_no", name="uq_work_entry_seq"),
        Index("idx_work_items_status", "status"),
        Index("idx_work_items_entry", "standup_entry_id"),
    )


class NamingRule(Base):
    __tablename__ = "naming_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    project_key: Mapped[str] = mapped_column(String(20), nullable=False)
    task_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    template: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("project_key", "task_type", name="uq_naming_project_type"),
    )
