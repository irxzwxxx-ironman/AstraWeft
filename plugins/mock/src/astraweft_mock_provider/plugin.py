"""Mock Provider plugin factory."""

from __future__ import annotations

from collections.abc import Mapping

from astraweft_mock_provider.client import MockProviderClient
from astraweft_mock_provider.schemas import (
    CREDENTIAL_SCHEMA,
    SETTINGS_SCHEMA,
    SETTINGS_UI_SCHEMA,
)
from astraweft_provider_sdk import ProviderClient, ProviderContext, ProviderDescriptor


class MockProviderPlugin:
    """Zero-network Provider used to prove the public plugin boundary."""

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            plugin_id="dev.astraweft.mock-provider",
            name="AstraWeft Mock Provider",
            version="0.1.0.dev0",
            plugin_api=">=1.0,<2.0",
            description="Deterministic zero-network Provider for development and contract tests",
            operations=frozenset({"text.generate", "image.generate", "video.generate"}),
            supports_async_tasks=True,
            supports_cancel=True,
            supports_model_discovery=True,
            supports_usage=True,
            default_endpoint=None,
            settings_schema=SETTINGS_SCHEMA,
            settings_ui_schema=SETTINGS_UI_SCHEMA,
            credential_schema=CREDENTIAL_SCHEMA,
            redaction_paths=("$.credentials.api_key",),
            idempotency="native",
            progress="exact",
            client_concurrency=4,
        )

    def create_client(
        self,
        context: ProviderContext,
        settings: Mapping[str, object],
        credential_ref: str | None,
    ) -> ProviderClient:
        return MockProviderClient(context, settings, credential_ref)

    def migrate_settings(
        self,
        from_version: str,
        settings: Mapping[str, object],
    ) -> Mapping[str, object]:
        del from_version
        return dict(settings)
