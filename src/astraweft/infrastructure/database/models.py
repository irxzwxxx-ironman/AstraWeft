"""SQLAlchemy metadata for the locally gated AstraWeft schema."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative metadata root used by migrations."""


class AppSetting(Base):
    """Transactional settings reserved by the approved ER design."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ProviderCredentialRecord(Base):
    """Non-secret credential metadata; values remain in SecretStore."""

    __tablename__ = "provider_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_type: Mapped[str] = mapped_column(String(20), nullable=False)
    credential_ref: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    credential_type: Mapped[str] = mapped_column(String(40), nullable=False)
    hint: Mapped[str | None] = mapped_column(String(80), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ProviderRecord(Base):
    """Configured Provider instance."""

    __tablename__ = "providers"
    __table_args__ = (
        Index(
            "uq_providers_active_name",
            "name",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    plugin_id: Mapped[str] = mapped_column(String(180), index=True, nullable=False)
    plugin_version: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    credential_id: Mapped[str | None] = mapped_column(
        ForeignKey("provider_credentials.id", ondelete="RESTRICT"), nullable=True
    )
    health_status: Mapped[str] = mapped_column(String(24), nullable=False)
    last_checked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    deleted_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ModelRecord(Base):
    """Provider model catalog with user-owned preferences kept separately."""

    __tablename__ = "models"
    __table_args__ = (
        Index("uq_models_provider_remote", "provider_id", "remote_model_id", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    remote_model_id: Mapped[str] = mapped_column(String(240), nullable=False)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    modality: Mapped[str] = mapped_column(String(40), nullable=False)
    operations_json: Mapped[str] = mapped_column(Text, nullable=False)
    parameter_schema_json: Mapped[str] = mapped_column(Text, nullable=False)
    parameter_ui_schema_json: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema_json: Mapped[str] = mapped_column(Text, nullable=False)
    capabilities_json: Mapped[str] = mapped_column(Text, nullable=False)
    pricing_json: Mapped[str] = mapped_column(Text, nullable=False)
    default_params_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    deprecated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    synced_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class TaskRecord(Base):
    """Durable local task and its remote execution identity."""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "progress IS NULL OR (progress >= 0 AND progress <= 100)",
            name="ck_tasks_progress",
        ),
        Index("ix_tasks_scheduler", "status", "poll_after_at", "priority", "created_at"),
        Index("ix_tasks_provider_created", "provider_id", "created_at"),
        Index("ix_tasks_created_id", "created_at", "id"),
        Index("ix_tasks_remote", "provider_id", "remote_task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False
    )
    model_id: Mapped[str | None] = mapped_column(
        ForeignKey("models.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(160), nullable=False)
    input_json: Mapped[str] = mapped_column(Text, nullable=False)
    provider_config_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    remote_task_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    poll_after_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    timeout_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cancel_requested_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class TaskAttemptRecord(Base):
    """One persisted external action for a task."""

    __tablename__ = "task_attempts"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt_no", "phase", name="uq_attempt_task_no_phase"),
        Index("ix_attempts_task", "task_id", "attempt_no"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_error_json: Mapped[str] = mapped_column(Text, nullable=False)
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    retry_after_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    started_at: Mapped[str] = mapped_column(String(40), nullable=False)
    ended_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class RequestLogRecord(Base):
    """Redacted request facts, usage, cost, and errors."""

    __tablename__ = "request_logs"
    __table_args__ = (
        CheckConstraint("latency_ms >= 0", name="ck_request_logs_latency"),
        CheckConstraint(
            "(amount_micros IS NULL AND currency IS NULL) OR "
            "(amount_micros IS NOT NULL AND amount_micros >= 0 AND currency IS NOT NULL)",
            name="ck_request_logs_cost",
        ),
        Index("ix_request_logs_created", "created_at"),
        Index("ix_request_logs_provider_created", "provider_id", "created_at"),
        Index("ix_request_logs_created_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("task_attempts.id", ondelete="SET NULL"), nullable=True
    )
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False
    )
    model_id: Mapped[str | None] = mapped_column(
        ForeignKey("models.id", ondelete="SET NULL"), nullable=True
    )
    trace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    operation: Mapped[str] = mapped_column(String(160), nullable=False)
    method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    url_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    request_summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    response_summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    usage_json: Mapped[str] = mapped_column(Text, nullable=False)
    amount_micros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ArtifactRecord(Base):
    """Content-addressed local artifact metadata and lineage."""

    __tablename__ = "artifacts"
    __table_args__ = (
        Index(
            "ix_artifacts_active_created_id",
            "created_at",
            "id",
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_artifacts_trash_created_id",
            "created_at",
            "id",
            sqlite_where=text("deleted_at IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_url_redacted: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    deleted_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class WorkflowRecord(Base):
    """Stable workflow identity; current version lives in a normalized pointer table."""

    __tablename__ = "workflows"
    __table_args__ = (
        Index(
            "uq_workflows_active_name",
            "name",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    deleted_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class WorkflowVersionRecord(Base):
    """One draft or immutable published workflow graph definition."""

    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version_no", name="uq_workflow_version_no"),
        Index(
            "uq_workflow_single_draft",
            "workflow_id",
            unique=True,
            sqlite_where=text("status = 'DRAFT'"),
        ),
        Index("ix_workflow_versions_checksum", "checksum"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    input_schema_json: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema_json: Mapped[str] = mapped_column(Text, nullable=False)
    output_bindings_json: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    published_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class WorkflowCurrentVersionRecord(Base):
    """Avoid a circular workflow/version foreign key while preserving integrity."""

    __tablename__ = "workflow_current_versions"

    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), primary_key=True
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="RESTRICT"), unique=True, nullable=False
    )


class WorkflowNodeRecord(Base):
    """Version-owned node with frozen schemas and safe configuration."""

    __tablename__ = "workflow_nodes"
    __table_args__ = (
        UniqueConstraint("workflow_version_id", "node_key", name="uq_workflow_node_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_version_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    node_key: Mapped[str] = mapped_column(String(64), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    # Draft imports may carry source-install identities until the user rebinds
    # them locally. Publication validates these soft references; Task rows keep
    # strict Provider/Model foreign keys at execution time.
    provider_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    operation: Mapped[str | None] = mapped_column(String(160), nullable=True)
    input_schema_json: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_bindings_json: Mapped[str] = mapped_column(Text, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    continue_on_error: Mapped[bool] = mapped_column(Boolean, nullable=False)
    position_x: Mapped[int] = mapped_column(Integer, nullable=False)
    position_y: Mapped[int] = mapped_column(Integer, nullable=False)


class WorkflowEdgeRecord(Base):
    """Explicit source-output to target-input connection."""

    __tablename__ = "workflow_edges"
    __table_args__ = (
        CheckConstraint("source_node_id != target_node_id", name="ck_workflow_edge_no_self"),
        UniqueConstraint(
            "workflow_version_id",
            "source_node_id",
            "source_port",
            "target_node_id",
            "target_port",
            name="uq_workflow_edge",
        ),
        UniqueConstraint(
            "workflow_version_id",
            "target_node_id",
            "target_port",
            name="uq_workflow_target_port",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_version_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_node_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False
    )
    source_port: Mapped[str] = mapped_column(String(64), nullable=False)
    target_node_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False
    )
    target_port: Mapped[str] = mapped_column(String(64), nullable=False)


class WorkflowRunRecord(Base):
    """One execution of an immutable workflow version."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("ix_workflow_runs_status_created", "status", "created_at"),
        Index("ix_workflow_runs_workflow_created", "workflow_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="RESTRICT"), nullable=False
    )
    workflow_version_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    input_json: Mapped[str] = mapped_column(Text, nullable=False)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    definition_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cancel_requested_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class NodeRunRecord(Base):
    """Node-level execution fact and durable Task creation intent."""

    __tablename__ = "node_runs"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "workflow_node_id", name="uq_node_run_node"),
        Index("ix_node_runs_run_status", "workflow_run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    workflow_node_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    node_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resolved_input_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    planned_task_id: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    planned_comfyui_execution_id: Mapped[str | None] = mapped_column(
        String(36), unique=True, nullable=True
    )
    comfyui_execution_id: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ArtifactLinkRecord(Base):
    """Port-level lineage between NodeRun and an immutable local Artifact."""

    __tablename__ = "artifact_links"
    __table_args__ = (
        UniqueConstraint(
            "node_run_id",
            "artifact_id",
            "direction",
            "port_name",
            name="uq_artifact_link",
        ),
        Index("ix_artifact_links_artifact", "artifact_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_run_id: Mapped[str] = mapped_column(
        ForeignKey("node_runs.id", ondelete="CASCADE"), nullable=False
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    port_name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ComfyUIInstanceRecord(Base):
    """Configured ComfyUI execution endpoint and capability snapshot."""

    __tablename__ = "comfyui_instances"
    __table_args__ = (
        Index(
            "uq_comfyui_instances_active_name",
            "name",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    health: Mapped[str] = mapped_column(String(24), nullable=False)
    version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    python_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    capabilities_json: Mapped[str] = mapped_column(Text, nullable=False)
    node_catalog_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_checked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    deleted_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ComfyUITemplateRecord(Base):
    """Validated API-format prompt available for freezing into a WorkflowVersion."""

    __tablename__ = "comfyui_templates"
    __table_args__ = (
        UniqueConstraint("instance_id", "name", name="uq_comfyui_template_name"),
        Index("ix_comfyui_templates_checksum", "checksum"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instance_id: Mapped[str] = mapped_column(
        ForeignKey("comfyui_instances.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    prompt_json: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    input_schema_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_targets_json: Mapped[str] = mapped_column(Text, nullable=False)
    output_nodes_json: Mapped[str] = mapped_column(Text, nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ComfyUIExecutionRecord(Base):
    """Durable local intent and remote prompt reconciliation ledger."""

    __tablename__ = "comfyui_executions"
    __table_args__ = (
        Index("ix_comfyui_executions_status_poll", "status", "poll_after_at"),
        Index("ix_comfyui_executions_instance_created", "instance_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_run_id: Mapped[str] = mapped_column(
        ForeignKey("node_runs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    instance_id: Mapped[str] = mapped_column(
        ForeignKey("comfyui_instances.id", ondelete="RESTRICT"), nullable=False
    )
    template_id: Mapped[str | None] = mapped_column(
        ForeignKey("comfyui_templates.id", ondelete="SET NULL"), nullable=True
    )
    template_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_json: Mapped[str] = mapped_column(Text, nullable=False)
    output_nodes_json: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    remote_prompt_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    poll_after_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    timeout_at: Mapped[str] = mapped_column(String(40), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
