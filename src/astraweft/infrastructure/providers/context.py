"""Adapters for the capabilities Core injects into Provider plugins."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from astraweft.infrastructure.network import CoreHttpClient, RestrictedHttpTransport
from astraweft.ports.runtime import Clock
from astraweft.ports.secrets import SecretStore
from astraweft_provider_sdk import ProviderContext
from astraweft_provider_sdk import (
    SecretValue as PluginSecretValue,
)


class CoreSecretResolver:
    def __init__(self, store: SecretStore) -> None:
        self._store = store

    async def get(self, credential_ref: str, field: str) -> PluginSecretValue:
        value = await self._store.get(credential_ref, field)
        return PluginSecretValue(value.reveal())


class CorePluginLogger:
    def __init__(self, plugin_id: str) -> None:
        self._logger = logging.getLogger(f"astraweft.plugins.{plugin_id}")

    def debug(self, message: str, **context: object) -> None:
        self._logger.debug(message, extra={"plugin_context": context})

    def info(self, message: str, **context: object) -> None:
        self._logger.info(message, extra={"plugin_context": context})

    def warning(self, message: str, **context: object) -> None:
        self._logger.warning(message, extra={"plugin_context": context})

    def error(self, message: str, **context: object) -> None:
        self._logger.error(message, extra={"plugin_context": context})


class RestrictedPluginDataDirectory:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, relative_path: str) -> Path:
        candidate = PurePosixPath(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("plugin data path must stay inside its assigned directory")
        resolved = (self._root / Path(*candidate.parts)).resolve()
        if not resolved.is_relative_to(self._root):
            raise ValueError("plugin data path escaped its assigned directory")
        return resolved


def build_provider_context(
    *,
    plugin_id: str,
    secret_store: SecretStore,
    clock: Clock,
    plugin_data_root: Path,
    core_version: str,
    plugin_api_version: str,
    http_client: CoreHttpClient,
    allowed_network: tuple[str, ...],
    endpoint: str | None = None,
) -> ProviderContext:
    readable = plugin_id.replace(".", "_").replace("-", "_")[:64]
    suffix = hashlib.sha256(plugin_id.encode()).hexdigest()[:12]
    safe_directory = f"{readable}_{suffix}"
    return ProviderContext(
        http=RestrictedHttpTransport(http_client, allowed_network),
        secrets=CoreSecretResolver(secret_store),
        logger=CorePluginLogger(plugin_id),
        clock=clock,
        plugin_data=RestrictedPluginDataDirectory(plugin_data_root / safe_directory),
        core_version=core_version,
        plugin_api_version=plugin_api_version,
        endpoint=endpoint,
    )


@dataclass(frozen=True, slots=True)
class CoreProviderContextFactory:
    secret_store: SecretStore
    clock: Clock
    plugin_data_root: Path
    core_version: str
    plugin_api_version: str
    http_client: CoreHttpClient

    def __call__(
        self,
        plugin_id: str,
        allowed_network: tuple[str, ...],
        endpoint: str | None,
    ) -> ProviderContext:
        return build_provider_context(
            plugin_id=plugin_id,
            secret_store=self.secret_store,
            clock=self.clock,
            plugin_data_root=self.plugin_data_root,
            core_version=self.core_version,
            plugin_api_version=self.plugin_api_version,
            http_client=self.http_client,
            allowed_network=allowed_network,
            endpoint=endpoint,
        )
