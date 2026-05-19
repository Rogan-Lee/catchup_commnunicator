"""seed default naming rules

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-19

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Global default seed. Per-project rules can be added via UI/CLI later.
_GLOBAL_DEFAULT = "[{parent_feature}] {task_type} | {task_content}"


def upgrade() -> None:
    naming_rules = sa.table(
        "naming_rules",
        sa.column("project_key", sa.String),
        sa.column("task_type", sa.String),
        sa.column("template", sa.Text),
    )
    op.bulk_insert(
        naming_rules,
        [
            {"project_key": "__default__", "task_type": None, "template": _GLOBAL_DEFAULT},
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM naming_rules WHERE project_key = '__default__'")
