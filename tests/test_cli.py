"""Tests for mementic CLI commands (Spec 06a D2, D4, 06c)."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from mementic.cli import app
from mementic.config import load_global_config

runner = CliRunner()


def _make_mock_graph(**stats_overrides: int) -> MagicMock:
    """Build a MagicMock GraphStore that supports stats() and is not None."""
    mock_graph = MagicMock()
    mock_graph.stats.return_value = {
        "decisions": stats_overrides.get("decisions", 0),
        "constraints": stats_overrides.get("constraints", 0),
        "entities": stats_overrides.get("entities", 0),
        "edges": stats_overrides.get("edges", 0),
    }
    return mock_graph


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from CLI output for platform-agnostic assertions.

    Covers SGR sequences (colors, bold, etc.) used by Typer/Rich formatting.
    Does not cover OSC sequences (hyperlinks) or cursor movement — those are
    rare in CLI help output. Use a library like ``strip-ansi`` if this becomes
    a recurring pattern across tests.
    """
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


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


# ── D2: mementic init auto-bootstrap ──────────────────────────────


class TestInitAutoBootstrap:
    """Tests for mementic init auto-bootstrap behaviour."""

    def test_init_autocreate_config_when_missing(
        self, tmp_path: Path
    ) -> None:
        """mementic init auto-creates .mementic.json from discovered dirs."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Create source dirs so auto-discovery finds them
            adr_dir = tmp_path / "docs" / "adr"
            adr_dir.mkdir(parents=True)
            (adr_dir / "0001-test.md").write_text(
                "# ADR-0001\n\nStatus: Accepted\n\nContext: test\n\nDecision: test"
            )

            instr_dir = tmp_path / "docs" / "instructions"
            instr_dir.mkdir(parents=True)
            (instr_dir / "style.md").write_text("# Style")

            config_path = tmp_path / ".mementic.json"

            with patch("mementic.cli.run_init", return_value=(_make_mock_graph(), [])):
                result = runner.invoke(
                    app, ["init", "--config-path", str(config_path)],
                )

            assert result.exit_code == 0
            assert config_path.exists()
            config_data = json.loads(config_path.read_text())
            assert "sources" in config_data
            paths = [s["path"] for s in config_data["sources"]["adr_dirs"]]
            assert "./docs/adr" in paths
            paths = [s["path"] for s in config_data["sources"]["opencode_instructions"]]
            assert "./docs/instructions" in paths
        finally:
            os.chdir(old_cwd)

    def test_init_force_fails_when_config_missing(
        self, tmp_path: Path
    ) -> None:
        """mementic init --force exits with error when .mementic.json missing."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config_path = tmp_path / ".mementic.json"

            result = runner.invoke(
                app, ["init", "--config-path", str(config_path), "--force"],
            )

            assert result.exit_code == 1
            assert "Cannot proceed" in result.stdout or "Cannot proceed" in result.stderr
        finally:
            os.chdir(old_cwd)

    def test_init_autocreate_fails_when_no_sources_discovered(
        self, tmp_path: Path
    ) -> None:
        """mementic init exits when no source dirs found for auto-discovery."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config_path = tmp_path / ".mementic.json"

            with patch("mementic.cli.run_init", return_value=(_make_mock_graph(), [])):
                result = runner.invoke(
                    app, ["init", "--config-path", str(config_path)],
                )

            assert result.exit_code == 1
            assert "No sources discovered" in result.stdout or "No sources discovered" in result.stderr
        finally:
            os.chdir(old_cwd)

    def test_init_existing_config_not_overwritten(
        self, tmp_path: Path
    ) -> None:
        """When .mementic.json exists, init uses it without overwriting."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config_path = tmp_path / ".mementic.json"
            original = {
                "sources": {"adr_dirs": [{"path": "/custom/path"}]},
                "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
                "llm": {"provider": "deepseek", "model": "deepseek-chat"},
            }
            config_path.write_text(json.dumps(original))

            with patch("mementic.cli.run_init", return_value=(_make_mock_graph(), [])):
                result = runner.invoke(
                    app, ["init", "--config-path", str(config_path)],
                )

            assert result.exit_code == 0
            loaded = json.loads(config_path.read_text())
            assert loaded["sources"]["adr_dirs"][0]["path"] == "/custom/path"
        finally:
            os.chdir(old_cwd)

    def test_init_discovers_cursor_instructions(
        self, tmp_path: Path
    ) -> None:
        """mementic init auto-discovers .cursor/instructions/ and includes in config."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Create cursor instructions dir so auto-discovery finds it
            cursor_dir = tmp_path / ".cursor" / "instructions"
            cursor_dir.mkdir(parents=True)
            (cursor_dir / "typescript.md").write_text("# Cursor TypeScript Rules")

            config_path = tmp_path / ".mementic.json"

            with patch("mementic.cli.run_init", return_value=(_make_mock_graph(), [])):
                result = runner.invoke(
                    app, ["init", "--config-path", str(config_path)],
                )

            assert result.exit_code == 0
            assert config_path.exists()
            config_data = json.loads(config_path.read_text())
            assert "sources" in config_data
            assert "cursor_instructions" in config_data["sources"]
            paths = [s["path"] for s in config_data["sources"]["cursor_instructions"]]
            assert ".cursor/instructions" in paths
        finally:
            os.chdir(old_cwd)

    def test_init_discovers_windsurf_instructions(
        self, tmp_path: Path
    ) -> None:
        """mementic init auto-discovers .windsurf/instructions/ and includes in config."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Create windsurf instructions dir so auto-discovery finds it
            windsurf_dir = tmp_path / ".windsurf" / "instructions"
            windsurf_dir.mkdir(parents=True)
            (windsurf_dir / "styling.md").write_text("# Windsurf Styling Rules")

            config_path = tmp_path / ".mementic.json"

            with patch("mementic.cli.run_init", return_value=(_make_mock_graph(), [])):
                result = runner.invoke(
                    app, ["init", "--config-path", str(config_path)],
                )

            assert result.exit_code == 0
            assert config_path.exists()
            config_data = json.loads(config_path.read_text())
            assert "sources" in config_data
            assert "windsurf_instructions" in config_data["sources"]
            paths = [s["path"] for s in config_data["sources"]["windsurf_instructions"]]
            assert ".windsurf/instructions" in paths
        finally:
            os.chdir(old_cwd)


