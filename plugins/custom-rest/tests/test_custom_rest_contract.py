"""Public Provider SDK baseline contract for Custom REST."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from astraweft_custom_rest_provider import CustomRestProviderPlugin
from astraweft_provider_sdk import ProviderContext, ProviderContractSuite, load_manifest


@pytest.mark.contract
@pytest.mark.asyncio
async def test_custom_rest_provider_passes_public_baseline_contract(
    provider_context: ProviderContext,
    fake_http: Any,
    settings: Mapping[str, object],
) -> None:
    fake_http.queue(200, {"ok": True})
    manifest_path = (
        Path(__file__).parents[1] / "src" / "astraweft_custom_rest_provider" / "plugin.toml"
    )

    checks = await ProviderContractSuite.run_baseline(
        CustomRestProviderPlugin(),
        load_manifest(manifest_path),
        provider_context,
        settings=settings,
        credential_ref="credential-ref",
    )

    assert all(check.passed for check in checks)
