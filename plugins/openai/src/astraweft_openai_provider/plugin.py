"""OpenAI Provider plugin factory."""

from __future__ import annotations

from collections.abc import Mapping

from astraweft_openai_provider.client import OpenAIProviderClient
from astraweft_openai_provider.schemas import (
    CREDENTIAL_SCHEMA,
    SETTINGS_SCHEMA,
    SETTINGS_UI_SCHEMA,
)
from astraweft_provider_sdk import ProviderClient, ProviderContext, ProviderDescriptor


class OpenAIProviderPlugin:
    """Synchronous Responses API text Provider."""

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            plugin_id="com.openai.api-provider",
            name="OpenAI",
            version="0.1.0.dev0",
            plugin_api=">=1.0,<2.0",
            description="OpenAI Responses API text generation Provider",
            operations=frozenset({"text.generate"}),
            supports_async_tasks=False,
            supports_cancel=False,
            supports_model_discovery=True,
            supports_usage=True,
            default_endpoint="https://api.openai.com/v1",
            settings_schema=SETTINGS_SCHEMA,
            settings_ui_schema=SETTINGS_UI_SCHEMA,
            credential_schema=CREDENTIAL_SCHEMA,
            redaction_paths=("$.credentials.api_key", "$.headers.authorization"),
            idempotency="none",
            progress="none",
            supports_streaming=False,
            client_concurrency=4,
        )

    def create_client(
        self,
        context: ProviderContext,
        settings: Mapping[str, object],
        credential_ref: str | None,
    ) -> ProviderClient:
        return OpenAIProviderClient(context, settings, credential_ref)

    def migrate_settings(
        self,
        from_version: str,
        settings: Mapping[str, object],
    ) -> Mapping[str, object]:
        del from_version
        return dict(settings)