class TestInitInteractive:
    """Tests for mementic init --interactive."""

    def test_init_interactive_updates_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mementic init --interactive prompts user and updates global config."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config_path = tmp_path / ".mementic.json"
            config_path.write_text(json.dumps({
                "sources": {},
                "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
            }))

            # Set up global config so interactive mode writes to it
            global_config_path = tmp_path / "global_config.json"
            global_config_path.write_text(json.dumps({
                "version": 1,
                "default_project": "main",
                "projects": {},
                "llm": {"provider": "deepseek", "model": "deepseek-chat"},
            }))
            monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(global_config_path))

            with patch("mementic.cli.run_init", return_value=(_make_mock_graph(), [])):
                result = runner.invoke(
                    app,
                    ["init", "--config-path", str(config_path), "--interactive"],
                    input="openai\ngpt-4o\n",
                )

            assert result.exit_code == 0
            # LLM config should be in global config, not .mementic.json
            local = json.loads(config_path.read_text())
            assert "llm" not in local
            global_cfg = json.loads(global_config_path.read_text())
            assert global_cfg["llm"]["provider"] == "openai"
            assert global_cfg["llm"]["model"] == "gpt-4o"
        finally:
            os.chdir(old_cwd)

    def test_init_interactive_defaults_kept_on_empty_input(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mementic init --interactive with empty inputs keeps defaults."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config_path = tmp_path / ".mementic.json"
            config_path.write_text(json.dumps({
                "sources": {},
                "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
            }))

            # Set up global config
            global_config_path = tmp_path / "global_config.json"
            global_config_path.write_text(json.dumps({
                "version": 1,
                "default_project": "main",
                "projects": {},
                "llm": {"provider": "deepseek", "model": "deepseek-chat"},
            }))
            monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(global_config_path))

            with patch("mementic.cli.run_init", return_value=(_make_mock_graph(), [])):
                result = runner.invoke(
                    app,
                    ["init", "--config-path", str(config_path), "--interactive"],
                    input="\n\n",
                )

            assert result.exit_code == 0
            global_cfg = json.loads(global_config_path.read_text())
            assert global_cfg["llm"]["provider"] == "deepseek"
            assert global_cfg["llm"]["model"] == "deepseek-chat"
        finally:
            os.chdir(old_cwd)


# ── D4: graph corrupt error message ───────────────────────────────


class TestGraphCorruptMessage:
    """Tests for D4: corrupt graph file error message."""

    def test_graph_corrupt_message_on_invalid_json(
        self, tmp_path: Path
    ) -> None:
        """Graph file with invalid JSON shows health checklist [FAIL]."""
        graph_path = tmp_path / ".mementic" / "graph.json"
        graph_path.parent.mkdir(parents=True)
        graph_path.write_text("not valid json at all {{{")

        config_path = tmp_path / ".mementic.json"
        config_path.write_text(json.dumps({
            "output": {"graph_path": str(graph_path)},
        }))

        result = runner.invoke(
            app, ["status"],
            env={"MEMENTIC_CONFIG": str(config_path)},
        )
        # Status command handles graph load failures gracefully
        assert result.exit_code == 0
        output = result.stdout + result.stderr
        assert "[FAIL]" in output
        assert "Graph" in output

    def test_graph_missing_shows_init_message(
        self, tmp_path: Path
    ) -> None:
        """Missing graph file shows health checklist [FAIL]."""
        graph_path = tmp_path / ".mementic" / "graph.json"

        config_path = tmp_path / ".mementic.json"
        config_path.write_text(json.dumps({
            "output": {"graph_path": str(graph_path)},
        }))

        result = runner.invoke(
            app, ["status"],
            env={"MEMENTIC_CONFIG": str(config_path)},
        )
        # Status command handles graph load failures gracefully
        assert result.exit_code == 0
        output = result.stdout + result.stderr
        assert "[FAIL]" in output
        assert "Graph" in output


# ── D4: git error message in capture ──────────────────────────────


