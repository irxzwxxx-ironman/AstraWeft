"""ComfyUI instance, immutable template, and durable execution facts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

from astraweft.domain.common import freeze_mapping

_SHA256_LENGTH = 64


class ComfyUIHealth(StrEnum):
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class ComfyUIExecutionStatus(StrEnum):
    PLANNED = "PLANNED"
    SUBMITTING = "SUBMITTING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    MATERIALIZING = "MATERIALIZING"
    CANCELING = "CANCELING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCESS, self.FAILED, self.CANCELED, self.NEEDS_ATTENTION}


class ComfyUITransitionError(ValueError):
    """A ComfyUI execution transition violates recovery semantics."""


_EXECUTION_TRANSITIONS: Mapping[ComfyUIExecutionStatus, frozenset[ComfyUIExecutionStatus]] = {
    ComfyUIExecutionStatus.PLANNED: frozenset(
        {
            ComfyUIExecutionStatus.SUBMITTING,
            ComfyUIExecutionStatus.CANCELED,
            ComfyUIExecutionStatus.FAILED,
        }
    ),
    ComfyUIExecutionStatus.SUBMITTING: frozenset(
        {
            ComfyUIExecutionStatus.QUEUED,
            ComfyUIExecutionStatus.RUNNING,
            ComfyUIExecutionStatus.FAILED,
            ComfyUIExecutionStatus.CANCELED,
            ComfyUIExecutionStatus.NEEDS_ATTENTION,
        }
    ),
    ComfyUIExecutionStatus.QUEUED: frozenset(
        {
            ComfyUIExecutionStatus.RUNNING,
            ComfyUIExecutionStatus.MATERIALIZING,
            ComfyUIExecutionStatus.CANCELING,
            ComfyUIExecutionStatus.FAILED,
            ComfyUIExecutionStatus.NEEDS_ATTENTION,
        }
    ),
    ComfyUIExecutionStatus.RUNNING: frozenset(
        {
            ComfyUIExecutionStatus.QUEUED,
            ComfyUIExecutionStatus.MATERIALIZING,
            ComfyUIExecutionStatus.CANCELING,
            ComfyUIExecutionStatus.FAILED,
            ComfyUIExecutionStatus.NEEDS_ATTENTION,
        }
    ),
    ComfyUIExecutionStatus.MATERIALIZING: frozenset(
        {ComfyUIExecutionStatus.SUCCESS, ComfyUIExecutionStatus.FAILED}
    ),
    ComfyUIExecutionStatus.CANCELING: frozenset(
        {
            ComfyUIExecutionStatus.CANCELED,
            ComfyUIExecutionStatus.QUEUED,
            ComfyUIExecutionStatus.RUNNING,
            ComfyUIExecutionStatus.MATERIALIZING,
            ComfyUIExecutionStatus.FAILED,
            ComfyUIExecutionStatus.NEEDS_ATTENTION,
        }
    ),
    ComfyUIExecutionStatus.SUCCESS: frozenset(),
    ComfyUIExecutionStatus.FAILED: frozenset(),
    ComfyUIExecutionStatus.CANCELED: frozenset(),
    ComfyUIExecutionStatus.NEEDS_ATTENTION: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ComfyUIInstance:
    id: str
    name: str
    base_url: str
    enabled: bool
    health: ComfyUIHealth
    version: str | None
    python_version: str | None
    capabilities: Mapping[str, object]
    node_catalog_hash: str | None
    last_error_code: str | None
    last_checked_at: datetime | None
    row_version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not self.id or not name or len(name) > 160:
            raise ValueError("ComfyUI instance identity is invalid")
        if self.row_version < 1:
            raise ValueError("ComfyUI instance row_version must be positive")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "base_url", normalize_comfyui_base_url(self.base_url))
        object.__setattr__(self, "capabilities", freeze_mapping(self.capabilities))
        _require_aware(self.created_at)
        _require_aware(self.updated_at)
        for value in (self.last_checked_at, self.deleted_at):
            if value is not None:
                _require_aware(value)

    def with_probe(
        self,
        *,
        health: ComfyUIHealth,
        version: str | None,
        python_version: str | None,
        capabilities: Mapping[str, object],
        node_catalog_hash: str | None,
        error_code: str | None,
        checked_at: datetime,
    ) -> ComfyUIInstance:
        _require_aware(checked_at)
        return replace(
            self,
            health=health,
            version=version,
            python_version=python_version,
            capabilities=capabilities,
            node_catalog_hash=node_catalog_hash,
            last_error_code=error_code,
            last_checked_at=checked_at,
            updated_at=checked_at,
            row_version=self.row_version + 1,
        )


@dataclass(frozen=True, slots=True)
class ComfyUITemplate:
    id: str
    instance_id: str
    name: str
    prompt: Mapping[str, object]
    checksum: str
    input_schema: Mapping[str, object]
    input_targets: Mapping[str, object]
    output_nodes: tuple[str, ...]
    row_version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not self.id or not self.instance_id or not name or len(name) > 160:
            raise ValueError("ComfyUI template identity is invalid")
        if len(self.checksum) != _SHA256_LENGTH or any(
            char not in "0123456789abcdef" for char in self.checksum
        ):
            raise ValueError("ComfyUI template checksum is invalid")
        if self.row_version < 1:
            raise ValueError("ComfyUI template row_version must be positive")
        validate_api_prompt(self.prompt)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "prompt", freeze_mapping(self.prompt))
        object.__setattr__(self, "input_schema", freeze_mapping(self.input_schema))
        object.__setattr__(self, "input_targets", freeze_mapping(self.input_targets))
        object.__setattr__(self, "output_nodes", tuple(self.output_nodes))
        _require_aware(self.created_at)
        _require_aware(self.updated_at)


@dataclass(frozen=True, slots=True)
class ComfyUIExecution:
    id: str
    node_run_id: str
    instance_id: str
    template_id: str | None
    template_checksum: str
    workflow_checksum: str
    prompt: Mapping[str, object]
    output_nodes: tuple[str, ...]
    client_id: str
    status: ComfyUIExecutionStatus
    remote_prompt_id: str | None
    progress: int | None
    output: Mapping[str, object] | None
    artifact_ids: tuple[str, ...]
    error_code: str | None
    error_message: str | None
    poll_after_at: datetime | None
    timeout_at: datetime
    row_version: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not all(
            (
                self.id,
                self.node_run_id,
                self.instance_id,
                self.template_checksum,
                self.workflow_checksum,
                self.client_id,
            )
        ):
            raise ValueError("ComfyUI execution identity is invalid")
        for checksum in (self.template_checksum, self.workflow_checksum):
            if len(checksum) != _SHA256_LENGTH or any(
                char not in "0123456789abcdef" for char in checksum
            ):
                raise ValueError("ComfyUI execution checksum is invalid")
        if self.row_version < 1:
            raise ValueError("ComfyUI execution row_version must be positive")
        if not self.output_nodes or any(not value for value in self.output_nodes):
            raise ValueError("ComfyUI execution requires output nodes")
        if self.progress is not None and not 0 <= self.progress <= 100:
            raise ValueError("ComfyUI execution progress is invalid")
        if (
            self.status
            in {
                ComfyUIExecutionStatus.QUEUED,
                ComfyUIExecutionStatus.RUNNING,
                ComfyUIExecutionStatus.MATERIALIZING,
                ComfyUIExecutionStatus.SUCCESS,
                ComfyUIExecutionStatus.CANCELING,
            }
            and not self.remote_prompt_id
        ):
            raise ValueError("remote ComfyUI state requires prompt_id")
        if self.status is ComfyUIExecutionStatus.SUCCESS and self.output is None:
            raise ValueError("successful ComfyUI execution requires output")
        if self.status.terminal and self.completed_at is None:
            raise ValueError("terminal ComfyUI execution requires completed_at")
        if not self.status.terminal and self.completed_at is not None:
            raise ValueError("non-terminal ComfyUI execution cannot have completed_at")
        object.__setattr__(self, "prompt", freeze_mapping(self.prompt))
        object.__setattr__(self, "output_nodes", tuple(dict.fromkeys(self.output_nodes)))
        if self.output is not None:
            object.__setattr__(self, "output", freeze_mapping(self.output))
        object.__setattr__(self, "artifact_ids", tuple(self.artifact_ids))
        for value in (
            self.poll_after_at,
            self.timeout_at,
            self.created_at,
            self.updated_at,
            self.started_at,
            self.completed_at,
        ):
            if value is not None:
                _require_aware(value)

    def transition(
        self,
        target: ComfyUIExecutionStatus,
        at: datetime,
        *,
        remote_prompt_id: str | None = None,
        progress: int | None = None,
        output: Mapping[str, object] | None = None,
        artifact_ids: tuple[str, ...] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        poll_after_at: datetime | None = None,
    ) -> ComfyUIExecution:
        _require_aware(at)
        if target not in _EXECUTION_TRANSITIONS[self.status]:
            raise ComfyUITransitionError(
                f"invalid ComfyUI execution transition: {self.status} -> {target}"
            )
        effective_remote_id = remote_prompt_id or self.remote_prompt_id
        if (
            target
            in {
                ComfyUIExecutionStatus.QUEUED,
                ComfyUIExecutionStatus.RUNNING,
                ComfyUIExecutionStatus.MATERIALIZING,
                ComfyUIExecutionStatus.SUCCESS,
                ComfyUIExecutionStatus.CANCELING,
            }
            and not effective_remote_id
        ):
            raise ComfyUITransitionError(f"{target} requires remote prompt ID")
        effective_output = self.output if output is None else output
        if target is ComfyUIExecutionStatus.SUCCESS and effective_output is None:
            raise ComfyUITransitionError("SUCCESS requires output")
        effective_progress = self.progress if progress is None else progress
        if target is ComfyUIExecutionStatus.SUCCESS:
            effective_progress = 100
        return replace(
            self,
            status=target,
            remote_prompt_id=effective_remote_id,
            progress=effective_progress,
            output=effective_output,
            artifact_ids=self.artifact_ids if artifact_ids is None else artifact_ids,
            error_code=error_code,
            error_message=error_message,
            poll_after_at=poll_after_at,
            started_at=at
            if target is ComfyUIExecutionStatus.SUBMITTING and self.started_at is None
            else self.started_at,
            completed_at=at if target.terminal else None,
            updated_at=at,
            row_version=self.row_version + 1,
        )

    def refresh(
        self,
        at: datetime,
        *,
        progress: int | None = None,
        poll_after_at: datetime | None = None,
    ) -> ComfyUIExecution:
        if self.status.terminal:
            raise ComfyUITransitionError("terminal ComfyUI execution cannot be refreshed")
        _require_aware(at)
        effective_progress = self.progress if progress is None else progress
        return replace(
            self,
            progress=effective_progress,
            poll_after_at=poll_after_at,
            updated_at=at,
            row_version=self.row_version + 1,
        )


def normalize_comfyui_base_url(value: str) -> str:
    text = value.strip()
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("ComfyUI URL must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("ComfyUI URL must not contain userinfo")
    if parsed.query or parsed.fragment:
        raise ValueError("ComfyUI URL must not contain query or fragment")
    host = parsed.hostname.lower()
    if parsed.scheme == "http" and host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("plain HTTP is restricted to loopback ComfyUI instances")
    default_port = 80 if parsed.scheme == "http" else 443
    port = parsed.port
    display_host = f"[{host}]" if ":" in host else host
    netloc = display_host if port in {None, default_port} else f"{display_host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def validate_api_prompt(prompt: Mapping[str, object]) -> None:
    if not prompt:
        raise ValueError("ComfyUI API prompt must contain at least one node")
    for node_id, value in prompt.items():
        if not isinstance(node_id, str) or not node_id or not isinstance(value, Mapping):
            raise ValueError("ComfyUI API prompt node is invalid")
        class_type = value.get("class_type")
        inputs = value.get("inputs")
        if not isinstance(class_type, str) or not class_type or not isinstance(inputs, Mapping):
            raise ValueError("ComfyUI API prompt node requires class_type and inputs")


def comfyui_prompt_checksum(prompt: Mapping[str, object]) -> str:
    validate_api_prompt(prompt)
    encoded = json.dumps(
        _plain_json(prompt),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def patch_api_prompt(
    prompt: Mapping[str, object],
    targets: Mapping[str, object],
    inputs: Mapping[str, object],
) -> Mapping[str, object]:
    copied = _plain_json(prompt)
    if not isinstance(copied, dict):  # pragma: no cover - Mapping always normalizes to dict
        raise ValueError("ComfyUI API prompt must be an object")
    for port, value in inputs.items():
        target = targets.get(port)
        if not isinstance(target, Mapping):
            raise ValueError(f"ComfyUI input target is missing: {port}")
        node_id = target.get("node_id")
        input_name = target.get("input_name")
        if not isinstance(node_id, str) or not isinstance(input_name, str):
            raise ValueError(f"ComfyUI input target is invalid: {port}")
        node = copied.get(node_id)
        if not isinstance(node, dict):
            raise ValueError(f"ComfyUI target node does not exist: {node_id}")
        node_inputs = node.get("inputs")
        if not isinstance(node_inputs, dict) or input_name not in node_inputs:
            raise ValueError(f"ComfyUI target input does not exist: {node_id}.{input_name}")
        node_inputs[input_name] = _plain_json(value)
    return copied


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json(child) for child in value]
    return value


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
