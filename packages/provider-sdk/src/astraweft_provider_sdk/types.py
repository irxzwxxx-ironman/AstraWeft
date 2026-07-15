"""Immutable Provider DTOs exchanged across the plugin boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from astraweft_provider_sdk._json import freeze_mapping

if TYPE_CHECKING:
    from astraweft_provider_sdk.protocols import (
        Clock,
        HttpTransport,
        PluginDataDirectory,
        PluginLogger,
        SecretResolver,
    )

ArtifactKind = Literal["image", "video", "audio", "text", "json"]
ArtifactSource = Literal["url", "base64", "text", "json"]
HealthStatus = Literal["healthy", "degraded", "unavailable"]
TaskState = Literal["queued", "running", "succeeded", "failed", "canceled"]


@dataclass(frozen=True, slots=True)
class SecretValue:
    """A secret that cannot leak through normal string formatting."""

    _value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self._value:
            raise ValueError("secret must not be empty")

    def reveal(self) -> str:
        return self._value

    def as_bearer(self) -> str:
        return f"Bearer {self._value}"

    def __str__(self) -> str:
        return "••••••••"

    def __repr__(self) -> str:
        return "SecretValue(••••••••)"


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    plugin_id: str
    name: str
    version: str
    plugin_api: str
    description: str
    operations: frozenset[str]
    supports_async_tasks: bool
    supports_cancel: bool
    supports_model_discovery: bool
    supports_usage: bool
    default_endpoint: str | None
    settings_schema: Mapping[str, object]
    settings_ui_schema: Mapping[str, object]
    credential_schema: Mapping[str, object]
    redaction_paths: tuple[str, ...] = ()
    idempotency: Literal["native", "emulated", "none"] = "none"
    progress: Literal["exact", "estimated", "none"] = "none"
    supports_streaming: bool = False
    client_concurrency: int = 1

    def __post_init__(self) -> None:
        if not self.plugin_id or not self.name or not self.version or not self.plugin_api:
            raise ValueError("descriptor identity fields must not be empty")
        if not self.operations:
            raise ValueError("descriptor must declare at least one operation")
        if self.client_concurrency < 1:
            raise ValueError("client_concurrency must be positive")
        object.__setattr__(self, "operations", frozenset(self.operations))
        object.__setattr__(self, "settings_schema", freeze_mapping(self.settings_schema))
        object.__setattr__(self, "settings_ui_schema", freeze_mapping(self.settings_ui_schema))
        object.__setattr__(self, "credential_schema", freeze_mapping(self.credential_schema))
        object.__setattr__(self, "redaction_paths", tuple(self.redaction_paths))


@dataclass(frozen=True, slots=True)
class PricingRule:
    unit: str
    price_micros: int
    currency: str
    effective_at: str | None = None

    def __post_init__(self) -> None:
        if self.price_micros < 0:
            raise ValueError("price_micros must be non-negative")
        if len(self.currency) != 3:
            raise ValueError("currency must be an ISO 4217 code")


@dataclass(frozen=True, slots=True)
class ProviderModel:
    remote_model_id: str
    display_name: str
    modality: str
    operations: frozenset[str]
    parameter_schema: Mapping[str, object]
    parameter_ui_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    capabilities: Mapping[str, object]
    pricing: tuple[PricingRule, ...] = ()
    deprecated: bool = False

    def __post_init__(self) -> None:
        if not self.remote_model_id or not self.display_name or not self.modality:
            raise ValueError("model identity fields must not be empty")
        if not self.operations:
            raise ValueError("model must declare at least one operation")
        object.__setattr__(self, "operations", frozenset(self.operations))
        object.__setattr__(self, "parameter_schema", freeze_mapping(self.parameter_schema))
        object.__setattr__(self, "parameter_ui_schema", freeze_mapping(self.parameter_ui_schema))
        object.__setattr__(self, "output_schema", freeze_mapping(self.output_schema))
        object.__setattr__(self, "capabilities", freeze_mapping(self.capabilities))
        object.__setattr__(self, "pricing", tuple(self.pricing))


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    operation: str
    remote_model_id: str
    inputs: Mapping[str, object]
    idempotency_key: str
    trace_id: str
    timeout_seconds: float
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not all((self.operation, self.remote_model_id, self.idempotency_key, self.trace_id)):
            raise ValueError("request identity fields must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        object.__setattr__(self, "inputs", freeze_mapping(self.inputs))
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class RemoteArtifact:
    kind: ArtifactKind
    source: ArtifactSource
    value: str | Mapping[str, object]
    mime_type: str | None = None
    filename_hint: str | None = None
    expires_at: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.value, Mapping):
            object.__setattr__(self, "value", freeze_mapping(self.value))
        elif not self.value:
            raise ValueError("artifact value must not be empty")
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class Usage:
    units: Mapping[str, int | str]
    cost_micros: int | None = None
    currency: str | None = None
    pricing_source: str | None = None

    def __post_init__(self) -> None:
        if self.cost_micros is not None and self.cost_micros < 0:
            raise ValueError("cost_micros must be non-negative")
        if self.currency is not None and len(self.currency) != 3:
            raise ValueError("currency must be an ISO 4217 code")
        if (self.cost_micros is None) != (self.currency is None):
            raise ValueError("known cost requires both cost_micros and currency")
        object.__setattr__(self, "units", freeze_mapping(self.units))


@dataclass(frozen=True, slots=True)
class ProviderOutput:
    data: Mapping[str, object]
    artifacts: tuple[RemoteArtifact, ...] = ()
    usage: Usage | None = None
    finish_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", freeze_mapping(self.data))
        object.__setattr__(self, "artifacts", tuple(self.artifacts))


@dataclass(frozen=True, slots=True)
class ProviderCallInfo:
    """Safe HTTP metadata that may be persisted in a Request Log."""

    method: str
    url_template: str
    http_status: int
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        method = self.method.strip().upper()
        url_template = self.url_template.strip()
        if not method or not url_template:
            raise ValueError("call method and URL template must not be empty")
        if not 100 <= self.http_status <= 599:
            raise ValueError("http_status must be a valid HTTP status")
        if not url_template.startswith("/") or any(
            mark in url_template for mark in ("://", "?", "#")
        ):
            raise ValueError("url_template must be a safe path template")
        request_id = self.provider_request_id
        if request_id is not None:
            request_id = request_id.strip()
            if not request_id or len(request_id) > 512 or not request_id.isascii():
                raise ValueError("provider_request_id must be safe ASCII metadata")
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "url_template", url_template)
        object.__setattr__(self, "provider_request_id", request_id)


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    mode: Literal["completed", "accepted"]
    remote_task_id: str | None = None
    output: ProviderOutput | None = None
    progress: int | None = None
    poll_after_seconds: float | None = None
    provider_request_id: str | None = None
    call: ProviderCallInfo | None = None

    def __post_init__(self) -> None:
        if self.progress is not None and not 0 <= self.progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        if self.poll_after_seconds is not None and self.poll_after_seconds < 0:
            raise ValueError("poll_after_seconds must be non-negative")
        if self.mode == "completed" and (self.output is None or self.remote_task_id is not None):
            raise ValueError("completed result requires output and no remote task")
        if self.mode == "accepted" and (not self.remote_task_id or self.output is not None):
            raise ValueError("accepted result requires a remote task and no output")
        if self.call is not None:
            if (
                self.provider_request_id is not None
                and self.provider_request_id != self.call.provider_request_id
            ):
                raise ValueError("result and call request IDs must match")
            if self.provider_request_id is None:
                object.__setattr__(self, "provider_request_id", self.call.provider_request_id)


@dataclass(frozen=True, slots=True)
class RemoteError:
    code: str
    message: str
    retryable: bool = False
    safe_details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "safe_details", freeze_mapping(self.safe_details))


@dataclass(frozen=True, slots=True)
class RemoteTaskSnapshot:
    state: TaskState
    progress: int | None = None
    output: ProviderOutput | None = None
    error: RemoteError | None = None
    poll_after_seconds: float | None = None
    provider_updated_at: str | None = None
    call: ProviderCallInfo | None = None

    def __post_init__(self) -> None:
        if self.progress is not None and not 0 <= self.progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        if self.state == "succeeded" and self.output is None:
            raise ValueError("succeeded task requires output")
        if self.state == "failed" and self.error is None:
            raise ValueError("failed task requires an error")


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    status: HealthStatus
    latency_ms: int | None
    message: str
    details: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        object.__setattr__(self, "details", freeze_mapping(self.details))


@dataclass(frozen=True, slots=True)
class CancelResult:
    accepted: bool
    terminal: bool
    message: str
    call: ProviderCallInfo | None = None


@dataclass(frozen=True, slots=True)
class ProviderContext:
    http: HttpTransport
    secrets: SecretResolver
    logger: PluginLogger
    clock: Clock
    plugin_data: PluginDataDirectory
    core_version: str
    plugin_api_version: str
