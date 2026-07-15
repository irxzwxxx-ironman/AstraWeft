"""Create durable task runtime, request log, and artifact tables.

Revision ID: 20260715_0003
Revises: 20260715_0002
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_0003"
down_revision: str | None = "20260715_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider_id", sa.String(length=36), nullable=False),
        sa.Column("model_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=160), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("provider_config_snapshot_json", sa.Text(), nullable=False),
        sa.Column("normalized_output_json", sa.Text(), nullable=True),
        sa.Column("remote_task_id", sa.String(length=240), nullable=True),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("progress", sa.Integer(), nullable=True),
        sa.Column("poll_after_at", sa.String(length=40), nullable=True),
        sa.Column("timeout_at", sa.String(length=40), nullable=True),
        sa.Column("cancel_requested_at", sa.String(length=40), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.String(length=40), nullable=True),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "progress IS NULL OR (progress >= 0 AND progress <= 100)",
            name="ck_tasks_progress",
        ),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_tasks_scheduler",
        "tasks",
        ["status", "poll_after_at", "priority", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_provider_created",
        "tasks",
        ["provider_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_remote",
        "tasks",
        ["provider_id", "remote_task_id"],
        unique=False,
    )
    op.create_table(
        "task_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("provider_error_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("retry_after_at", sa.String(length=40), nullable=True),
        sa.Column("started_at", sa.String(length=40), nullable=False),
        sa.Column("ended_at", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "attempt_no", "phase", name="uq_attempt_task_no_phase"),
    )
    op.create_index(
        "ix_attempts_task",
        "task_attempts",
        ["task_id", "attempt_no"],
        unique=False,
    )
    op.create_table(
        "request_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=True),
        sa.Column("provider_id", sa.String(length=36), nullable=False),
        sa.Column("model_id", sa.String(length=36), nullable=True),
        sa.Column("trace_id", sa.String(length=80), nullable=False),
        sa.Column("operation", sa.String(length=160), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=True),
        sa.Column("url_template", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("request_summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("response_summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("usage_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("amount_micros", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint("latency_ms >= 0", name="ck_request_logs_latency"),
        sa.CheckConstraint(
            "(amount_micros IS NULL AND currency IS NULL) OR "
            "(amount_micros IS NOT NULL AND amount_micros >= 0 AND currency IS NOT NULL)",
            name="ck_request_logs_cost",
        ),
        sa.ForeignKeyConstraint(["attempt_id"], ["task_attempts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_request_logs_created", "request_logs", ["created_at"], unique=False)
    op.create_index(
        "ix_request_logs_provider_created",
        "request_logs",
        ["provider_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=160), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("source_url_redacted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("deleted_at", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relative_path"),
    )
    op.create_index("ix_artifacts_task_id", "artifacts", ["task_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_artifacts_task_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_request_logs_provider_created", table_name="request_logs")
    op.drop_index("ix_request_logs_created", table_name="request_logs")
    op.drop_table("request_logs")
    op.drop_index("ix_attempts_task", table_name="task_attempts")
    op.drop_table("task_attempts")
    op.drop_index("ix_tasks_remote", table_name="tasks")
    op.drop_index("ix_tasks_provider_created", table_name="tasks")
    op.drop_index("ix_tasks_scheduler", table_name="tasks")
    op.drop_table("tasks")
