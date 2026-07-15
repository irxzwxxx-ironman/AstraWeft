"""Runway Provider plugin factory."""

from __future__ import annotations

from collections.abc import Mapping

from astraweft_provider_sdk import ProviderClient, ProviderContext, ProviderDescriptor
from astraweft_runway_provider.client import RunwayProviderClient
from astraweft_runway_provider.schemas import (
    CREDENTIAL_SCHEMA,
    SETTINGS_SCHEMA,
    SETTINGS_UI_SCHEMA,
)


class RunwayProviderPlugin:
    """Remote asynchronous text-to-video Provider."""

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            plugin_id="com.runwayml.api-provider",
            name="Runway",
            version="0.1.0.dev0",
            plugin_api=">=1.0,<2.0",
            description="Runway asynchronous text-to-video Provider",
            operations=frozenset({"video.generate"}),
            supports_async_tasks=True,
            supports_cancel=True,
            supports_model_discovery=True,
            supports_usage=False,
            default_endpoint="https://api.dev.runwayml.com/v1",
            settings_schema=SETTINGS_SCHEMA,
            settings_ui_schema=SETTINGS_UI_SCHEMA,
            credential_schema=CREDENTIAL_SCHEMA,
            redaction_paths=("$.credentials.api_key", "$.headers.authorization"),
            idempotency="none",
            progress="exact",
            supports_streaming=False,
            client_concurrency=2,
        )

    def create_client(
        self,
        context: ProviderContext,
        settings: Mapping[str, object],
        credential_ref: str | None,
    ) -> ProviderClient:
        return RunwayProviderClient(context, settings, credential_ref)

    def migrate_settings(
        self,
        from_version: str,
        settings: Mapping[str, object],
    ) -> Mapping[str, object]:
        del from_version
        return dict(settings)
