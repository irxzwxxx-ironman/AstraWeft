"""Public Provider SDK contract applied to Runway offline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from astraweft_provider_sdk import ProviderContext, ProviderContractSuite, load_manifest
from astraweft_runway_provider import RunwayProviderPlugin


@pytest.mark.contract
@pytest.mark.asyncio
async def test_runway_provider_passes_public_baseline_contract(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    fake_http.queue(200, {"creditBalance": 100, "tier": {}, "usage": {}})
    manifest_path = Path(__file__).parents[1] / "src" / "astraweft_runway_provider" / "plugin.toml"

    checks = await ProviderContractSuite.run_baseline(
        RunwayProviderPlugin(),
        load_manifest(manifest_path),
        provider_context,
        settings={"request_timeout_seconds": 30, "poll_interval_seconds": 5},
        credential_ref="credential-ref",
    )

    assert all(check.passed for check in checks)
    assert [request.url for request in fake_http.requests] == [
        "https://api.dev.runwayml.com/v1/organization"
    ]
