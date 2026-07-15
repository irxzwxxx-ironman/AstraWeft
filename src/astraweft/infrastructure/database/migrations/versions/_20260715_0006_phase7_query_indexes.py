"""Add keyset pagination indexes for Phase 7 read models.

Revision ID: 20260715_0006
Revises: 20260715_0005
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_0006"
down_revision: str | None = "20260715_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_tasks_created_id", "tasks", ["created_at", "id"], unique=False)
    op.create_index(
        "ix_request_logs_created_id",
        "request_logs",
        ["created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_artifacts_active_created_id",
        "artifacts",
        ["created_at", "id"],
        unique=False,
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_artifacts_trash_created_id",
        "artifacts",
        ["created_at", "id"],
        unique=False,
        sqlite_where=sa.text("deleted_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_artifacts_trash_created_id", table_name="artifacts")
    op.drop_index("ix_artifacts_active_created_id", table_name="artifacts")
    op.drop_index("ix_request_logs_created_id", table_name="request_logs")
    op.drop_index("ix_tasks_created_id", table_name="tasks")
