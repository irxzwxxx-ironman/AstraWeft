"""Add filtered artifact-library query indexes.

Revision ID: 20260715_0007
Revises: 20260715_0006
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260715_0007"
down_revision: str | None = "20260715_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_artifacts_kind_deleted_created_id",
        "artifacts",
        ["kind", "deleted_at", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_provider_model_id",
        "tasks",
        ["provider_id", "model_id", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_provider_model_id", table_name="tasks")
    op.drop_index("ix_artifacts_kind_deleted_created_id", table_name="artifacts")
