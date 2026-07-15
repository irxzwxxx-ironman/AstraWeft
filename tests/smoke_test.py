"""Smoke test that can run against an isolated wheel or source distribution."""

from astraweft import APP_NAME, __version__
from astraweft.bootstrap.cli import build_parser


def test_installed_distribution() -> None:
    assert APP_NAME == "AstraWeft"
    assert __version__
    assert build_parser().prog == "astraweft"
