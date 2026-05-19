"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-19

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "standup_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("channel_id", sa.String(length=20), nullable=False),
        sa.Column("slack_message_ts", sa.String(length=30), nullable=False),
        sa.Column("author_slack_id", sa.String(length=20), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("posted_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "extraction_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("extraction_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("channel_id", "slack_message_ts", name="uq_standup_channel_ts"),
    )
    op.create_index(
        "idx_standup_entries_status", "standup_entries", ["extraction_status"]
    )
    op.create_index(
        "idx_standup_entries_author",
        "standup_entries",
        ["author_slack_id", "posted_at"],
    )

    op.create_table(
        "work_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "standup_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("standup_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("extracted_slots", postgresql.JSONB(), nullable=False),
        sa.Column("confirmed_slots", postgresql.JSONB(), nullable=True),
        sa.Column("jira_team_id", sa.String(length=100), nullable=True),
        sa.Column("jira_project_key", sa.String(length=20), nullable=True),
        sa.Column("task_type", sa.String(length=20), nullable=True),
        sa.Column("parent_feature", sa.Text(), nullable=True),
        sa.Column("task_content", sa.Text(), nullable=True),
        sa.Column("parent_issue_key", sa.String(length=30), nullable=True),
        sa.Column("jira_issue_key", sa.String(length=30), nullable=True),
        sa.Column("jira_issue_url", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("confirmed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("jira_created_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("standup_entry_id", "sequence_no", name="uq_work_entry_seq"),
    )
    op.create_index("idx_work_items_status", "work_items", ["status"])
    op.create_index("idx_work_items_entry", "work_items", ["standup_entry_id"])

    op.create_table(
        "naming_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("project_key", sa.String(length=20), nullable=False),
        sa.Column("task_type", sa.String(length=20), nullable=True),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("project_key", "task_type", name="uq_naming_project_type"),
    )


def downgrade() -> None:
    op.drop_table("naming_rules")
    op.drop_index("idx_work_items_entry", table_name="work_items")
    op.drop_index("idx_work_items_status", table_name="work_items")
    op.drop_table("work_items")
    op.drop_index("idx_standup_entries_author", table_name="standup_entries")
    op.drop_index("idx_standup_entries_status", table_name="standup_entries")
    op.drop_table("standup_entries")
