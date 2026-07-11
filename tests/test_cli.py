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
from mementic.config import GlobalConfig, ProjectEntry, RepoEntry, load_global_config

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

    def _find_registered_repo(
        self, repo_path: Path
    ) -> "RepoEntry | None":
        """Find the repo entry registered in the global config for repo_path."""
        global_cfg = load_global_config()
        resolved = repo_path.resolve()
        for proj in global_cfg.projects.values():
            for repo in proj.repos.values():
                try:
                    if Path(repo.path).resolve() == resolved:
                        return repo
                except (OSError, ValueError):
                    continue
        return None

    def test_init_autocreate_config_when_missing(
        self, tmp_path: Path
    ) -> None:
        """mementic init registers repo with empty sources in global config."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Create source dirs to verify they are NOT auto-discovered
            # (pipeline Phase 0 handles markdown scanning separately)
            adr_dir = tmp_path / "docs" / "adr"
            adr_dir.mkdir(parents=True)
            (adr_dir / "0001-test.md").write_text(
                "# ADR-0001\n\nStatus: Accepted\n\nContext: test\n\nDecision: test"
            )

            instr_dir = tmp_path / "docs" / "instructions"
            instr_dir.mkdir(parents=True)
            (instr_dir / "style.md").write_text("# Style")

            with patch("mementic.cli.run_init", return_value=_make_mock_graph()):
                result = runner.invoke(app, ["init"])

            assert result.exit_code == 0
            repo_entry = self._find_registered_repo(tmp_path)
            assert repo_entry is not None
            # Sources are empty — pipeline Phase 0 handles markdown scanning.
            assert not repo_entry.sources.adr_dirs
            assert not repo_entry.sources.opencode_instructions
        finally:
            os.chdir(old_cwd)

    def test_init_no_sources_discovered_is_warning(
        self, tmp_path: Path
    ) -> None:
        """mementic init succeeds with a warning when no source dirs are found."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            with patch("mementic.cli.run_init", return_value=_make_mock_graph()):
                result = runner.invoke(app, ["init"])

            assert result.exit_code == 0
            output = result.stdout + result.stderr
            assert "No sources discovered" in output
            # Repo still registered with empty sources
            repo_entry = self._find_registered_repo(tmp_path)
            assert repo_entry is not None
        finally:
            os.chdir(old_cwd)

    def test_init_existing_registration_not_overwritten(
        self, tmp_path: Path
    ) -> None:
        """When repo is already registered, init uses existing sources without overwriting."""
        from mementic.config import (
            GlobalConfig,
            ProjectEntry,
            RepoEntry,
            Sources,
            AdrDirSource,
            save_global_config,
        )

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Pre-register the repo in the global config with custom sources
            repo_abs = str(tmp_path.resolve())
            global_cfg = GlobalConfig(
                default_project="main",
                projects={
                    "main": ProjectEntry(
                        db_path=str(tmp_path / "test.mementic.db"),
                        repos={
                            repo_abs: RepoEntry(
                                path=repo_abs,
                                label=tmp_path.name,
                                sources=Sources(
                                    adr_dirs=[AdrDirSource(path="/custom/path")],
                                ),
                            ),
                        },
                    ),
                },
            )
            save_global_config(global_cfg)

            with patch("mementic.cli.run_init", return_value=_make_mock_graph()):
                result = runner.invoke(app, ["init"])

            assert result.exit_code == 0
            repo_entry = self._find_registered_repo(tmp_path)
            assert repo_entry is not None
            assert repo_entry.sources.adr_dirs[0].path == "/custom/path"
        finally:
            os.chdir(old_cwd)

