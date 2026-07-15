"""Create ComfyUI adapter ledgers and NodeRun execution intent.

Revision ID: 20260715_0005
Revises: 20260715_0004
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_0005"
down_revision: str | None = "20260715_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "comfyui_instances",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("health", sa.String(length=24), nullable=False, server_default="UNKNOWN"),
        sa.Column("version", sa.String(length=80), nullable=True),
        sa.Column("python_version", sa.String(length=80), nullable=True),
        sa.Column("capabilities_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("node_catalog_hash", sa.String(length=64), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("last_checked_at", sa.String(length=40), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.Column("deleted_at", sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_comfyui_instances_active_name",
        "comfyui_instances",
        ["name"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "comfyui_templates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("instance_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("prompt_json", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("input_schema_json", sa.Text(), nullable=False),
        sa.Column("input_targets_json", sa.Text(), nullable=False),
        sa.Column("output_nodes_json", sa.Text(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["instance_id"], ["comfyui_instances.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instance_id", "name", name="uq_comfyui_template_name"),
    )
    op.create_index(
        "ix_comfyui_templates_instance_id",
        "comfyui_templates",
        ["instance_id"],
        unique=False,
    )
    op.create_index(
        "ix_comfyui_templates_checksum",
        "comfyui_templates",
        ["checksum"],
        unique=False,
    )
    op.create_table(
        "comfyui_executions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("node_run_id", sa.String(length=36), nullable=False),
        sa.Column("instance_id", sa.String(length=36), nullable=False),
        sa.Column("template_id", sa.String(length=36), nullable=True),
        sa.Column("template_checksum", sa.String(length=64), nullable=False),
        sa.Column("workflow_checksum", sa.String(length=64), nullable=False),
        sa.Column("prompt_json", sa.Text(), nullable=False),
        sa.Column("output_nodes_json", sa.Text(), nullable=False),
        sa.Column("client_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("remote_prompt_id", sa.String(length=160), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=True),
        sa.Column("output_json", sa.Text(), nullable=True),
        sa.Column("artifact_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("poll_after_at", sa.String(length=40), nullable=True),
        sa.Column("timeout_at", sa.String(length=40), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.String(length=40), nullable=True),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "progress IS NULL OR (progress >= 0 AND progress <= 100)",
            name="ck_comfyui_executions_progress",
        ),
        sa.ForeignKeyConstraint(["instance_id"], ["comfyui_instances.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["node_run_id"], ["node_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["comfyui_templates.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_run_id"),
    )
    op.create_index(
        "ix_comfyui_executions_status_poll",
        "comfyui_executions",
        ["status", "poll_after_at"],
        unique=False,
    )
    op.create_index(
        "ix_comfyui_executions_instance_created",
        "comfyui_executions",
        ["instance_id", "created_at"],
        unique=False,
    )
    with op.batch_alter_table("node_runs") as batch:
        batch.add_column(
            sa.Column("planned_comfyui_execution_id", sa.String(length=36), nullable=True)
        )
        batch.add_column(sa.Column("comfyui_execution_id", sa.String(length=36), nullable=True))
        batch.create_unique_constraint(
            "uq_node_runs_planned_comfyui_execution_id",
            ["planned_comfyui_execution_id"],
        )
        batch.create_unique_constraint(
            "uq_node_runs_comfyui_execution_id", ["comfyui_execution_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("node_runs") as batch:
        batch.drop_constraint("uq_node_runs_comfyui_execution_id", type_="unique")
        batch.drop_constraint("uq_node_runs_planned_comfyui_execution_id", type_="unique")
        batch.drop_column("comfyui_execution_id")
        batch.drop_column("planned_comfyui_execution_id")
    op.drop_index("ix_comfyui_executions_instance_created", table_name="comfyui_executions")
    op.drop_index("ix_comfyui_executions_status_poll", table_name="comfyui_executions")
    op.drop_table("comfyui_executions")
    op.drop_index("ix_comfyui_templates_checksum", table_name="comfyui_templates")
    op.drop_index("ix_comfyui_templates_instance_id", table_name="comfyui_templates")
    op.drop_table("comfyui_templates")
    op.drop_index("uq_comfyui_instances_active_name", table_name="comfyui_instances")
    op.drop_table("comfyui_instances")
