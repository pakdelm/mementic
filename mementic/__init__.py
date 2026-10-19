"""Mementic — semantic memory layer for engineering teams and AI agents."""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    """Resolve the package version on first access.

    ``importlib.metadata`` costs roughly 100 ms to import, so the lookup is
    deferred out of the CLI startup path instead of running at module import.

    Args:
        name: Attribute requested from the package.

    Returns:
        The package version for ``__version__``.

    Raises:
        AttributeError: For every other attribute name.
    """
    if name != "__version__":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("mementic")
    except PackageNotFoundError:
        return "0.0.0"
