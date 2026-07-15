"""Stable, safe Provider exception hierarchy."""

from __future__ import annotations

from collections.abc import Mapping

from astraweft_provider_sdk._json import freeze_mapping
from astraweft_provider_sdk.types import ProviderCallInfo


class ProviderError(Exception):
    """User-safe Provider failure with structured retry semantics."""

    default_code = "provider_error"
    default_retryable = False

    def __init__(
        self,
        user_message: str,
        *,
        technical_message: str = "",
        code: str | None = None,
        retryable: bool | None = None,
        retry_after_seconds: float | None = None,
        provider_code: str | None = None,
        provider_request_id: str | None = None,
        call: ProviderCallInfo | None = None,
        safe_details: Mapping[str, object] | None = None,
    ) -> None:
        if not user_message.strip():
            raise ValueError("user_message must not be empty")
        if retry_after_seconds is not None and retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be non-negative")
        super().__init__(user_message)
        self.code = code or self.default_code
        self.user_message = user_message
        self.technical_message = technical_message
        self.retryable = self.default_retryable if retryable is None else retryable
        self.retry_after_seconds = retry_after_seconds
        self.provider_code = provider_code
        if (
            call is not None
            and provider_request_id is not None
            and call.provider_request_id != provider_request_id
        ):
            raise ValueError("error and call request IDs must match")
        self.provider_request_id = (
            call.provider_request_id
            if provider_request_id is None and call is not None
            else provider_request_id
        )
        self.call = call
        self.safe_details = freeze_mapping(safe_details or {})

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, retryable={self.retryable!r})"


class ProviderValidationError(ProviderError):
    default_code = "validation_error"


class ProviderAuthenticationError(ProviderError):
    default_code = "authentication_error"


class ProviderPermissionError(ProviderError):
    default_code = "permission_error"


class ProviderRateLimitError(ProviderError):
    default_code = "rate_limit"
    default_retryable = True


class ProviderUnavailableError(ProviderError):
    default_code = "unavailable"
    default_retryable = True


class ProviderNetworkError(ProviderError):
    default_code = "network_error"
    default_retryable = True


class ProviderTimeoutError(ProviderError):
    default_code = "timeout"
    default_retryable = True


class ProviderTaskFailedError(ProviderError):
    default_code = "task_failed"


class ProviderProtocolError(ProviderError):
    default_code = "protocol_error"


class UnsupportedOperationError(ProviderError):
    default_code = "unsupported_operation"


class PluginConfigurationError(ProviderError):
    default_code = "plugin_configuration_error"
