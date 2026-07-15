"""Unit tests for the process bootstrap."""

import runpy
import sys
from pathlib import Path

import pytest

from astraweft import APP_NAME, __version__
from astraweft.bootstrap.cli import build_parser, main


def test_main_starts_desktop_with_default_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "astraweft.bootstrap.cli.run_desktop",
        lambda path, *, quit_after_ms: 23 if path is None and quit_after_ms is None else 1,
    )

    assert main([]) == 23


def test_main_forwards_an_isolated_data_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[object] = []

    def capture_path(path: object, *, quit_after_ms: int | None) -> int:
        captured.extend((path, quit_after_ms))
        return 0

    monkeypatch.setattr(
        "astraweft.bootstrap.cli.run_desktop",
        capture_path,
    )

    assert main(["--data-dir", str(tmp_path)]) == 0
    assert captured == [tmp_path, None]


def test_main_accepts_no_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("astraweft.bootstrap.cli.run_desktop", lambda _path, *, quit_after_ms: 0)

    assert main([]) == 0


def test_internal_smoke_timeout_is_validated_and_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[int | None] = []

    def capture_timeout(_path: object, *, quit_after_ms: int | None) -> int:
        captured.append(quit_after_ms)
        return 0

    monkeypatch.setattr(
        "astraweft.bootstrap.cli.run_desktop",
        capture_timeout,
    )

    assert main(["--quit-after-ms", "25"]) == 0
    assert captured == [25]
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--quit-after-ms", "-1"])


def test_parser_reports_product_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"{APP_NAME} {__version__}"


def test_module_entrypoint_exits_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["astraweft", "--version"])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("astraweft", run_name="__main__")

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"{APP_NAME} {__version__}"
