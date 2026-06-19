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


@pytest.fixture
def tmp_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
    """Set up a tmp global config with a registered repo.

    Returns (config_path, repo_path) tuple. The global config at config_path
    has a 'main' project with the repo at repo_path registered. Tests can
    add sources to the repo entry via load_global_config/modify/save.
    """
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(config_path))

    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    from mementic.config import (
        GlobalConfig,
        ProjectEntry,
        RepoEntry,
        Sources,
        save_global_config,
    )

    global_cfg = GlobalConfig(
        default_project="main",
        projects={
            "main": ProjectEntry(
                db_path=str(tmp_path / "test.mementic.db"),
                repos={
                    str(repo_path.resolve()): RepoEntry(
                        path=str(repo_path),
                        label=repo_path.name,
                        sources=Sources(),
                    ),
                },
            ),
        },
    )
    save_global_config(global_cfg)

    return config_path, str(repo_path)


@pytest.fixture
def tmp_global_config_with_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
    """Like tmp_global_config but creates docs/adr/ and docs/instructions/ dirs."""
    config_path, repo_path = tmp_global_config(tmp_path, monkeypatch)

    # Create source dirs
    adr_dir = Path(repo_path) / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    instr_dir = Path(repo_path) / "docs" / "instructions"
    instr_dir.mkdir(parents=True)

    # Update repo entry with sources
    from mementic.config import (
        AdrDirSource,
        OpenCodeSource,
        Sources,
        load_global_config,
        save_global_config,
    )

    global_cfg = load_global_config()
    for proj in global_cfg.projects.values():
        for repo in proj.repos.values():
            if repo.path == str(repo_path):
                repo.sources = Sources(
                    adr_dirs=[AdrDirSource(path=str(adr_dir))],
                    opencode_instructions=[OpenCodeSource(path=str(instr_dir))],
                )
    save_global_config(global_cfg)

    return config_path, repo_path
