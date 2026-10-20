"""Tests for lazy CLI startup: deferred imports and version lookup."""

from __future__ import annotations

import subprocess
import sys


def test_cli_import_does_not_load_command_modules() -> None:
    """Importing the root CLI app must not import any command module."""
    code = (
        "import sys\n"
        "import mementic.cli\n"
        "loaded = sorted(name for name in sys.modules if name.startswith('mementic.cli.'))\n"
        "assert loaded == [], loaded\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr


def test_version_resolves_on_first_access() -> None:
    """The package version is resolved through the lazy module attribute."""
    import mementic

    assert mementic.__version__


def test_sanitize_is_available_without_rich_console() -> None:
    """Sanitizing text does not require importing the Rich console module."""
    from mementic.text import sanitize

    assert sanitize("a \u2014 b") == "a -- b"
