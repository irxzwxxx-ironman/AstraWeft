"""Real offscreen desktop startup and controlled-shutdown tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.integration
def test_desktop_starts_migrates_and_exits_cleanly(tmp_path: Path) -> None:
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    command = [
        sys.executable,
        "-m",
        "astraweft",
        "--data-dir",
        str(tmp_path),
        "--quit-after-ms",
        "25",
    ]

    for _ in range(2):
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=15,
        )
        assert completed.returncode == 0, completed.stderr

    records = [
        json.loads(line)
        for line in (tmp_path / "logs" / "astraweft.jsonl").read_text().splitlines()
    ]
    messages = [record["message"] for record in records]
    assert messages.count("bootstrap_ready") == 2
    assert messages.count("shutdown_complete") == 2
    assert all(record.get("trace_id") for record in records)
    assert (tmp_path / "data" / "astraweft.db").exists()


@pytest.mark.integration
def test_second_desktop_process_returns_without_opening_database_concurrently(
    tmp_path: Path,
) -> None:
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    base_command = [sys.executable, "-m", "astraweft", "--data-dir", str(tmp_path)]
    primary = subprocess.Popen(
        [*base_command, "--quit-after-ms", "800"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    log_path = tmp_path / "logs" / "astraweft.jsonl"
    try:
        for _ in range(200):
            if log_path.exists() and "bootstrap_ready" in log_path.read_text():
                break
            time.sleep(0.01)
        else:
            pytest.fail("primary desktop did not become ready")

        secondary = subprocess.run(
            base_command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=5,
        )
        assert secondary.returncode == 0, secondary.stderr
        assert primary.wait(timeout=5) == 0
    finally:
        if primary.poll() is None:
            primary.terminate()
            primary.wait(timeout=5)

    messages = [
        json.loads(line)["message"] for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert messages.count("bootstrap_started") == 1
