"""Task command boundary validation."""

from collections.abc import Callable

import pytest

from astraweft.application.tasks import CreateTask


@pytest.mark.parametrize(
    "command",
    [
        lambda: CreateTask("", "model", "text.generate", {}),
        lambda: CreateTask("provider", "model", "text.generate", {}, priority=-1),
        lambda: CreateTask("provider", "model", "text.generate", {}, timeout_seconds=0),
        lambda: CreateTask("provider", "model", "text.generate", {}, timeout_seconds=86_401),
        lambda: CreateTask("provider", "model", "text.generate", {}, task_id="  "),
    ],
)
def test_create_task_rejects_invalid_scheduling_boundaries(
    command: Callable[[], CreateTask],
) -> None:
    with pytest.raises(ValueError):
        command()