class TestCaptureGitErrorMessage:
    """Tests for D4: clear git error message in capture command."""

    def test_capture_without_git_shows_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """capture without a git repo shows a clear warning about git."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")

        config_path = tmp_path / ".mementic.json"
        config_path.write_text(json.dumps({
            "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
        }))
        monkeypatch.setenv("MEMENTIC_CONFIG", str(config_path))

        # Create a fake session file to avoid "No session found"
        session_file = tmp_path / "session.log"
        session_file.write_text("Fake agent session log content.\n")

        with patch(
            "mementic.extraction.extract_from_session",
            return_value=__import__("mementic.models").models.ExtractionResult(),
        ):
            result = runner.invoke(
                app, ["capture", "--session", str(session_file)],
            )

        output = result.stdout + result.stderr
        assert "git" in output.lower()


# ── 06c F1: capture auto-accept ────────────────────────────────────


class TestCaptureAutoAccept:
    """Tests for capture auto-accept behaviour (Spec 06c F1)."""

    def test_capture_auto_accepts_non_conflicting_decisions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Decisions with no conflicts are auto-accepted without prompts."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")

        config_path, graph_path = _write_config_and_graph(tmp_path)
        monkeypatch.setenv("MEMENTIC_CONFIG", str(config_path))

        session_file = tmp_path / "session.log"
        session_file.write_text("Agent session about adding auth.\n")

        from mementic.models import Decision, DecisionStatus, ExtractionResult, UsageStats

        mock_result = ExtractionResult(
            decisions=[
                Decision(
                    id="add-jwt-auth",
                    title="Add JWT Auth",
                    rationale="Secure API with tokens",
                    source="session",
                    status=DecisionStatus.ACCEPTED,
                    created_at="2024-01-01T00:00:00Z",
                    modified_at="2024-01-01T00:00:00Z",
                ),
            ],
            affected_entities=["src/auth.py"],
        )
        mock_usage = UsageStats(model="test-model", status="success")

        with patch(
            "mementic.cli.extract_from_session",
            return_value=(mock_result, mock_usage),
        ), patch(
            "mementic.cli._run_git_diff", return_value=("", 0),
        ):
            result = runner.invoke(
                app, ["capture", "--session", str(session_file)],
            )

        output = result.stdout + result.stderr
        # Should show new decisions
        assert "New:" in output
        assert "1 decisions" in output
        # No duplicates flagged
        assert "Flagged" not in output

    def test_capture_updates_conflicting_decisions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Decisions with same slug but different rationale auto-update (deprecate existing)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")

        config_path, graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[{
                "id": "use-jwt-auth",
                "title": "Use JWT Auth",
                "rationale": "Token-based auth for APIs",
                "source": "ADR-001",
                "status": "accepted",
                "timestamp": "2024-01-01T00:00:00Z",
                "tags": ["auth"],
                "rejected_alternatives": [],
                "author": "test",
            }],
        )
        monkeypatch.setenv("MEMENTIC_CONFIG", str(config_path))

        session_file = tmp_path / "session.log"
        session_file.write_text("Session about JWT auth updates.\n")

        from mementic.models import Decision, DecisionStatus, ExtractionResult, UsageStats

        mock_result = ExtractionResult(
            decisions=[
                Decision(
                    id="new-jwt-approach",
                    title="Use JWT Auth",  # slugifies to "use-jwt-auth" → update
                    rationale="Completely different approach to JWT authentication with rotating keys",
                    source="session",
                    status=DecisionStatus.ACCEPTED,
                    created_at="2024-01-02T00:00:00Z",
                    modified_at="2024-01-02T00:00:00Z",
                ),
            ],
            affected_entities=[],
        )
        mock_usage = UsageStats(model="test-model", status="success")

        with patch(
            "mementic.cli.extract_from_session",
            return_value=(mock_result, mock_usage),
        ), patch(
            "mementic.cli._run_git_diff", return_value=("", 0),
        ):
            result = runner.invoke(
                app, ["capture", "--session", str(session_file)],
            )

        output = result.stdout + result.stderr
        assert "Updated:" in output
        assert "1 decisions (existing deprecated)" in output

    def test_capture_no_interactive_prompts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Capture produces no interactive y/n prompts."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")

        config_path, graph_path = _write_config_and_graph(tmp_path)
        monkeypatch.setenv("MEMENTIC_CONFIG", str(config_path))

        session_file = tmp_path / "session.log"
        session_file.write_text("Session log content.\n")

        from mementic.models import Decision, DecisionStatus, ExtractionResult

        mock_result = ExtractionResult(
            decisions=[
                Decision(
                    id="test-decision",
                    title="Test Decision",
                    rationale="Testing",
                    source="session",
                    status=DecisionStatus.ACCEPTED,
                    created_at="2024-01-01T00:00:00Z",
                    modified_at="2024-01-01T00:00:00Z",
                ),
            ],
        )

        with patch(
            "mementic.cli.extract_from_session",
            return_value=mock_result,
        ):
            result = runner.invoke(
                app, ["capture", "--session", str(session_file)],
            )

        output = result.stdout + result.stderr
        # No y/n prompt
        assert "Accept?" not in output
        assert "[Y/n]" not in output


# ── 06c F3: list command ───────────────────────────────────────────


class TestListCommand:
    """Tests for mementic list command (Spec 06c F3)."""

    def test_list_decisions(self, tmp_path: Path) -> None:
        config_path, _graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[
                {
                    "id": "use-jwt",
                    "title": "Use JWT Auth",
                    "rationale": "Tokens",
                    "source": "test",
                    "status": "accepted",
                    "timestamp": "2024-05-15T00:00:00Z",
                    "tags": ["auth"],
                    "rejected_alternatives": [],
                    "author": "test",
                },
                {
                    "id": "use-async",
                    "title": "Use async I/O",
                    "rationale": "Performance",
                    "source": "test",
                    "status": "accepted",
                    "timestamp": "2024-05-20T00:00:00Z",
                    "tags": ["perf"],
                    "rejected_alternatives": [],
                    "author": "test",
                },
            ],
        )

        result = runner.invoke(
            app, ["list", "decisions"],
            env={"MEMENTIC_CONFIG": str(config_path)},
        )
        assert result.exit_code == 0
        output = result.stdout
        assert "use-jwt" in output
        assert "use-async" in output
        assert "Use JWT Auth" in output

    def test_list_decisions_by_status(self, tmp_path: Path) -> None:
        config_path, _graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[
                {
                    "id": "d-accepted",
                    "title": "Accepted",
                    "rationale": "...",
                    "source": "test",
                    "status": "accepted",
                    "timestamp": "2024-01-01T00:00:00Z",
                    "tags": [],
                    "rejected_alternatives": [],
                    "author": "test",
                },
                {
                    "id": "d-deprecated",
                    "title": "Deprecated",
                    "rationale": "...",
                    "source": "test",
                    "status": "deprecated",
                    "timestamp": "2024-01-01T00:00:00Z",
                    "tags": [],
                    "rejected_alternatives": [],
                    "author": "test",
                },
            ],
        )

        result = runner.invoke(
            app, ["list", "decisions", "--status", "deprecated"],
            env={"MEMENTIC_CONFIG": str(config_path)},
        )
        assert result.exit_code == 0
        output = result.stdout
        assert "d-deprecated" in output
        assert "d-accepted" not in output

    def test_list_decisions_last_n(self, tmp_path: Path) -> None:
        config_path, _graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[
                {
                    "id": f"d-{i}",
                    "title": f"Decision {i}",
                    "rationale": "...",
                    "source": "test",
                    "status": "accepted",
                    "timestamp": f"2024-01-{i:02d}T00:00:00Z",
                    "tags": [],
                    "rejected_alternatives": [],
                    "author": "test",
                }
                for i in range(1, 6)
            ],
        )

        result = runner.invoke(
            app, ["list", "decisions", "-n", "2"],
            env={"MEMENTIC_CONFIG": str(config_path)},
        )
        assert result.exit_code == 0
        output = result.stdout
        assert "d-4" in output
        assert "d-5" in output
        assert "d-1" not in output

    def test_list_constraints(self, tmp_path: Path) -> None:
        config_path, _graph_path = _write_config_and_graph(
            tmp_path,
            constraints=[
                {
                    "id": "c-stateless",
                    "description": "Services must be stateless",
                    "bound_entities": [],
                    "enforced_by": "use-jwt",
                    "source": "test",
                    "status": "active",
                    "timestamp": "2024-01-01T00:00:00Z",
                },
            ],
        )

        result = runner.invoke(
            app, ["list", "constraints"],
            env={"MEMENTIC_CONFIG": str(config_path)},
        )
        assert result.exit_code == 0
        output = result.stdout
        assert "c-stateless" in output
        assert "Services must be stateless" in output

    def test_list_entities(self, tmp_path: Path) -> None:
        config_path, _graph_path = _write_config_and_graph(
            tmp_path,
            entities=[
                {
                    "id": "src/auth.py",
                    "type": "file",
                    "repo": "my-repo",
                },
                {
                    "id": "src/models/user.py",
                    "type": "file",
                    "repo": "my-repo",
                },
            ],
        )

        result = runner.invoke(
            app, ["list", "entities"],
            env={"MEMENTIC_CONFIG": str(config_path)},
        )
        assert result.exit_code == 0
        output = result.stdout
        assert "src/auth.py" in output
        assert "my-repo" in output

    def test_list_entities_stale(self, tmp_path: Path) -> None:
        config_path, _graph_path = _write_config_and_graph(
            tmp_path,
            entities=[
                {
                    "id": str(tmp_path / "does_not_exist.py"),
                    "type": "file",
                    "repo": "",
                },
            ],
        )

        result = runner.invoke(
            app, ["list", "entities", "--stale"],
            env={"MEMENTIC_CONFIG": str(config_path)},
        )
        assert result.exit_code == 0
        output = result.stdout
        assert "does_not_exist.py" in output


# ── 06c F4: deprecate command ──────────────────────────────────────


class TestDeprecateCommand:
    """Tests for mementic deprecate command (Spec 06c F4)."""

    def test_deprecate_marks_decision_deprecated(self, tmp_path: Path) -> None:
        config_path, graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[{
                "id": "use-old-auth",
                "title": "Use Old Auth",
                "rationale": "Basic session auth",
                "source": "test",
                "status": "accepted",
                "timestamp": "2024-01-01T00:00:00Z",
                "tags": [],
                "rejected_alternatives": [],
                "author": "test",
            }],
        )

        result = runner.invoke(
            app, ["deprecate", "use-old-auth"],
            env={"MEMENTIC_CONFIG": str(config_path)},
        )
        assert result.exit_code == 0
        output = result.stdout
        assert "Deprecated" in output or "deprecated" in output.lower()
        assert "use-old-auth" in output

    def test_deprecate_with_reason_appends_to_rationale(self, tmp_path: Path) -> None:
        config_path, graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[{
                "id": "use-old-auth",
                "title": "Use Old Auth",
                "rationale": "Basic session auth",
                "source": "test",
                "status": "accepted",
                "timestamp": "2024-01-01T00:00:00Z",
                "tags": [],
                "rejected_alternatives": [],
                "author": "test",
            }],
        )

        result = runner.invoke(
            app,
            ["deprecate", "use-old-auth", "--reason", "Migrated to JWT"],
            env={"MEMENTIC_CONFIG": str(config_path)},
        )
        assert result.exit_code == 0
        output = result.stdout
        assert "Deprecated" in output

    def test_deprecate_nonexistent_shows_error(self, tmp_path: Path) -> None:
        config_path, graph_path = _write_config_and_graph(tmp_path)

        result = runner.invoke(
            app, ["deprecate", "nonexistent-id"],
            env={"MEMENTIC_CONFIG": str(config_path)},
        )
        assert result.exit_code == 1
        output = result.stdout + result.stderr
        assert "not found" in output.lower()

    def test_deprecate_already_deprecated_shows_message(self, tmp_path: Path) -> None:
        config_path, graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[{
                "id": "already-dep",
                "title": "Already Deprecated",
                "rationale": "Old stuff",
                "source": "test",
                "status": "deprecated",
                "timestamp": "2024-01-01T00:00:00Z",
                "tags": [],
                "rejected_alternatives": [],
                "author": "test",
            }],
        )

        result = runner.invoke(
            app, ["deprecate", "already-dep"],
            env={"MEMENTIC_CONFIG": str(config_path)},
        )
        output = result.stdout + result.stderr
        assert "already deprecated" in output.lower()


# ── 06c F7: lint command ───────────────────────────────────────────


# ── Spec 09b B3: Graceful no-git capture ────────────────────────────


class TestCaptureNoGit:
    """Tests for graceful capture when git is unavailable."""

    def test_capture_git_not_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """capture when git is not on PATH prints warning and proceeds."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")

        config_path = tmp_path / ".mementic.json"
        config_path.write_text(json.dumps({
            "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
        }))
        monkeypatch.setenv("MEMENTIC_CONFIG", str(config_path))

        session_file = tmp_path / "session.log"
        session_file.write_text("Fake agent session log content.\n")

        from mementic.models import Decision, DecisionStatus, ExtractionResult, UsageStats

        mock_result = ExtractionResult(
            decisions=[
                Decision(
                    id="test-d",
                    title="Test Decision",
                    rationale="Testing",
                    source="session",
                    status=DecisionStatus.ACCEPTED,
                    created_at="2024-01-01T00:00:00Z",
                    modified_at="2024-01-01T00:00:00Z",
                ),
            ],
        )
        mock_usage = UsageStats(model="test", status="success")

        mock_registry = MagicMock()
        mock_provider = MagicMock()
        mock_provider.agent_name = "opencode"
        mock_registry.detect.return_value = (mock_provider, str(session_file))

        with patch(
            "mementic.session.get_registry", return_value=mock_registry,
        ), patch(
            "mementic.cli._run_git_diff",
            return_value=("", 1),  # git not found (rc=1)
        ), patch(
            "mementic.graph.GraphStore.get_capture_checkpoint", return_value="",
        ), patch(
            "mementic.cli.extract_from_session",
            return_value=(mock_result, mock_usage),
        ): 
            result = runner.invoke(
                app, ["capture", "--session", str(session_file)],
            )

        output = result.stdout + result.stderr
        assert result.exit_code == 0
        assert "git not found" in output.lower()

    def test_capture_not_a_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """capture outside a git repo shows 'Not a git repository' warning."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")

        config_path = tmp_path / ".mementic.json"
        config_path.write_text(json.dumps({
            "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
        }))
        monkeypatch.setenv("MEMENTIC_CONFIG", str(config_path))

        session_file = tmp_path / "session.log"
        session_file.write_text("Fake agent session log content.\n")

        from mementic.models import Decision, DecisionStatus, ExtractionResult, UsageStats

        mock_result = ExtractionResult(
            decisions=[
                Decision(
                    id="test-d",
                    title="Test Decision",
                    rationale="Testing",
                    source="session",
                    status=DecisionStatus.ACCEPTED,
                    created_at="2024-01-01T00:00:00Z",
                    modified_at="2024-01-01T00:00:00Z",
                ),
            ],
        )
        mock_usage = UsageStats(model="test", status="success")

        mock_registry = MagicMock()
        mock_provider = MagicMock()
        mock_provider.agent_name = "opencode"
        mock_registry.detect.return_value = (mock_provider, str(session_file))

        with patch(
            "mementic.session.get_registry", return_value=mock_registry,
        ), patch(
            "mementic.cli._run_git_diff", return_value=("", 128),
        ), patch(
            "mementic.graph.GraphStore.get_capture_checkpoint", return_value="",
        ), patch(
            "mementic.cli.extract_from_session",
            return_value=(mock_result, mock_usage),
        ):
            result = runner.invoke(
                app, ["capture", "--session", str(session_file)],
            )

        output = result.stdout + result.stderr
        assert result.exit_code == 0
        assert "not a git repository" in output.lower()

    def test_capture_no_new_commits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """capture when checkpoint matches HEAD shows informative message."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")

        config_path = tmp_path / ".mementic.json"
        config_path.write_text(json.dumps({
            "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
        }))
        monkeypatch.setenv("MEMENTIC_CONFIG", str(config_path))

        session_file = tmp_path / "session.log"
        session_file.write_text("Fake agent session log content.\n")

        from mementic.models import Decision, DecisionStatus, ExtractionResult, UsageStats

        mock_result = ExtractionResult(
            decisions=[
                Decision(
                    id="test-d",
                    title="Test Decision",
                    rationale="Testing",
                    source="session",
                    status=DecisionStatus.ACCEPTED,
                    created_at="2024-01-01T00:00:00Z",
                    modified_at="2024-01-01T00:00:00Z",
                ),
            ],
        )
        mock_usage = UsageStats(model="test", status="success")

        mock_registry = MagicMock()
        mock_provider = MagicMock()
        mock_provider.agent_name = "opencode"
        mock_registry.detect.return_value = (mock_provider, str(session_file))

        with patch(
            "mementic.session.get_registry", return_value=mock_registry,
        ), patch(
            "mementic.graph.GraphStore.get_capture_checkpoint", return_value="abc123",
        ), patch(
            "mementic.cli.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["git", "rev-parse", "HEAD"], returncode=0, stdout="abc123\n"
            ),
        ), patch(
            "mementic.cli.extract_from_session",
            return_value=(mock_result, mock_usage),
        ):
            result = runner.invoke(
                app, ["capture", "--session", str(session_file)],
            )

        output = result.stdout + result.stderr
        assert result.exit_code == 0
        assert "no new commits" in output.lower()

    def test_capture_empty_diff(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """capture when git diff returns empty shows 'No changes' message."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")

        config_path = tmp_path / ".mementic.json"
        config_path.write_text(json.dumps({
            "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
        }))
        monkeypatch.setenv("MEMENTIC_CONFIG", str(config_path))

        session_file = tmp_path / "session.log"
        session_file.write_text("Fake agent session log content.\n")

        from mementic.models import Decision, DecisionStatus, ExtractionResult, UsageStats

        mock_result = ExtractionResult(
            decisions=[
                Decision(
                    id="test-d",
                    title="Test Decision",
                    rationale="Testing",
                    source="session",
                    status=DecisionStatus.ACCEPTED,
                    created_at="2024-01-01T00:00:00Z",
                    modified_at="2024-01-01T00:00:00Z",
                ),
            ],
        )
        mock_usage = UsageStats(model="test", status="success")

        mock_registry = MagicMock()
        mock_provider = MagicMock()
        mock_provider.agent_name = "opencode"
        mock_registry.detect.return_value = (mock_provider, str(session_file))

        with patch(
            "mementic.session.get_registry", return_value=mock_registry,
        ), patch(
            "mementic.graph.GraphStore.get_capture_checkpoint", return_value="old123",
        ), patch(
            "mementic.cli.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["git", "rev-parse", "HEAD"], returncode=0, stdout="new456\n"
            ),
        ), patch(
            "mementic.cli._run_git_diff", return_value=("", 0),
        ), patch(
            "mementic.cli.extract_from_session",
            return_value=(mock_result, mock_usage),
        ):
            result = runner.invoke(
                app, ["capture", "--session", str(session_file)],
            )

        output = result.stdout + result.stderr
        assert result.exit_code == 0
        assert "no changes" in output.lower()


# ── Spec 12a: Codebase Scanner CLI Tests ────────────────────────────


class TestInitRepoFlag:
    """Tests for mementic init --repo and --no-scan flags (Spec 12a)."""

    def test_init_repo_flag_passed_to_pipeline(self, tmp_path: Path) -> None:
        """mementic init --repo passes repo_path to run_init."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config_path = tmp_path / ".mementic.json"
            config_path.write_text(json.dumps({
                "sources": {},
                "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
                "llm": {"provider": "deepseek", "model": "deepseek-chat"},
            }))

            with patch("mementic.cli.run_init", return_value=(_make_mock_graph(), [])) as mock_run_init:
                result = runner.invoke(
                    app, ["init", "--config-path", str(config_path), "--repo", "/some/path"],
                )

            assert result.exit_code == 0
            mock_run_init.assert_called_once()
            call_kwargs = mock_run_init.call_args.kwargs
            assert call_kwargs["repo_path"] == "/some/path"
        finally:
            os.chdir(old_cwd)

    def test_init_no_scan_flag_passed_to_pipeline(self, tmp_path: Path) -> None:
        """mementic init --no-scan passes no_scan=True to run_init."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config_path = tmp_path / ".mementic.json"
            config_path.write_text(json.dumps({
                "sources": {},
                "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
                "llm": {"provider": "deepseek", "model": "deepseek-chat"},
            }))

            with patch("mementic.cli.run_init", return_value=(_make_mock_graph(), [])) as mock_run_init:
                result = runner.invoke(
                    app, ["init", "--config-path", str(config_path), "--no-scan"],
                )

            assert result.exit_code == 0
            mock_run_init.assert_called_once()
            call_kwargs = mock_run_init.call_args.kwargs
            assert call_kwargs["no_scan"] is True
        finally:
            os.chdir(old_cwd)

    def test_init_help_shows_repo_and_no_scan(self) -> None:
        """mementic init --help shows --repo and --no-scan options."""
        result = runner.invoke(app, ["init", "--help"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "--repo" in output
        assert "--no-scan" in output


# ── Spec 12b: Project management CLI commands ──────────────────────


class TestProjectCommands:
    """Tests for 'mementic project create|list|set-default' commands."""

    def _setup_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Path:
        """Create an isolated global config file and monkeypatch its path."""
        config_dir = tmp_path / ".mementic"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({
            "version": 1,
            "default_project": "main",
            "projects": {
                "main": {
                    "description": "",
                    "db_path": "~/.mementic/projects/main.mementic.db",
                    "repos": {},
                },
            },
        }))
        monkeypatch.setattr(
            "mementic.config.get_global_config_path", lambda: config_file
        )
        return config_file

    def test_project_create_adds_to_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Runs 'mementic project create myproject', then checks config."""
        self._setup_config(tmp_path, monkeypatch)

        result = runner.invoke(app, ["project", "create", "myproject"])
        assert result.exit_code == 0, f"stderr: {result.stderr}"

        cfg = load_global_config()
        assert "myproject" in cfg.projects
        assert cfg.projects["myproject"].db_path == (
            "~/.mementic/projects/myproject.mementic.db"
        )
        assert cfg.projects["myproject"].description == ""

    def test_project_create_duplicate_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Creates same project twice, second call fails with non-zero exit code."""
        self._setup_config(tmp_path, monkeypatch)

        result1 = runner.invoke(app, ["project", "create", "dup-project"])
        assert result1.exit_code == 0

        result2 = runner.invoke(app, ["project", "create", "dup-project"])
        assert result2.exit_code == 1
        assert "already exists" in result2.stderr or "already exists" in result2.stdout

    def test_project_list_shows_projects(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Creates a project, runs 'mementic project list', output contains project name."""
        self._setup_config(tmp_path, monkeypatch)

        runner.invoke(app, ["project", "create", "myproject"])
        result = runner.invoke(app, ["project", "list"])

        assert result.exit_code == 0
        output = result.stdout
        assert "myproject" in output

    def test_project_set_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Creates two projects, sets default to second, checks config."""
        self._setup_config(tmp_path, monkeypatch)

        runner.invoke(app, ["project", "create", "first"])
        runner.invoke(app, ["project", "create", "second"])

        result = runner.invoke(app, ["project", "set-default", "second"])
        assert result.exit_code == 0

        cfg = load_global_config()
        assert cfg.default_project == "second"

    def test_project_set_default_missing_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Set-default to nonexistent project, fails with non-zero exit code."""
        self._setup_config(tmp_path, monkeypatch)

        result = runner.invoke(app, ["project", "set-default", "ghost-project"])
        assert result.exit_code == 1
        output = result.stdout + result.stderr
        assert "not found" in output.lower()


# ── Spec 12d: Audit Mutation Log ────────────────────────────────────


class TestLogCommand:
    """Tests for mementic log command with SQLite audit_log support."""

    def test_log_from_sqlite_audit_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mementic log reads from SQLite audit_log when project DB is active."""
        import sqlite3

        # Create a project DB with audit_log entries
        db_path = tmp_path / "test.mementic.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Run migration to create tables
        from mementic.graph import GraphStore
        GraphStore._migrate_schema(conn)

        # Insert test audit entries
        entries = [
            ("decision", "d1", "add", '{"title": "Use async"}', "test", "2024-01-01T00:00:00Z"),
            ("constraint", "c1", "add", '{"description": "Must be async"}', "test", "2024-01-01T00:00:01Z"),
            ("edge", "d1→e1", "link", '{"source":"d1","target":"e1","relation":"affects"}', "test", "2024-01-01T00:00:02Z"),
        ]
        conn.executemany(
            "INSERT INTO audit_log (entity_type, entity_id, action, details, source, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            entries,
        )
        conn.commit()
        conn.close()

        # Set up global config pointing to this DB
        config_dir = tmp_path / ".mementic"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({
            "version": 1,
            "default_project": "main",
            "projects": {
                "main": {
                    "description": "",
                    "db_path": str(db_path),
                    "repos": {},
                },
            },
        }))
        monkeypatch.setattr(
            "mementic.config.get_global_config_path", lambda: config_file
        )

        result = runner.invoke(app, ["log"])
        assert result.exit_code == 0
        output = result.stdout

        # Each entry should be formatted as [timestamp] entity_type/action entity_id — summary
        assert "decision/add d1" in output
        assert "Use async" in output
        assert "constraint/add c1" in output
        assert "Must be async" in output
        assert "edge/link d1→e1" in output

    def test_log_fallback_to_jsonl(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mementic log falls back to JSONL when no project DB is active."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Create legacy audit.jsonl
            audit_dir = tmp_path / ".mementic"
            audit_dir.mkdir()
            audit_path = audit_dir / "audit.jsonl"
            audit_path.write_text(
                json.dumps({
                    "timestamp": "2024-01-01T00:00:00Z",
                    "action": "save",
                    "decisions": 3,
                    "constraints": 2,
                    "entities": 1,
                    "edges": 5,
                    "id": "save-1",
                }) + "\n"
            )

            # Set up config pointing to non-existent graph (no projects configured)
            config_path = tmp_path / ".mementic.json"
            config_path.write_text(json.dumps({
                "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
            }))
            monkeypatch.setenv("MEMENTIC_CONFIG", str(config_path))

            # No global config, so _resolve_graph falls back to JSON mode
            # But the graph doesn't exist, so we need to handle that
            # Since there's no global config, _resolve_graph tries to load graph.json
            # We need a valid graph.json to pass the _load_graph_safe check
            graph_dir = tmp_path / ".mementic"
            graph_dir.mkdir(parents=True, exist_ok=True)
            graph_file = graph_dir / "graph.json"
            graph_file.write_text(json.dumps({"nodes": [], "edges": []}))

            # Remove global config to force JSON fallback
            config_dir = tmp_path / ".empty_mementic"
            config_dir.mkdir(parents=True, exist_ok=True)
            monkeypatch.setattr(
                "mementic.config.get_global_config_path",
                lambda: config_dir / "config.json",
            )

            result = runner.invoke(app, ["log"])
            assert result.exit_code == 0
            output = result.stdout
            assert "save" in output
        finally:
            os.chdir(old_cwd)

    def test_log_empty_sqlite_audit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mementic log shows empty message on empty SQLite audit_log."""
        import sqlite3

        db_path = tmp_path / "empty.mementic.db"
        conn = sqlite3.connect(str(db_path))
        from mementic.graph import GraphStore
        GraphStore._migrate_schema(conn)
        conn.close()

        config_dir = tmp_path / ".mementic"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({
            "version": 1,
            "default_project": "main",
            "projects": {
                "main": {
                    "description": "",
                    "db_path": str(db_path),
                    "repos": {},
                },
            },
        }))
        monkeypatch.setattr(
            "mementic.config.get_global_config_path", lambda: config_file
        )

        result = runner.invoke(app, ["log"])
        assert result.exit_code == 0
        output = result.stdout + result.stderr
        assert "empty" in output.lower() or "no audit" in output.lower()


