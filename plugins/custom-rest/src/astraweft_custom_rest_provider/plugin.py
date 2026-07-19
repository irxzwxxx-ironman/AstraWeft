"""Custom REST Provider factory."""

from __future__ import annotations

from collections.abc import Mapping

from astraweft_custom_rest_provider.client import CustomRestProviderClient
from astraweft_custom_rest_provider.schemas import (
    CREDENTIAL_SCHEMA,
    SETTINGS_SCHEMA,
    SETTINGS_UI_SCHEMA,
)
from astraweft_provider_sdk import ProviderClient, ProviderContext, ProviderDescriptor


class CustomRestProviderPlugin:
    """Expose user-defined HTTPS JSON APIs through AstraWeft's stable task gateway."""

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            plugin_id="dev.astraweft.custom-rest-provider",
            name="Custom REST / JSON",
            version="0.1.0.dev0",
            plugin_api=">=1.0,<2.0",
            description=(
                "Map multiple custom HTTPS JSON APIs into the AstraWeft/ComfyUI local gateway; "
                "API keys remain in AstraWeft's credential store."
            ),
            operations=frozenset(
                {
                    "text.generate",
                    "image.generate",
                    "video.generate",
                    "audio.generate",
                    "custom.invoke",
                }
            ),
            supports_async_tasks=True,
            supports_cancel=True,
            supports_model_discovery=True,
            supports_usage=True,
            default_endpoint=None,
            endpoint_required=True,
            settings_schema=SETTINGS_SCHEMA,
            settings_ui_schema=SETTINGS_UI_SCHEMA,
            credential_schema=CREDENTIAL_SCHEMA,
            redaction_paths=(
                "$.credentials.api_key",
                "$.credentials.api_secret",
                "$.credentials.username",
                "$.credentials.password",
                "$.headers.authorization",
                "$.query.api_key",
            ),
            idempotency="none",
            progress="estimated",
            supports_streaming=False,
            client_concurrency=4,
        )

    def create_client(
        self,
        context: ProviderContext,
        settings: Mapping[str, object],
        credential_ref: str | None,
    ) -> ProviderClient:
        return CustomRestProviderClient(context, settings, credential_ref)

    def migrate_settings(
        self,
        from_version: str,
        settings: Mapping[str, object],
    ) -> Mapping[str, object]:
        del from_version
        return dict(settings)
