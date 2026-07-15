"""Create immutable workflow definitions, runs, node intent, and lineage.

Revision ID: 20260715_0004
Revises: 20260715_0003
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_0004"
down_revision: str | None = "20260715_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.Column("deleted_at", sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_workflows_active_name",
        "workflows",
        ["name"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("input_schema_json", sa.Text(), nullable=False),
        sa.Column("output_schema_json", sa.Text(), nullable=False),
        sa.Column("output_bindings_json", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.Column("published_at", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "version_no", name="uq_workflow_version_no"),
    )
    op.create_index(
        "ix_workflow_versions_workflow_id",
        "workflow_versions",
        ["workflow_id"],
        unique=False,
    )
    op.create_index(
        "uq_workflow_single_draft",
        "workflow_versions",
        ["workflow_id"],
        unique=True,
        sqlite_where=sa.text("status = 'DRAFT'"),
    )
    op.create_index(
        "ix_workflow_versions_checksum",
        "workflow_versions",
        ["checksum"],
        unique=False,
    )
    op.create_table(
        "workflow_current_versions",
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["workflow_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workflow_id"),
        sa.UniqueConstraint("version_id"),
    )
    op.create_table(
        "workflow_nodes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_version_id", sa.String(length=36), nullable=False),
        sa.Column("node_key", sa.String(length=64), nullable=False),
        sa.Column("node_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("provider_id", sa.String(length=36), nullable=True),
        sa.Column("model_id", sa.String(length=36), nullable=True),
        sa.Column("operation", sa.String(length=160), nullable=True),
        sa.Column("input_schema_json", sa.Text(), nullable=False),
        sa.Column("output_schema_json", sa.Text(), nullable=False),
        sa.Column("input_bindings_json", sa.Text(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("continue_on_error", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("position_x", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("position_y", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["workflow_version_id"], ["workflow_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_version_id", "node_key", name="uq_workflow_node_key"),
    )
    op.create_index(
        "ix_workflow_nodes_workflow_version_id",
        "workflow_nodes",
        ["workflow_version_id"],
        unique=False,
    )
    op.create_table(
        "workflow_edges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_version_id", sa.String(length=36), nullable=False),
        sa.Column("source_node_id", sa.String(length=36), nullable=False),
        sa.Column("source_port", sa.String(length=64), nullable=False),
        sa.Column("target_node_id", sa.String(length=36), nullable=False),
        sa.Column("target_port", sa.String(length=64), nullable=False),
        sa.CheckConstraint("source_node_id != target_node_id", name="ck_workflow_edge_no_self"),
        sa.ForeignKeyConstraint(["source_node_id"], ["workflow_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_node_id"], ["workflow_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workflow_version_id"], ["workflow_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_version_id",
            "source_node_id",
            "source_port",
            "target_node_id",
            "target_port",
            name="uq_workflow_edge",
        ),
        sa.UniqueConstraint(
            "workflow_version_id",
            "target_node_id",
            "target_port",
            name="uq_workflow_target_port",
        ),
    )
    op.create_index(
        "ix_workflow_edges_workflow_version_id",
        "workflow_edges",
        ["workflow_version_id"],
        unique=False,
    )
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_version_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=True),
        sa.Column("definition_checksum", sa.String(length=64), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.String(length=40), nullable=True),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.Column("cancel_requested_at", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["workflow_version_id"], ["workflow_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_runs_status_created",
        "workflow_runs",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_runs_workflow_created",
        "workflow_runs",
        ["workflow_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "node_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_node_id", sa.String(length=36), nullable=False),
        sa.Column("node_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resolved_input_json", sa.Text(), nullable=True),
        sa.Column("output_json", sa.Text(), nullable=True),
        sa.Column("planned_task_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.String(length=40), nullable=True),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_node_id"], ["workflow_nodes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("planned_task_id"),
        sa.UniqueConstraint("task_id"),
        sa.UniqueConstraint("workflow_run_id", "workflow_node_id", name="uq_node_run_node"),
    )
    op.create_index(
        "ix_node_runs_run_status",
        "node_runs",
        ["workflow_run_id", "status"],
        unique=False,
    )
    op.create_table(
        "artifact_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("node_run_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("port_name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["node_run_id"], ["node_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "node_run_id",
            "artifact_id",
            "direction",
            "port_name",
            name="uq_artifact_link",
        ),
    )
    op.create_index(
        "ix_artifact_links_artifact",
        "artifact_links",
        ["artifact_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_links_artifact", table_name="artifact_links")
    op.drop_table("artifact_links")
    op.drop_index("ix_node_runs_run_status", table_name="node_runs")
    op.drop_table("node_runs")
    op.drop_index("ix_workflow_runs_workflow_created", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_status_created", table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_index("ix_workflow_edges_workflow_version_id", table_name="workflow_edges")
    op.drop_table("workflow_edges")
    op.drop_index("ix_workflow_nodes_workflow_version_id", table_name="workflow_nodes")
    op.drop_table("workflow_nodes")
    op.drop_table("workflow_current_versions")
    op.drop_index("ix_workflow_versions_checksum", table_name="workflow_versions")
    op.drop_index("uq_workflow_single_draft", table_name="workflow_versions")
    op.drop_index("ix_workflow_versions_workflow_id", table_name="workflow_versions")
    op.drop_table("workflow_versions")
    op.drop_index("uq_workflows_active_name", table_name="workflows")
    op.drop_table("workflows")
