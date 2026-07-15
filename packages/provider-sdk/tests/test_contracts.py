"""Provider contract-kit validation tests."""

from __future__ import annotations

import pytest

from astraweft_provider_sdk import (
    ProviderDescriptor,
    SchemaContractError,
    assert_descriptor_matches_manifest,
    validate_instance,
    validate_schema,
)
from astraweft_provider_sdk.manifest import (
    ManifestCapabilities,
    PluginManifest,
    PluginPermissions,
    PluginPublisher,
)


def _descriptor() -> ProviderDescriptor:
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    return ProviderDescriptor(
        plugin_id="com.example.provider",
        name="Example",
        version="1.0.0",
        plugin_api=">=1.0,<2.0",
        description="Example",
        operations=frozenset({"text.generate"}),
        supports_async_tasks=False,
        supports_cancel=False,
        supports_model_discovery=True,
        supports_usage=True,
        default_endpoint=None,
        settings_schema=schema,
        settings_ui_schema={},
        credential_schema={"type": "object"},
    )


def _manifest() -> PluginManifest:
    return PluginManifest(
        manifest_version=1,
        plugin_id="com.example.provider",
        name="Example",
        version="1.0.0",
        plugin_api=">=1.0,<2.0",
        python=">=3.12,<3.14",
        entry_point="example.plugin:Plugin",
        description="Example",
        homepage=None,
        license="Apache-2.0",
        publisher=PluginPublisher("Example"),
        permissions=PluginPermissions(),
        capabilities=ManifestCapabilities(
            operations=frozenset({"text.generate"}),
            model_discovery=True,
            usage=True,
        ),
    )


def test_schema_validation_uses_draft_2020_and_rejects_remote_refs() -> None:
    validate_schema({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"})
    validate_instance({"name": "valid"}, _descriptor().settings_schema)
    with pytest.raises(SchemaContractError, match="name"):
        validate_instance({"name": 42}, _descriptor().settings_schema)
    with pytest.raises(SchemaContractError, match="remote"):
        validate_schema({"$ref": "https://example.com/schema.json"})
    with pytest.raises(SchemaContractError, match="invalid"):
        validate_schema({"type": "not-a-json-schema-type"})


def test_descriptor_manifest_mismatch_is_actionable() -> None:
    assert_descriptor_matches_manifest(_descriptor(), _manifest())
    descriptor = _descriptor()
    changed = ProviderDescriptor(
        plugin_id=descriptor.plugin_id,
        name="Different",
        version=descriptor.version,
        plugin_api=descriptor.plugin_api,
        description=descriptor.description,
        operations=descriptor.operations,
        supports_async_tasks=descriptor.supports_async_tasks,
        supports_cancel=descriptor.supports_cancel,
        supports_model_discovery=descriptor.supports_model_discovery,
        supports_usage=descriptor.supports_usage,
        default_endpoint=None,
        settings_schema=descriptor.settings_schema,
        settings_ui_schema=descriptor.settings_ui_schema,
        credential_schema=descriptor.credential_schema,
    )
    with pytest.raises(AssertionError, match="name"):
        assert_descriptor_matches_manifest(changed, _manifest())
