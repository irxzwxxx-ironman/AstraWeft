"""Artifact metadata and files move through a recoverable trash lifecycle."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from astraweft.application.providers import CreateProvider
from astraweft.application.tasks import (
    ArtifactLifecycleError,
    ArtifactNotFoundError,
    CreateTask,
)
from astraweft.bootstrap.container import build_app_context
from astraweft.bootstrap.context import AppContext
from astraweft.domain.task import Artifact
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.secrets import SecretValue


async def _create_artifact(context: AppContext) -> Artifact:
    provider = await context.provider_service.create(
        CreateProvider(
            plugin_id="dev.astraweft.mock-provider",
            name="Trash Mock",
            settings={"response_mode": "accepted", "catalog_revision": 2},
            credentials={"api_key": SecretValue("mock-valid-key")},
        )
    )
    models = await context.provider_service.sync_models(provider.id)
    model = next(item for item in models if item.remote_model_id == "mock-video-v1")
    task = await context.task_service.create_and_run(
        CreateTask(
            provider_id=provider.id,
            model_id=model.id,
            operation="video.generate",
            inputs={"prompt": "trash lifecycle"},
        )
    )
    return (await context.task_service.list_artifacts(task.id))[0]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_artifact_trash_restore_and_confirmed_purge(tmp_path: Path) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        artifact = await _create_artifact(context)
        preview = await context.task_service.preview_artifact_trash(artifact.id)

        assert preview.file_exists
        assert preview.task_reference
        assert preview.workflow_reference_count == 0
        assert not preview.can_purge

        trashed = await context.task_service.trash_artifact(artifact.id)
        assert trashed.deleted_at is not None
        assert not (context.paths.artifact_dir / artifact.relative_path).exists()
        assert len(await context.task_service.list_trashed_artifacts()) == 1
        assert not await context.task_service.list_artifacts()
        assert await context.task_service.trash_artifact(artifact.id) == trashed

        restored = await context.task_service.restore_artifact(artifact.id)
        assert restored.deleted_at is None
        assert (context.paths.artifact_dir / artifact.relative_path).exists()
        assert await context.task_service.restore_artifact(artifact.id) == restored
        with pytest.raises(ArtifactLifecycleError, match="必须先"):
            await context.task_service.purge_artifact(artifact.id, confirm_sha256=artifact.sha256)

        await context.task_service.trash_artifact(artifact.id)
        with pytest.raises(ArtifactLifecycleError, match="不匹配"):
            await context.task_service.purge_artifact(artifact.id, confirm_sha256="0" * 64)
        await context.task_service.purge_artifact(artifact.id, confirm_sha256=artifact.sha256)
        with pytest.raises(ArtifactNotFoundError):
            await context.task_service.preview_artifact_trash(artifact.id)
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_file_and_retention_sweep_are_safe(tmp_path: Path) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        artifact = await _create_artifact(context)
        artifact_path = context.paths.artifact_dir / artifact.relative_path
        artifact_path.unlink()
        with pytest.raises(ArtifactLifecycleError, match="缺失"):
            await context.task_service.trash_artifact(artifact.id)

        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("restored placeholder", encoding="utf-8")
        trashed = await context.task_service.trash_artifact(artifact.id)
        old = replace(trashed, deleted_at=context.clock.now() - timedelta(days=2))
        async with context.task_service._uow_factory() as uow:
            await uow.artifacts.update_deleted_at(old)
            await uow.commit()

        assert await context.task_service.purge_expired_artifacts(retention_days=1) == 1
        assert await context.task_service.purge_expired_artifacts(retention_days=1) == 0
        with pytest.raises(ValueError, match="positive"):
            await context.task_service.purge_expired_artifacts(retention_days=0)

        assert await context.task_service.purge_request_logs(retention_days=0) == 0
        with pytest.raises(ValueError, match="non-negative"):
            await context.task_service.purge_request_logs(retention_days=-1)
        old_timestamp = (context.clock.now() - timedelta(days=100)).isoformat()
        async with context.database.engine.begin() as connection:
            await connection.execute(
                text("UPDATE request_logs SET created_at = :created_at"),
                {"created_at": old_timestamp},
            )
        assert await context.task_service.purge_request_logs(retention_days=90) == 3
        assert not await context.task_service.list_request_logs()
    finally:
        await context.close()
