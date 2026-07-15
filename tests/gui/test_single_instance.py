"""Cross-process lock and local activation channel GUI tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from astraweft.bootstrap.single_instance import InstanceOutcome, SingleInstanceCoordinator


@pytest.mark.gui
def test_second_instance_notifies_primary_and_lock_is_released(
    qtbot: QtBot, tmp_path: Path
) -> None:
    primary = SingleInstanceCoordinator(tmp_path / "cache", tmp_path / "data")
    secondary = SingleInstanceCoordinator(tmp_path / "cache", tmp_path / "data")
    activated: list[bool] = []
    try:
        assert primary.start() is InstanceOutcome.PRIMARY
        assert primary.start() is InstanceOutcome.PRIMARY
        primary.set_activation_handler(lambda: activated.append(True))
        assert secondary.start() is InstanceOutcome.NOTIFIED_EXISTING
        qtbot.waitUntil(lambda: bool(activated), timeout=2000)
        assert activated == [True]
    finally:
        secondary.close()
        primary.close()

    replacement = SingleInstanceCoordinator(tmp_path / "cache", tmp_path / "data")
    try:
        assert replacement.start() is InstanceOutcome.PRIMARY
    finally:
        replacement.close()


@pytest.mark.gui
def test_activation_waits_until_window_handler_exists(qtbot: QtBot, tmp_path: Path) -> None:
    primary = SingleInstanceCoordinator(tmp_path / "cache", tmp_path / "data")
    secondary = SingleInstanceCoordinator(tmp_path / "cache", tmp_path / "data")
    activated: list[bool] = []
    try:
        assert primary.start() is InstanceOutcome.PRIMARY
        assert secondary.start() is InstanceOutcome.NOTIFIED_EXISTING
        qtbot.wait(20)
        primary.set_activation_handler(lambda: activated.append(True))
        assert activated == [True]
    finally:
        secondary.close()
        primary.close()
