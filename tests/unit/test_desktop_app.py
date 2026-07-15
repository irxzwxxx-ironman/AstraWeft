"""In-process desktop lifecycle branch tests without opening real Qt windows."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypeVar, cast

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from astraweft.bootstrap import app as desktop
from astraweft.bootstrap.single_instance import InstanceOutcome

ResultT = TypeVar("ResultT")


class FakeSignal:
    def __init__(self) -> None:
        self.handlers: list[object] = []

    def connect(self, handler: object) -> None:
        self.handlers.append(handler)


class FakeApplication:
    current: FakeApplication | None = None

    def __init__(self, _args: object = None) -> None:
        self.aboutToQuit = FakeSignal()
        self.quit_called = False
        type(self).current = self

    @classmethod
    def instance(cls) -> FakeApplication | None:
        return cls.current

    def setApplicationName(self, _value: str) -> None:
        pass

    def setApplicationDisplayName(self, _value: str) -> None:
        pass

    def setOrganizationName(self, _value: str) -> None:
        pass

    def quit(self) -> None:
        self.quit_called = True


class FakeLoop:
    def __init__(self, _application: FakeApplication) -> None:
        self.forever_called = False
        self.stopped = False

    def __enter__(self) -> FakeLoop:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def run_until_complete(self, operation: Coroutine[Any, Any, ResultT]) -> ResultT:
        try:
            operation.send(None)
        except StopIteration as stopped:
            return cast(ResultT, stopped.value)
        raise AssertionError("fake coroutine unexpectedly suspended")

    def run_forever(self) -> None:
        self.forever_called = True

    def stop(self) -> None:
        self.stopped = True


class FakeInstance:
    outcome = InstanceOutcome.PRIMARY
    last: FakeInstance | None = None

    def __init__(self, _cache: Path, _data: Path) -> None:
        self.closed = False
        self.activation_handler: object | None = None
        type(self).last = self

    def start(self) -> InstanceOutcome:
        return self.outcome

    def set_activation_handler(self, handler: object) -> None:
        self.activation_handler = handler

    def close(self) -> None:
        self.closed = True


class FakeRuntime:
    def __init__(self) -> None:
        self.started = False

    def start(self) -> None:
        self.started = True


class FakeContext:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            theme="dark",
            reduce_motion=True,
            system_notifications=False,
        )
        self.provider_service = object()
        self.task_service = object()
        self.workflow_service = object()
        self.workflow_execution = object()
        self.task_runtime = FakeRuntime()
        self.workflow_runtime = FakeRuntime()
        self.closed = False

    def presentation_status(self) -> object:
        return object()

    async def close(self) -> None:
        self.closed = True


class FakeWindow:
    last: FakeWindow | None = None

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.shown = False
        type(self).last = self

    def activate_from_external_instance(self) -> None:
        pass

    def show(self) -> None:
        self.shown = True


def _install_fakes(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeApplication, FakeContext]:
    application = FakeApplication()
    context = FakeContext()
    fake_root = Path.cwd() / "build" / "desktop-app-test"
    paths = SimpleNamespace(
        cache_dir=fake_root / "cache",
        data_dir=fake_root / "data",
        ensure=lambda: None,
    )

    async def build(_root: Path | None) -> FakeContext:
        return context

    monkeypatch.setattr(desktop, "QApplication", FakeApplication)
    monkeypatch.setattr(desktop, "QEventLoop", FakeLoop)
    monkeypatch.setattr(desktop, "SingleInstanceCoordinator", FakeInstance)
    monkeypatch.setattr(desktop, "resolve_app_paths", lambda _root: paths)
    monkeypatch.setattr(desktop, "build_app_context", build)
    monkeypatch.setattr(desktop, "MainWindow", FakeWindow)
    monkeypatch.setattr(desktop, "apply_theme", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(asyncio, "set_event_loop", lambda _loop: None)
    monkeypatch.setattr(
        QTimer,
        "singleShot",
        lambda _delay, callback: callback(),
    )
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    return application, context


def test_run_desktop_primary_owns_and_closes_all_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, context = _install_fakes(monkeypatch)
    FakeInstance.outcome = InstanceOutcome.PRIMARY

    assert desktop.run_desktop(Path.cwd() / "build" / "fake-root", quit_after_ms=1) == 0
    assert application.quit_called
    assert context.task_runtime.started
    assert context.workflow_runtime.started
    assert context.closed
    assert FakeWindow.last is not None and FakeWindow.last.shown
    assert FakeInstance.last is not None and FakeInstance.last.closed


@pytest.mark.parametrize(
    "outcome",
    [InstanceOutcome.NOTIFIED_EXISTING, InstanceOutcome.EXISTING_UNREACHABLE],
)
def test_run_desktop_secondary_outcomes_return_without_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    outcome: InstanceOutcome,
) -> None:
    _install_fakes(monkeypatch)
    FakeInstance.outcome = outcome

    assert desktop.run_desktop() == 0
    assert FakeInstance.last is not None and FakeInstance.last.closed


def test_run_desktop_reports_instance_and_core_startup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fakes(monkeypatch)

    class BrokenInstance(FakeInstance):
        def start(self) -> InstanceOutcome:
            raise RuntimeError("instance failure")

    monkeypatch.setattr(desktop, "SingleInstanceCoordinator", BrokenInstance)
    assert desktop.run_desktop() == 1
    assert BrokenInstance.last is not None and BrokenInstance.last.closed

    _install_fakes(monkeypatch)
    FakeInstance.outcome = InstanceOutcome.PRIMARY

    async def fail_build(_root: Path | None) -> FakeContext:
        raise RuntimeError("core failure")

    monkeypatch.setattr(desktop, "build_app_context", fail_build)
    assert desktop.run_desktop() == 1
    assert FakeInstance.last is not None and FakeInstance.last.closed