class TestInitInteractive:
    """Tests for mementic init API key setup prompt."""

    def _enable_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make the CliRunner's replacement stdin report as a TTY and clear API keys.

        CliRunner replaces sys.stdin with a click.testing._NamedTextIOWrapper
        instance whose isatty() returns False. Patching the subclass method
        (a Python subclass of io.TextIOWrapper, which is itself immutable)
        makes the prompt gate in mementic init open during invoke.

        Also clears all provider API key env vars so the prompt gate
        (``not api_key``) is reached — mementic.cli calls load_dotenv() at
        import, which may populate DEEPSEEK_API_KEY from a developer .env.
        """
        from typer.testing import _NamedTextIOWrapper
        monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)
        for env_var in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
            monkeypatch.delenv(env_var, raising=False)

    def test_init_api_key_prompt_updates_global_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mementic init prompts for LLM provider/key and updates global config."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            self._enable_tty(monkeypatch)

            with patch("mementic.cli.run_init", return_value=_make_mock_graph()), \
                 patch("mementic.cli.typer.confirm", return_value=True), \
                 patch(
                     "mementic.cli.typer.prompt",
                     side_effect=["openai", "sk-fake-key", "gpt-4o"],
                 ):
                result = runner.invoke(app, ["init"])

            assert result.exit_code == 0
            global_cfg = load_global_config()
            assert global_cfg.llm.provider == "openai"
            assert global_cfg.llm.model == "gpt-4o"
            assert global_cfg.llm.extraction_model == "gpt-4o"
        finally:
            os.chdir(old_cwd)

    def test_init_api_key_prompt_empty_keeps_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mementic init with empty prompt inputs keeps default provider/model."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            self._enable_tty(monkeypatch)

            # Mock prompt to return default values (empty input → defaults)
            with patch("mementic.cli.run_init", return_value=_make_mock_graph()), \
                 patch("mementic.cli.typer.confirm", return_value=True), \
                 patch(
                     "mementic.cli.typer.prompt",
                     side_effect=["deepseek", "", "deepseek-chat"],
                 ):
                result = runner.invoke(app, ["init"])

            assert result.exit_code == 0
            global_cfg = load_global_config()
            assert global_cfg.llm.provider == "deepseek"
            assert global_cfg.llm.model == "deepseek-chat"
        finally:
            os.chdir(old_cwd)

    def test_init_api_key_prompt_skipped_without_tty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mementic init skips the API key prompt when stdin is not a TTY."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            # CliRunner stdin is not a TTY by default — prompt is skipped
            monkeypatch.setattr("sys.stdin.isatty", lambda: False)

            with patch("mementic.cli.run_init", return_value=_make_mock_graph()):
                result = runner.invoke(app, ["init"])

            assert result.exit_code == 0
            # Provider remains default; no prompt-related output
            output = result.stdout + result.stderr
            assert "Set up an LLM provider" not in output
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
        assert "[FAIL]" in output  # STATUS_FAIL icon
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
        assert "[FAIL]" in output  # STATUS_FAIL icon
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

        # Create a fake session file to avoid "No session found"
        session_file = tmp_path / "session.log"
        session_file.write_text("Fake agent session log content.\n")

        from mementic.models import ExtractionResult

        with patch(
            "mementic.cli._extract_session",
            return_value=ExtractionResult(),
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
        # Graph is at tmp_path/.mementic/graph.json (default legacy path)

        session_file = tmp_path / "session.log"
        session_file.write_text("Agent session about adding auth.\n")

        from mementic.models import Decision, DecisionStatus, ExtractionResult

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

        with patch(
            "mementic.cli._extract_session",
            return_value=mock_result,
        ), patch(
            "mementic.cli._run_git_diff", return_value=("", 0),
        ):
            result = runner.invoke(
                app, ["capture", "--session", str(session_file)],
            )

        output = result.stdout + result.stderr
        # Should show new decisions
        assert "New" in output
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
                "created_at": "2024-01-01T00:00:00Z",
                "modified_at": "2024-01-01T00:00:00Z",
                "tags": ["auth"],
                "rejected_alternatives": [],
                "author": "test",
            }],
        )

        session_file = tmp_path / "session.log"
        session_file.write_text("Session about JWT auth updates.\n")

        from mementic.models import Decision, DecisionStatus, ExtractionResult

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

        with patch(
            "mementic.cli._extract_session",
            return_value=mock_result,
        ), patch(
            "mementic.cli._run_git_diff", return_value=("", 0),
        ):
            result = runner.invoke(
                app, ["capture", "--session", str(session_file)],
            )

        output = result.stdout + result.stderr
        assert "Updated" in output
        assert "(existing deprecated)" in output

    def test_capture_no_interactive_prompts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Capture produces no interactive y/n prompts."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")

        config_path, graph_path = _write_config_and_graph(tmp_path)

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
            "mementic.cli._extract_session",
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

    def test_list_decisions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path, _graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[
                {
                    "id": "use-jwt",
                    "title": "Use JWT Auth",
                    "rationale": "Tokens",
                    "source": "test",
                    "status": "accepted",
                    "created_at": "2024-05-15T00:00:00Z",
                    "modified_at": "2024-05-15T00:00:00Z",
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
                    "created_at": "2024-05-20T00:00:00Z",
                    "modified_at": "2024-05-20T00:00:00Z",
                    "tags": ["perf"],
                    "rejected_alternatives": [],
                    "author": "test",
                },
            ],
        )
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["list", "decisions"])
        assert result.exit_code == 0
        output = result.stdout
        assert "use-jwt" in output
        assert "use-async" in output
        assert "Use JWT Auth" in output

    def test_list_decisions_by_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path, _graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[
                {
                    "id": "d-accepted",
                    "title": "Accepted",
                    "rationale": "...",
                    "source": "test",
                    "status": "accepted",
                    "created_at": "2024-01-01T00:00:00Z",
                    "modified_at": "2024-01-01T00:00:00Z",
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
                    "created_at": "2024-01-01T00:00:00Z",
                    "modified_at": "2024-01-01T00:00:00Z",
                    "tags": [],
                    "rejected_alternatives": [],
                    "author": "test",
                },
            ],
        )
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["list", "decisions", "--status", "deprecated"])
        assert result.exit_code == 0
        output = result.stdout
        assert "d-deprecated" in output
        assert "d-accepted" not in output

    def test_list_decisions_last_n(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path, _graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[
                {
                    "id": f"d-{i}",
                    "title": f"Decision {i}",
                    "rationale": "...",
                    "source": "test",
                    "status": "accepted",
                    "created_at": f"2024-01-{i:02d}T00:00:00Z",
                    "modified_at": f"2024-01-{i:02d}T00:00:00Z",
                    "tags": [],
                    "rejected_alternatives": [],
                    "author": "test",
                }
                for i in range(1, 6)
            ],
        )
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["list", "decisions", "-n", "2"])
        assert result.exit_code == 0
        output = result.stdout
        assert "d-4" in output
        assert "d-5" in output
        assert "d-1" not in output

    def test_list_constraints(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
                    "created_at": "2024-01-01T00:00:00Z",
                },
            ],
        )
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["list", "constraints"])
        assert result.exit_code == 0
        output = result.stdout
        assert "c-stateless" in output
        assert "Services must be stateless" in output

    def test_list_entities(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["list", "entities"])
        assert result.exit_code == 0
        output = result.stdout
        assert "src/auth.py" in output
        assert "my-repo" in output

    def test_list_entities_stale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["list", "entities", "--stale"])
        assert result.exit_code == 0
        output = result.stdout
        assert "does_not_exist.py" in output


# ── 06c F4: deprecate command ──────────────────────────────────────


class TestDeprecateCommand:
    """Tests for mementic deprecate command (Spec 06c F4)."""

    def test_deprecate_marks_decision_deprecated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path, graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[{
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
            }],
        )
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["deprecate", "use-old-auth"])
        assert result.exit_code == 0
        output = result.stdout
        assert "Deprecated" in output or "deprecated" in output.lower()
        assert "use-old-auth" in output

    def test_deprecate_with_reason_appends_to_rationale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path, graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[{
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
            }],
        )
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(
            app, ["deprecate", "use-old-auth", "--reason", "Migrated to JWT"]
        )
        assert result.exit_code == 0
        output = result.stdout
        assert "Deprecated" in output

    def test_deprecate_nonexistent_shows_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path, graph_path = _write_config_and_graph(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["deprecate", "nonexistent-id"])
        assert result.exit_code == 1
        output = result.stdout + result.stderr
        assert "not found" in output.lower()

    def test_deprecate_already_deprecated_shows_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path, graph_path = _write_config_and_graph(
            tmp_path,
            decisions=[{
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
            }],
        )
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["deprecate", "already-dep"])
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

        session_file = tmp_path / "session.log"
        session_file.write_text("Fake agent session log content.\n")

        from mementic.models import Decision, DecisionStatus, ExtractionResult

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
            "mementic.cli._extract_session",
            return_value=mock_result,
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

        session_file = tmp_path / "session.log"
        session_file.write_text("Fake agent session log content.\n")

        from mementic.models import Decision, DecisionStatus, ExtractionResult

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
            "mementic.cli._extract_session",
            return_value=mock_result,
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

        session_file = tmp_path / "session.log"
        session_file.write_text("Fake agent session log content.\n")

        from mementic.models import Decision, DecisionStatus, ExtractionResult

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
            "mementic.cli._extract_session",
            return_value=mock_result,
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

        session_file = tmp_path / "session.log"
        session_file.write_text("Fake agent session log content.\n")

        from mementic.models import Decision, DecisionStatus, ExtractionResult

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
            "mementic.cli._extract_session",
            return_value=mock_result,
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

    def test_init_repo_flag_passed_to_pipeline(
        self, tmp_path: Path
    ) -> None:
        """mementic init --repo passes repo_path to run_init."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            with patch("mementic.cli.run_init", return_value=_make_mock_graph()) as mock_run_init:
                result = runner.invoke(app, ["init", "--repo", "/some/path"])

            assert result.exit_code == 0
            mock_run_init.assert_called_once()
            call_kwargs = mock_run_init.call_args.kwargs
            assert call_kwargs["repo_path"] == "/some/path"
        finally:
            os.chdir(old_cwd)

    def test_init_no_scan_flag_passed_to_pipeline(
        self, tmp_path: Path
    ) -> None:
        """mementic init --no-scan passes no_scan=True to run_init."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            with patch("mementic.cli.run_init", return_value=_make_mock_graph()) as mock_run_init:
                result = runner.invoke(app, ["init", "--no-scan"])

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

    # ── Spec 17: project delete ─────────────────────────────────────

    def test_project_delete_removes_project(self) -> None:
        """Delete a project from config."""
        config = GlobalConfig(
            projects={
                "main": ProjectEntry(db_path="/tmp/main.db"),
                "platform": ProjectEntry(db_path="/tmp/platform.db"),
            }
        )
        with patch("mementic.cli.load_global_config", return_value=config), \
             patch("mementic.config.load_global_config", return_value=config), \
             patch("mementic.config.save_global_config"):
            result = runner.invoke(
                app, ["project", "delete", "platform", "--force", "--keep-db"]
            )
        assert result.exit_code == 0
        assert "Deleted project 'platform'" in result.output

    def test_project_delete_last_project_errors(self) -> None:
        """Cannot delete the last remaining project."""
        config = GlobalConfig(
            projects={
                "main": ProjectEntry(db_path="/tmp/main.db"),
            }
        )
        with patch("mementic.cli.load_global_config", return_value=config), \
             patch("mementic.config.load_global_config", return_value=config):
            result = runner.invoke(app, ["project", "delete", "main"])
        assert result.exit_code == 1
        assert "last remaining project" in result.output

    def test_project_delete_nonexistent_errors(self) -> None:
        """Deleting a nonexistent project fails."""
        config = GlobalConfig(
            projects={
                "main": ProjectEntry(db_path="/tmp/main.db"),
            }
        )
        with patch("mementic.cli.load_global_config", return_value=config), \
             patch("mementic.config.load_global_config", return_value=config):
            result = runner.invoke(app, ["project", "delete", "ghost"])
        assert result.exit_code == 1
        assert "not found" in result.output

    # ── Spec 17: project info ───────────────────────────────────────

    def test_project_info_shows_details(self) -> None:
        """Project info shows project details."""
        config = GlobalConfig(
            default_project="main",
            projects={
                "main": ProjectEntry(
                    db_path="/tmp/main.db",
                    description="Primary project",
                    repos={
                        "/dev/my-app": RepoEntry(
                            path="/dev/my-app", label="app"
                        ),
                    },
                ),
            }
        )
        with patch("mementic.cli.load_global_config", return_value=config):
            result = runner.invoke(app, ["project", "info"])
        assert result.exit_code == 0
        assert "main" in result.output
        assert "(default)" in result.output
        assert "/dev/my-app" in result.output
        assert "[app]" in result.output or "app" in result.output

    def test_project_info_defaults_to_default_project(self) -> None:
        """Project info without name argument defaults to default_project."""
        config = GlobalConfig(
            default_project="platform",
            projects={
                "main": ProjectEntry(db_path="/tmp/main.db"),
                "platform": ProjectEntry(db_path="/tmp/platform.db"),
            }
        )
        with patch("mementic.cli.load_global_config", return_value=config):
            result = runner.invoke(app, ["project", "info"])
        assert result.exit_code == 0
        assert "platform" in result.output

    def test_project_info_nonexistent_errors(self) -> None:
        """Project info for nonexistent project fails."""
        config = GlobalConfig(
            projects={
                "main": ProjectEntry(db_path="/tmp/main.db"),
            }
        )
        with patch("mementic.cli.load_global_config", return_value=config):
            result = runner.invoke(app, ["project", "info", "ghost"])
        assert result.exit_code == 1
        assert "not found" in result.output


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
        output = _strip_ansi(result.stdout)

        # Each entry is rendered in a Rich table with columns:
        # Timestamp | Action | Rule ID | Summary
        assert "decision/add" in output
        assert "d1" in output
        assert "Use async" in output
        assert "constraint/add" in output
        assert "c1" in output
        assert "Must be async" in output
        assert "edge/link" in output
        assert "d1→e1" in output

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

