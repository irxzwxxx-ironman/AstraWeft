"""Process-level command-line bootstrap for the desktop application."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from astraweft import APP_NAME, __version__
from astraweft.bootstrap.app import run_desktop


def build_parser() -> argparse.ArgumentParser:
    """Build the process-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="astraweft",
        description="Local-first desktop AI workflow manager.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_NAME} {__version__}",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Use an isolated root for config, data, cache, and logs.",
    )
    parser.add_argument(
        "--quit-after-ms",
        type=_non_negative_int,
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse process arguments and start the desktop application."""
    arguments = build_parser().parse_args(argv)
    return run_desktop(arguments.data_dir, quit_after_ms=arguments.quit_after_ms)


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed
