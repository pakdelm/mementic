"""Tests for the session-cleanup CLI command."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from mementic.cli import app

runner = CliRunner()


def _write_config_and_graph(
    tmp_path: Path,
    decisions: list[dict] | None = None,
    constraints: list[dict] | None = None,
    entities: list[dict] | None = None,
    edges: list[dict] | None = None,
) -> tuple[Path, Path]:
    """Helper to write a .mementic.json and graph.json for CLI tests."""
    config_path = tmp_path / ".mementic.json"
    graph_path = tmp_path / ".mementic" / "graph.json"
    graph_path.parent.mkdir(parents=True)

    config_path.write_text(json.dumps({
        "output": {"graph_path": str(graph_path)},
        "llm": {"provider": "deepseek", "model": "deepseek-chat"},
    }))

    graph_data: dict = {
        "nodes": [],
        "edges": edges or [],
    }

    for d in (decisions or []):
        graph_data["nodes"].append({
            "id": d["id"],
            "type": "Decision",
            "data": d,
        })
    for c in (constraints or []):
        graph_data["nodes"].append({
            "id": c["id"],
            "type": "Constraint",
            "data": c,
        })
    for e in (entities or []):
        graph_data["nodes"].append({
            "id": e["id"],
            "type": "CodeEntity",
            "data": e,
        })

    graph_path.write_text(json.dumps(graph_data))
    return config_path, graph_path


# ── Tests ────────────────────────────────────────────────────────────


class TestSessionCleanup:
    """Tests for the session-cleanup CLI command."""

    def test_cleanup_no_deprecate_flag_shows_counts(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """session-cleanup with no --deprecate still reports active counts."""
        config_path, _graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[
                {
                    "id": "d1",
                    "title": "Use async",
                    "rationale": "Better performance",
                    "source": "test",
                    "status": "accepted",
                    "created_at": "2024-01-01T00:00:00Z",
                    "modified_at": "2024-01-01T00:00:00Z",
                    "tags": [],
                    "rejected_alternatives": [],
                    "author": "test",
                    "affected_entities": [],
                },
            ],
            constraints=[
                {
                    "id": "c1",
                    "description": "All I/O must be async",
                    "bound_entities": ["src/io.py"],
                    "enforced_by": "",
                    "source": "test",
                    "status": "active",
                    "created_at": "2024-01-01T00:00:00Z",
                },
            ],
            entities=[
                {
                    "id": "src/io.py",
                    "type": "file",
                    "repo": "test-repo",
                },
            ],
        )
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(
            app, ["session-cleanup", "Test feature done"],
        )

        assert result.exit_code == 0
        output = result.stdout
        # Check summary line format
        assert "session-cleanup: 'Test feature done'" in output
        assert "2 active" in output
        assert "1 decisions" in output
        assert "1 constraints" in output

    def test_cleanup_deprecates_valid_ids(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """session-cleanup --deprecate marks entities as deprecated."""
        config_path, _graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[
                {
                    "id": "use-old-auth",
                    "title": "Use Old Auth",
                    "rationale": "Basic session auth",
                    "source": "test",
                    "status": "accepted",
                    "created_at": "2024-01-01T00:00:00Z",
                    "modified_at": "2024-01-01T00:00:00Z",
                    "tags": [],
                    "rejected_alternatives": [],
                    "author": "test",
                    "affected_entities": [],
                },
                {
                    "id": "use-jwt",
                    "title": "Use JWT",
                    "rationale": "Token auth",
                    "source": "test",
                    "status": "accepted",
                    "created_at": "2024-01-01T00:00:00Z",
                    "modified_at": "2024-01-01T00:00:00Z",
                    "tags": [],
                    "rejected_alternatives": [],
                    "author": "test",
                    "affected_entities": [],
                },
            ],
        )
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(
            app,
            [
                "session-cleanup",
                "Auth refactor done",
                "--deprecate", "use-old-auth",
            ],
        )

        assert result.exit_code == 0
        output = result.stdout
        assert "session-cleanup: 'Auth refactor done'" in output
        assert "deprecated use-old-auth" in output
        # After deprecation, 1 active decision remains
        assert "1 active" in output
        assert "1 decisions" in output
        assert "0 constraints" in output

    def test_cleanup_already_deprecated_id(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """session-cleanup handles already-deprecated IDs gracefully."""
        config_path, _graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[
                {
                    "id": "already-dep",
                    "title": "Already Deprecated",
                    "rationale": "Old stuff",
                    "source": "test",
                    "status": "deprecated",
                    "created_at": "2024-01-01T00:00:00Z",
                    "modified_at": "2024-01-01T00:00:00Z",
                    "tags": [],
                    "rejected_alternatives": [],
                    "author": "test",
                    "affected_entities": [],
                },
            ],
        )
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(
            app,
            [
                "session-cleanup",
                "Test",
                "--deprecate", "already-dep",
            ],
        )

        assert result.exit_code == 0
        output = result.stdout
        assert "already deprecated: already-dep" in output

    def test_cleanup_not_found_id(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """session-cleanup reports not-found IDs gracefully."""
        config_path, _graph_path = _write_config_and_graph(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(
            app,
            [
                "session-cleanup",
                "Test",
                "--deprecate", "ghost-id",
            ],
        )

        assert result.exit_code == 0
        output = result.stdout
        assert "not found: ghost-id" in output

    def test_cleanup_multiple_deprecate_flags(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """session-cleanup accepts multiple --deprecate flags."""
        config_path, _graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[
                {
                    "id": "d-to-dep-1",
                    "title": "To dep 1",
                    "rationale": "...",
                    "source": "test",
                    "status": "accepted",
                    "created_at": "2024-01-01T00:00:00Z",
                    "modified_at": "2024-01-01T00:00:00Z",
                    "tags": [],
                    "rejected_alternatives": [],
                    "author": "test",
                    "affected_entities": [],
                },
                {
                    "id": "d-to-dep-2",
                    "title": "To dep 2",
                    "rationale": "...",
                    "source": "test",
                    "status": "accepted",
                    "created_at": "2024-01-01T00:00:00Z",
                    "modified_at": "2024-01-01T00:00:00Z",
                    "tags": [],
                    "rejected_alternatives": [],
                    "author": "test",
                    "affected_entities": [],
                },
                {
                    "id": "d-to-keep",
                    "title": "To keep",
                    "rationale": "...",
                    "source": "test",
                    "status": "accepted",
                    "created_at": "2024-01-01T00:00:00Z",
                    "modified_at": "2024-01-01T00:00:00Z",
                    "tags": [],
                    "rejected_alternatives": [],
                    "author": "test",
                    "affected_entities": [],
                },
            ],
        )
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(
            app,
            [
                "session-cleanup",
                "Multi deprecation",
                "--deprecate", "d-to-dep-1",
                "--deprecate", "d-to-dep-2",
            ],
        )

        assert result.exit_code == 0
        output = result.stdout
        assert "deprecated d-to-dep-1, d-to-dep-2" in output
        assert "1 active" in output  # only d-to-keep remains

    def test_cleanup_mixed_valid_already_notfound(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """session-cleanup with mixed valid, already-deprecated, and not-found IDs."""
        config_path, _graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[
                {
                    "id": "d-active",
                    "title": "Active",
                    "rationale": "...",
                    "source": "test",
                    "status": "accepted",
                    "created_at": "2024-01-01T00:00:00Z",
                    "modified_at": "2024-01-01T00:00:00Z",
                    "tags": [],
                    "rejected_alternatives": [],
                    "author": "test",
                    "affected_entities": [],
                },
                {
                    "id": "d-deprecated",
                    "title": "Deprecated",
                    "rationale": "...",
                    "source": "test",
                    "status": "deprecated",
                    "created_at": "2024-01-01T00:00:00Z",
                    "modified_at": "2024-01-01T00:00:00Z",
                    "tags": [],
                    "rejected_alternatives": [],
                    "author": "test",
                    "affected_entities": [],
                },
            ],
        )
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(
            app,
            [
                "session-cleanup",
                "Mixed test",
                "--deprecate", "d-active",
                "--deprecate", "d-deprecated",
                "--deprecate", "ghost",
            ],
        )

        assert result.exit_code == 0
        output = result.stdout
        assert "deprecated d-active" in output
        assert "already deprecated: d-deprecated" in output
        assert "not found: ghost" in output
        assert "0 active" in output  # all decisions now deprecated

    def test_cleanup_with_constraint(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """session-cleanup works with constraints via --deprecate."""
        config_path, _graph_path = _write_config_and_graph(
            tmp_path,
            constraints=[
                {
                    "id": "c-old-rule",
                    "description": "Old rule that should go away",
                    "bound_entities": [],
                    "enforced_by": "",
                    "source": "test",
                    "status": "active",
                    "created_at": "2024-01-01T00:00:00Z",
                },
            ],
        )
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(
            app,
            [
                "session-cleanup",
                "Constraint cleanup",
                "--deprecate", "c-old-rule",
            ],
        )

        assert result.exit_code == 0
        output = result.stdout
        assert "deprecated c-old-rule" in output
        assert "0 active" in output
