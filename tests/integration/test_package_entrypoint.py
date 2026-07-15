"""Installed-package entry point smoke tests."""

from __future__ import annotations

import subprocess
import sys

import pytest

from astraweft import APP_NAME, __version__


@pytest.mark.integration
def test_python_module_entrypoint_reports_version() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "astraweft", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == f"{APP_NAME} {__version__}"
    assert completed.stderr == ""
