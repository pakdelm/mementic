"""Mementic — semantic memory layer for engineering teams and AI agents."""

from importlib.metadata import PackageNotFoundError, version as _get_version

try:
    __version__ = _get_version("mementic")
except PackageNotFoundError:
    __version__ = "0.0.0"  # fallback for development before package is installed
