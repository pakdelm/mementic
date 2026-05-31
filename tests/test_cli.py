"""Tests for mementic CLI commands (Spec 06a D2, D4)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from mementic.cli import app

runner = CliRunner()


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

            with patch("mementic.cli.run_init"):
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

            with patch("mementic.cli.run_init"):
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

            with patch("mementic.cli.run_init"):
                result = runner.invoke(
                    app, ["init", "--config-path", str(config_path)],
                )

            assert result.exit_code == 0
            loaded = json.loads(config_path.read_text())
            assert loaded["sources"]["adr_dirs"][0]["path"] == "/custom/path"
        finally:
            os.chdir(old_cwd)


class TestInitInteractive:
    """Tests for mementic init --interactive."""

    def test_init_interactive_updates_config(
        self, tmp_path: Path
    ) -> None:
        """mementic init --interactive prompts user and updates config."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config_path = tmp_path / ".mementic.json"
            config_path.write_text(json.dumps({
                "sources": {},
                "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
                "llm": {"provider": "deepseek", "model": "deepseek-chat"},
            }))

            with patch("mementic.cli.run_init"):
                result = runner.invoke(
                    app,
                    ["init", "--config-path", str(config_path), "--interactive"],
                    input="openai\ngpt-4o\n",
                )

            assert result.exit_code == 0
            loaded = json.loads(config_path.read_text())
            assert loaded["llm"]["provider"] == "openai"
            assert loaded["llm"]["model"] == "gpt-4o"
        finally:
            os.chdir(old_cwd)

    def test_init_interactive_defaults_kept_on_empty_input(
        self, tmp_path: Path
    ) -> None:
        """mementic init --interactive with empty inputs keeps defaults."""
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config_path = tmp_path / ".mementic.json"
            config_path.write_text(json.dumps({
                "sources": {},
                "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
                "llm": {"provider": "deepseek", "model": "deepseek-chat"},
            }))

            with patch("mementic.cli.run_init"):
                result = runner.invoke(
                    app,
                    ["init", "--config-path", str(config_path), "--interactive"],
                    input="\n\n",
                )

            assert result.exit_code == 0
            loaded = json.loads(config_path.read_text())
            assert loaded["llm"]["provider"] == "deepseek"
            assert loaded["llm"]["model"] == "deepseek-chat"
        finally:
            os.chdir(old_cwd)


# ── D4: graph corrupt error message ───────────────────────────────


class TestGraphCorruptMessage:
    """Tests for D4: corrupt graph file error message."""

    def test_graph_corrupt_message_on_invalid_json(
        self, tmp_path: Path
    ) -> None:
        """Graph file with invalid JSON shows corrupt message."""
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
        assert result.exit_code == 1
        output = result.stdout + result.stderr
        assert "corrupt" in output.lower()

    def test_graph_missing_shows_init_message(
        self, tmp_path: Path
    ) -> None:
        """Missing graph file shows 'run mementic init' message."""
        graph_path = tmp_path / ".mementic" / "graph.json"

        config_path = tmp_path / ".mementic.json"
        config_path.write_text(json.dumps({
            "output": {"graph_path": str(graph_path)},
        }))

        result = runner.invoke(
            app, ["status"],
            env={"MEMENTIC_CONFIG": str(config_path)},
        )
        assert result.exit_code == 1
        output = result.stdout + result.stderr
        assert "init" in output.lower()

    def test_review_graph_corrupt_message(
        self, tmp_path: Path
    ) -> None:
        """review command shows corrupt message for invalid graph."""
        graph_path = tmp_path / ".mementic" / "graph.json"
        graph_path.parent.mkdir(parents=True)
        graph_path.write_text("{invalid")

        config_path = tmp_path / ".mementic.json"
        config_path.write_text(json.dumps({
            "output": {"graph_path": str(graph_path)},
        }))

        result = runner.invoke(
            app, ["review"],
            env={"MEMENTIC_CONFIG": str(config_path)},
        )
        assert result.exit_code == 1
        output = result.stdout + result.stderr
        assert "corrupt" in output.lower()


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
