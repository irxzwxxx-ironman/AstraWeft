"""Public Provider SDK contract applied to the OpenAI plugin offline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from astraweft_openai_provider import OpenAIProviderPlugin
from astraweft_provider_sdk import ProviderContext, ProviderContractSuite, load_manifest


@pytest.mark.contract
@pytest.mark.asyncio
async def test_openai_provider_passes_public_baseline_contract(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    models = {"object": "list", "data": [{"id": "gpt-5-mini", "object": "model"}]}
    fake_http.queue(200, models, headers={"x-request-id": "req_health"})
    fake_http.queue(200, models, headers={"x-request-id": "req_models"})
    manifest_path = Path(__file__).parents[1] / "src" / "astraweft_openai_provider" / "plugin.toml"

    checks = await ProviderContractSuite.run_baseline(
        OpenAIProviderPlugin(),
        load_manifest(manifest_path),
        provider_context,
        settings={"request_timeout_seconds": 30},
        credential_ref="credential-ref",
    )

    assert all(check.passed for check in checks)
    assert [request.url for request in fake_http.requests] == [
        "https://api.openai.com/v1/models",
        "https://api.openai.com/v1/models",
    ]
