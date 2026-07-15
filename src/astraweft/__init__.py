"""AstraWeft application package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("astraweft")
except PackageNotFoundError:  # pragma: no cover - source tree without installation
    __version__ = "0.1.0.dev0"

APP_NAME = "AstraWeft"

__all__ = ["APP_NAME", "__version__"]
