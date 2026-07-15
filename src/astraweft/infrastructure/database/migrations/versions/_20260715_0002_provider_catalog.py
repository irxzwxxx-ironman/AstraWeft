"""Create Provider, credential metadata, and model catalog tables.

Revision ID: 20260715_0002
Revises: 20260715_0001
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_0002"
down_revision: str | None = "20260715_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("store_type", sa.String(length=20), nullable=False),
        sa.Column("credential_ref", sa.String(length=180), nullable=False),
        sa.Column("credential_type", sa.String(length=40), nullable=False),
        sa.Column("hint", sa.String(length=80), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_ref"),
    )
    op.create_table(
        "providers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plugin_id", sa.String(length=180), nullable=False),
        sa.Column("plugin_version", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("credential_id", sa.String(length=36), nullable=True),
        sa.Column("health_status", sa.String(length=24), nullable=False, server_default="UNKNOWN"),
        sa.Column("last_checked_at", sa.String(length=40), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.Column("deleted_at", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["provider_credentials.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_providers_plugin_id", "providers", ["plugin_id"], unique=False)
    op.create_index(
        "uq_providers_active_name",
        "providers",
        ["name"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "models",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider_id", sa.String(length=36), nullable=False),
        sa.Column("remote_model_id", sa.String(length=240), nullable=False),
        sa.Column("display_name", sa.String(length=240), nullable=False),
        sa.Column("modality", sa.String(length=40), nullable=False),
        sa.Column("operations_json", sa.Text(), nullable=False),
        sa.Column("parameter_schema_json", sa.Text(), nullable=False),
        sa.Column("parameter_ui_schema_json", sa.Text(), nullable=False),
        sa.Column("output_schema_json", sa.Text(), nullable=False),
        sa.Column("capabilities_json", sa.Text(), nullable=False),
        sa.Column("pricing_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("default_params_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("deprecated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("synced_at", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_models_provider_id", "models", ["provider_id"], unique=False)
    op.create_index(
        "uq_models_provider_remote",
        "models",
        ["provider_id", "remote_model_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_models_provider_remote", table_name="models")
    op.drop_index("ix_models_provider_id", table_name="models")
    op.drop_table("models")
    op.drop_index("uq_providers_active_name", table_name="providers")
    op.drop_index("ix_providers_plugin_id", table_name="providers")
    op.drop_table("providers")
    op.drop_table("provider_credentials")
