"""Shared test fixtures and configuration."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock keyring so tests never touch the real OS credential store.

    By default returns None (no stored keys).  Tests that need a keyring
    entry can override with ``monkeypatch.setattr(keyring, 'get_password',
    lambda s, u: 'sk-test-...')`` or use the ``keyring_store`` fixture.
    """
    monkeypatch.setattr("keyring.get_password", lambda service, username: None)
    monkeypatch.setattr("keyring.set_password", lambda service, username, pwd: None)
    monkeypatch.setattr("keyring.delete_password", lambda service, username: None)
