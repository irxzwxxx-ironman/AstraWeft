"""Public Provider SDK contract applied to the independent Mock plugin."""

from __future__ import annotations

from pathlib import Path

import pytest

from astraweft_mock_provider import MockProviderPlugin
from astraweft_provider_sdk import ProviderContext, ProviderContractSuite, load_manifest


@pytest.mark.contract
@pytest.mark.asyncio
async def test_mock_provider_passes_public_baseline_contract(
    provider_context: ProviderContext,
) -> None:
    manifest_path = Path(__file__).parents[1] / "src" / "astraweft_mock_provider" / "plugin.toml"
    manifest = load_manifest(manifest_path)

    checks = await ProviderContractSuite.run_baseline(
        MockProviderPlugin(),
        manifest,
        provider_context,
        settings={"mode": "healthy", "catalog_revision": 1},
        credential_ref="credential-ref",
    )

    assert all(check.passed for check in checks)
    assert {check.name for check in checks} == {
        "manifest_descriptor",
        "schemas",
        "health",
        "models",
        "close_idempotent",
    }
