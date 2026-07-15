"""Reusable Provider schema and baseline contract checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from astraweft_provider_sdk._json import thaw_json
from astraweft_provider_sdk.manifest import PluginManifest
from astraweft_provider_sdk.protocols import ProviderPlugin
from astraweft_provider_sdk.types import ProviderContext, ProviderDescriptor, ProviderModel


class SchemaContractError(ValueError):
    """A plugin Schema is invalid or unsafe for local validation."""


def validate_schema(schema: Mapping[str, object]) -> None:
    """Validate Draft 2020-12 and reject remote references."""
    normalized = cast(Mapping[str, Any], thaw_json(schema))
    try:
        Draft202012Validator.check_schema(normalized)
    except SchemaError as exc:
        raise SchemaContractError(f"invalid JSON Schema: {exc.message}") from exc
    for value in _walk(normalized):
        if isinstance(value, Mapping):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith(("http://", "https://")):
                raise SchemaContractError("remote $ref is not allowed")


def validate_instance(instance: object, schema: Mapping[str, object]) -> None:
    validate_schema(schema)
    try:
        normalized = cast(Mapping[str, Any], thaw_json(schema))
        Draft202012Validator(normalized).validate(thaw_json(instance))
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path) or "$"
        raise SchemaContractError(f"{path}: {exc.message}") from exc


def assert_descriptor_matches_manifest(
    descriptor: ProviderDescriptor,
    manifest: PluginManifest,
) -> None:
    mismatches = [
        field
        for field in ("plugin_id", "name", "version", "plugin_api")
        if getattr(descriptor, field) != getattr(manifest, field)
    ]
    if descriptor.operations != manifest.capabilities.operations:
        mismatches.append("operations")
    capability_pairs = (
        (descriptor.supports_async_tasks, manifest.capabilities.async_tasks, "async_tasks"),
        (descriptor.supports_cancel, manifest.capabilities.cancel, "cancel"),
        (
            descriptor.supports_model_discovery,
            manifest.capabilities.model_discovery,
            "model_discovery",
        ),
        (descriptor.supports_usage, manifest.capabilities.usage, "usage"),
    )
    mismatches.extend(name for runtime, static, name in capability_pairs if runtime != static)
    if mismatches:
        raise AssertionError(f"manifest/descriptor mismatch: {', '.join(mismatches)}")


@dataclass(frozen=True, slots=True)
class ContractCheck:
    name: str
    passed: bool


class ProviderContractSuite:
    """Baseline contract runner shared by every Provider package."""

    @staticmethod
    async def run_baseline(
        plugin: ProviderPlugin,
        manifest: PluginManifest,
        context: ProviderContext,
        *,
        settings: Mapping[str, object],
        credential_ref: str | None,
    ) -> tuple[ContractCheck, ...]:
        descriptor = plugin.descriptor
        assert_descriptor_matches_manifest(descriptor, manifest)
        for schema in (
            descriptor.settings_schema,
            descriptor.settings_ui_schema,
            descriptor.credential_schema,
        ):
            validate_schema(schema)
        validate_instance(settings, descriptor.settings_schema)

        client = plugin.create_client(context, settings, credential_ref)
        try:
            health = await client.health_check()
            if health.status not in ("healthy", "degraded", "unavailable"):
                raise AssertionError("health check returned an unknown status")
            models = await client.list_models()
            ids = [model.remote_model_id for model in models]
            if len(ids) != len(set(ids)):
                raise AssertionError("model remote IDs must be unique")
            for model in models:
                _validate_model(model, descriptor)
        finally:
            await client.close()
            await client.close()

        return (
            ContractCheck("manifest_descriptor", True),
            ContractCheck("schemas", True),
            ContractCheck("health", True),
            ContractCheck("models", True),
            ContractCheck("close_idempotent", True),
        )


def _validate_model(model: ProviderModel, descriptor: ProviderDescriptor) -> None:
    if not model.operations <= descriptor.operations:
        raise AssertionError("model declares an operation absent from the descriptor")
    validate_schema(model.parameter_schema)
    validate_schema(model.parameter_ui_schema)
    validate_schema(model.output_schema)


def _walk(value: object) -> Sequence[object]:
    found: list[object] = [value]
    if isinstance(value, Mapping):
        for child in value.values():
            found.extend(_walk(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            found.extend(_walk(child))
    return found
