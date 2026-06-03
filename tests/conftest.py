"""Shared test fixtures and configuration."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point MEMENTIC_GLOBAL_CONFIG to a temp directory for test isolation.

    Without this, tests on a developer machine that has a real
    ``~/.mementic/config.json`` would accidentally resolve the real
    project database instead of the test's temp ``graph.json``.
    """
    fake_config = tmp_path / "nonexistent" / "config.json"
    monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(fake_config))