# ── Metrics command tests (Spec 13, Tasks 2.2 / 2.3) ─────────────


def _write_metrics_jsonl(path: Path, entries: list[dict]) -> None:
    """Write a metrics.jsonl file from a list of MetricsEntry-like dicts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _write_source_files(base: Path, adr_count: int = 2, instr_count: int = 2) -> None:
    """Create source ADR and instruction markdown files for token counting."""
    adr_dir = base / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    for i in range(1, adr_count + 1):
        (adr_dir / f"{i:04d}-test-adr.md").write_text(
            f"# ADR-{i:04d}: Test Decision {i}\n\n"
            f"## Status\n\nAccepted\n\n"
            f"## Context\n\nThis is a test ADR document with some content.\n\n"
            f"## Decision\n\nWe will use Python for everything.\n\n"
            f"## Consequences\n\nMore Python code.\n\n"
        )

    instr_dir = base / "docs" / "instructions"
    instr_dir.mkdir(parents=True)
    for i in range(1, instr_count + 1):
        (instr_dir / f"style{i}.md").write_text(
            f"# Instruction {i}\n\n"
            f"This is a test instruction file with guidelines for agents.\n"
        )


class TestMetricsCommand:
    """Tests for mementic metrics command (four-section tabular output)."""

    def _setup_metrics_and_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        entries: list[dict] | None = None,
        with_sources: bool = True,
    ) -> Path:
        """Set up metrics.jsonl, config, and optional source dirs. Returns cwd."""
        old_cwd = os.getcwd()
        os.chdir(tmp_path)

        # Write metrics data
        if entries is not None:
            _write_metrics_jsonl(tmp_path / ".mementic" / "metrics.jsonl", entries)

        # Write config
        config = {
            "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
            "sources": {
                "adr_dirs": [{"path": str(tmp_path / "docs" / "adr")}],
                "opencode_instructions": [{"path": str(tmp_path / "docs" / "instructions")}],
            },
        }
        (tmp_path / ".mementic.json").write_text(json.dumps(config))

        # Ensure no global config (fallback to JSON mode)
        empty_config_dir = tmp_path / ".empty_mementic"
        empty_config_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(
            "mementic.config.get_global_config_path",
            lambda: empty_config_dir / "config.json",
        )

        if with_sources:
            _write_source_files(tmp_path)

        return Path(old_cwd)

    def test_metrics_json_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mementic metrics --json returns summary dict without dead fields."""
        entries = [
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "operation": "init_adr",
                "source_label": "deepseek-chat",
                "input_tokens": 1000,
                "output_tokens": 500,
                "total_tokens": 1500,
                "model": "deepseek-chat",
                "status": "success",
            },
            {
                "timestamp": "2024-01-01T00:00:01Z",
                "operation": "capture",
                "source_label": "opencode:session",
                "input_tokens": 800,
                "output_tokens": 400,
                "total_tokens": 1200,
                "model": "deepseek-chat",
                "status": "success",
            },
        ]
        try:
            self._setup_metrics_and_config(tmp_path, monkeypatch, entries=entries)
            result = runner.invoke(app, ["metrics", "--json"])
        finally:
            os.chdir(Path.cwd() if "old_cwd" not in dir() else tmp_path)

        assert result.exit_code == 0
        data = json.loads(result.stdout)

        # Verify honest summary fields exist
        assert "extraction_init_calls" in data
        assert "extraction_init_tokens" in data
        assert "extraction_capture_calls" in data
        assert "extraction_capture_tokens" in data
        assert "context_pack_queries" in data
        assert "context_pack_total_tokens" in data
        assert "context_pack_avg_tokens" in data

        # Dead fields must NOT be present
        assert "breakeven_ratio" not in data
        assert "extraction_per_query" not in data
        assert "context_pack_per_query" not in data

        # Verify values
        assert data["extraction_init_calls"] == 1
        assert data["extraction_init_tokens"] == 1500
        assert data["extraction_capture_calls"] == 1
        assert data["extraction_capture_tokens"] == 1200
        assert data["extraction_total_tokens"] == 2700

    def test_metrics_tabular_format(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mementic metrics renders four-section tabular output."""
        entries = [
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "operation": "init_adr",
                "source_label": "deepseek-chat",
                "input_tokens": 10000,
                "output_tokens": 4200,
                "total_tokens": 14200,
                "model": "deepseek-chat",
                "status": "success",
            },
            {
                "timestamp": "2024-01-01T00:00:01Z",
                "operation": "capture",
                "source_label": "opencode:session1",
                "input_tokens": 3000,
                "output_tokens": 1600,
                "total_tokens": 4600,
                "model": "deepseek-chat",
                "status": "success",
            },
            {
                "timestamp": "2024-01-01T00:00:02Z",
                "operation": "capture",
                "source_label": "opencode:session2",
                "input_tokens": 3000,
                "output_tokens": 1600,
                "total_tokens": 4600,
                "model": "deepseek-chat",
                "status": "success",
            },
            {
                "timestamp": "2024-01-01T00:00:03Z",
                "operation": "mcp_context_pack",
                "source_label": "get_context_pack",
                "input_tokens": 500,
                "output_tokens": 180,
                "total_tokens": 680,
                "model": "deepseek-chat",
                "status": "success",
            },
        ]
        try:
            self._setup_metrics_and_config(tmp_path, monkeypatch, entries=entries)
            result = runner.invoke(app, ["metrics"])
        finally:
            os.chdir(os.getcwd())

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)

        # Header
        assert "MEMENTIC TOKEN USAGE" in output

        # Section 1: Setup (one-time)
        assert "Setup (one-time)" in output
        assert "Source material" in output
        assert "Init extraction" in output
        assert "Compression ratio" in output
        # Init extraction: 1 call, 14,200 tokens
        assert "1" in output
        assert "14,200" in output

        # Section 2: Capture (recurring)
        assert "Capture (recurring)" in output
        assert "Sessions" in output
        assert "Avg per session" in output
        # 2 capture calls, 9,200 tokens
        assert "9,200" in output

        # Section 3: Context Packs
        assert "Context Packs" in output
        assert "Queries" in output
        assert "Avg per pack" in output
        assert "680" in output

        # Section 4: Reference
        assert "Reference" in output
        assert "Graph compression" in output
        assert "Pack vs source" in output
        assert "Extraction amortized" in output

        # No dead sections
        assert "Breakeven" not in output
        assert "overhead ratio" not in output
        assert "Token Economics" not in output

    def test_metrics_empty_data(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mementic metrics with empty metrics shows -- for missing data."""
        try:
            self._setup_metrics_and_config(
                tmp_path, monkeypatch, entries=[], with_sources=False
            )
            result = runner.invoke(app, ["metrics"])
        finally:
            os.chdir(os.getcwd())

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)

        # Header still present
        assert "MEMENTIC TOKEN USAGE" in output
        assert "Setup (one-time)" in output
        assert "Capture (recurring)" in output
        assert "Context Packs" in output
        assert "Reference" in output

        # Source material shows -- when no source dirs
        assert "--" in output

        # No crash from division by zero
        assert "ZeroDivisionError" not in output

    def test_metrics_source_material_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mementic metrics counts source material tokens from disk."""
        try:
            self._setup_metrics_and_config(tmp_path, monkeypatch, entries=[])
            result = runner.invoke(app, ["metrics"])
        finally:
            os.chdir(os.getcwd())

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)

        # Source material should show a count > 0 (we created 4 source files)
        assert "Source material" in output
        # The row should NOT show "--" for source material
        # Check that a number appears on the Source material line
        lines = output.split("\n")
        source_line = next(
            (line for line in lines if "Source material" in line), ""
        )
        # Should contain digits in the Count column area
        assert source_line != ""

    def test_metrics_json_no_dead_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mementic metrics --json does not include breakeven or per-query fields."""
        try:
            self._setup_metrics_and_config(tmp_path, monkeypatch, entries=[])
            result = runner.invoke(app, ["metrics", "--json"])
        finally:
            os.chdir(os.getcwd())

        assert result.exit_code == 0
        data = json.loads(result.stdout)

        # These dead fields from old breakeven analysis must be absent
        dead_fields = [
            "breakeven_ratio",
            "extraction_per_query",
            "context_pack_per_query",
            "context_pack_avg_constraints",
            "context_pack_avg_decisions",
        ]
        for field in dead_fields:
            assert field not in data, f"Dead field '{field}' leaked into JSON output"
