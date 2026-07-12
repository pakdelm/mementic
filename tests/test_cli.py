"""Tests for mementic CLI commands (Spec 06a D2, D4, 06c)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from mementic.cli import app

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




# ── D4: graph corrupt error message (removed — Spec 22) ──────────
# _load_graph_safe and graph.json fallback were removed.
# SQLite is crash-safe by design; no corrupt JSON edge case to handle.


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

        from mementic.graph import GraphStore
        from mementic.models import Decision, DecisionStatus, ExtractionResult

        graph = GraphStore(db_path=None)

        session_file = tmp_path / "session.log"
        session_file.write_text("Agent session about adding auth.\n")

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
            "mementic.cli._resolve_graph", return_value=graph,
        ), patch(
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

        from mementic.graph import GraphStore
        from mementic.models import Decision, DecisionStatus, ExtractionResult

        graph = GraphStore(db_path=None)
        existing = Decision(
            id="use-jwt-auth",
            title="Use JWT Auth",
            rationale="Token-based auth for APIs",
            source="ADR-001",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        graph.add_decision(existing)

        session_file = tmp_path / "session.log"
        session_file.write_text("Session about JWT auth updates.\n")

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
            "mementic.cli._resolve_graph", return_value=graph,
        ), patch(
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

        from mementic.graph import GraphStore
        from mementic.models import Decision, DecisionStatus, ExtractionResult

        graph = GraphStore(db_path=None)

        session_file = tmp_path / "session.log"
        session_file.write_text("Session log content.\n")

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
            "mementic.cli._resolve_graph", return_value=graph,
        ), patch(
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
        from mementic.graph import GraphStore
        from mementic.models import Decision, DecisionStatus

        graph = GraphStore(db_path=None)
        graph.add_decision(Decision(
            id="use-jwt",
            title="Use JWT Auth",
            rationale="Tokens",
            source="test",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-05-15T00:00:00Z",
            modified_at="2024-05-15T00:00:00Z",
        ))
        graph.add_decision(Decision(
            id="use-async",
            title="Use async I/O",
            rationale="Performance",
            source="test",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-05-20T00:00:00Z",
            modified_at="2024-05-20T00:00:00Z",
        ))

        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(app, ["list", "decisions"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "use-jwt" in output
        assert "use-async" in output
        assert "Use JWT Auth" in output

    def test_list_decisions_by_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mementic.graph import GraphStore
        from mementic.models import Decision, DecisionStatus

        graph = GraphStore(db_path=None)
        graph.add_decision(Decision(
            id="d-accepted",
            title="Accepted",
            rationale="...",
            source="test",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        ))
        graph.add_decision(Decision(
            id="d-deprecated",
            title="Deprecated",
            rationale="...",
            source="test",
            status=DecisionStatus.DEPRECATED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        ))

        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(app, ["list", "decisions", "--status", "deprecated"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "d-deprecated" in output
        assert "d-accepted" not in output

    def test_list_decisions_last_n(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mementic.graph import GraphStore
        from mementic.models import Decision, DecisionStatus

        graph = GraphStore(db_path=None)
        for i in range(1, 6):
            graph.add_decision(Decision(
                id=f"d-{i}",
                title=f"Decision {i}",
                rationale="...",
                source="test",
                status=DecisionStatus.ACCEPTED,
                created_at=f"2024-01-{i:02d}T00:00:00Z",
                modified_at=f"2024-01-{i:02d}T00:00:00Z",
            ))

        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(app, ["list", "decisions", "-n", "2"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "d-4" in output
        assert "d-5" in output
        assert "d-1" not in output

    def test_list_constraints(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mementic.graph import GraphStore
        from mementic.models import Constraint

        graph = GraphStore(db_path=None)
        graph.add_constraint(Constraint(
            id="c-stateless",
            description="Services must be stateless",
            bound_entities=[],
            enforced_by="use-jwt",
            source="test",
            status="active",
            created_at="2024-01-01T00:00:00Z",
        ))

        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(app, ["list", "constraints"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "c-stateless" in output
        assert "Services must be stateless" in output

    def test_list_entities(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mementic.graph import GraphStore
        from mementic.models import CodeEntity, EntityType

        graph = GraphStore(db_path=None)
        graph.add_code_entity(CodeEntity(
            id="src/auth.py", type=EntityType.FILE, repo="my-repo",
        ))
        graph.add_code_entity(CodeEntity(
            id="src/models/user.py", type=EntityType.FILE, repo="my-repo",
        ))

        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(app, ["list", "entities"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "src/auth.py" in output
        assert "my-repo" in output

    def test_list_entities_stale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mementic.graph import GraphStore
        from mementic.models import CodeEntity, EntityType

        monkeypatch.chdir(tmp_path)
        stale_id = str(tmp_path / "does_not_exist.py")

        graph = GraphStore(db_path=None)
        graph.add_code_entity(CodeEntity(
            id=stale_id, type=EntityType.FILE, repo="",
        ))

        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(app, ["list", "entities", "--stale"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "does_not_exist.py" in output


# ── 06c F4: deprecate command ──────────────────────────────────────


class TestDeprecateCommand:
    """Tests for mementic deprecate command (Spec 06c F4)."""

    def test_deprecate_marks_decision_deprecated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mementic.graph import GraphStore
        from mementic.models import Decision, DecisionStatus

        graph = GraphStore(db_path=None)
        graph.add_decision(Decision(
            id="use-old-auth",
            title="Use Old Auth",
            rationale="Basic session auth",
            source="test",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        ))

        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(app, ["deprecate", "use-old-auth"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "Deprecated" in output or "deprecated" in output.lower()
        assert "use-old-auth" in output

    def test_deprecate_with_reason_appends_to_rationale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mementic.graph import GraphStore
        from mementic.models import Decision, DecisionStatus

        graph = GraphStore(db_path=None)
        graph.add_decision(Decision(
            id="use-old-auth",
            title="Use Old Auth",
            rationale="Basic session auth",
            source="test",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        ))

        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(
                app, ["deprecate", "use-old-auth", "--reason", "Migrated to JWT"]
            )

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "Deprecated" in output

    def test_deprecate_nonexistent_shows_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mementic.graph import GraphStore

        graph = GraphStore(db_path=None)

        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(app, ["deprecate", "nonexistent-id"])

        assert result.exit_code == 1
        output = result.stdout + result.stderr
        assert "not found" in output.lower()

    def test_deprecate_already_deprecated_shows_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mementic.graph import GraphStore
        from mementic.models import Decision, DecisionStatus

        graph = GraphStore(db_path=None)
        graph.add_decision(Decision(
            id="already-dep",
            title="Already Deprecated",
            rationale="Old stuff",
            source="test",
            status=DecisionStatus.DEPRECATED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        ))

        with patch("mementic.cli._resolve_graph", return_value=graph):
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


# ── JSON output modes ───────────────────────────────────────────────


class TestQueryKeywordsJson:
    """Tests for `mementic query keywords --json` output."""
    
    def test_query_keywords_json_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """query keywords --json returns JSON with summary, constraints, decisions."""
        from mementic.graph import GraphStore
        from mementic.models import (
            CodeEntity,
            Constraint,
            Decision,
            DecisionStatus,
            EntityType,
        )
        
        graph = GraphStore(db_path=None)
        
        d = Decision(
            id="use-jwt",
            title="Use JWT Auth",
            rationale="Token-based authentication for API endpoints",
            source="cli",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
            affected_entities=["src/auth.py"],
        )
        c = Constraint(
            id="c-stateless",
            description="Services must be stateless",
            bound_entities=["src/auth.py"],
            enforced_by="use-jwt",
            source="cli",
            status="active",
            created_at="2024-01-01T00:00:00Z",
        )
        e = CodeEntity(id="src/auth.py", type=EntityType.FILE, repo="my-repo")
        
        graph.add_decision(d)
        graph.add_constraint(c)
        graph.add_code_entity(e)
        
        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(app, ["query", "keywords", "auth", "--json"])
        
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        
        assert "summary" in data
        assert "constraints" in data
        assert "decisions" in data
        
        # Constraint fields
        for c_data in data["constraints"]:
            assert "id" in c_data
            assert "description" in c_data
            assert "entities" in c_data
            assert "status" in c_data
        
        # Decision fields
        for d_data in data["decisions"]:
            assert "id" in d_data
            assert "title" in d_data
            assert "rationale" in d_data
            assert "entities" in d_data
            assert "status" in d_data
    
    def test_query_keywords_json_shape_with_matches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """query keywords --json returns matched items when keywords overlap."""
        from mementic.graph import GraphStore
        from mementic.models import (
            CodeEntity,
            Constraint,
            Decision,
            DecisionStatus,
            EntityType,
        )
        
        graph = GraphStore(db_path=None)
        
        d = Decision(
            id="use-jwt",
            title="Use JWT Auth",
            rationale="Token-based authentication for APIs",
            source="cli",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        c = Constraint(
            id="c-stateless",
            description="All services must be stateless",
            bound_entities=["src/services.py"],
            enforced_by="use-jwt",
            source="cli",
            status="active",
            created_at="2024-01-01T00:00:00Z",
        )
        e = CodeEntity(id="src/services.py", type=EntityType.FILE, repo="my-repo")
        
        graph.add_decision(d)
        graph.add_constraint(c)
        graph.add_code_entity(e)
        
        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(app, ["query", "keywords", "auth", "--json"])
        
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        
        assert isinstance(data["summary"], str)
        assert isinstance(data["constraints"], list)
        assert isinstance(data["decisions"], list)
        # "auth" keyword should match the decision with "Token-based authentication"
        assert len(data["decisions"]) >= 1
        assert data["decisions"][0]["id"] == "use-jwt"


class TestQueryFileJson:
    """Tests for `mementic query file --json` output."""
    
    def test_query_file_json_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """query file --json returns JSON with summary, file, decisions, constraints."""
        from mementic.graph import GraphStore
        from mementic.models import (
            CodeEntity,
            Constraint,
            Decision,
            DecisionStatus,
            EntityType,
        )
        
        graph = GraphStore(db_path=None)
        
        d = Decision(
            id="use-jwt",
            title="Use JWT Auth",
            rationale="Token-based authentication for API endpoints",
            source="cli",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        c = Constraint(
            id="c-stateless",
            description="Services must be stateless",
            bound_entities=["src/auth.py"],
            enforced_by="use-jwt",
            source="cli",
            status="active",
            created_at="2024-01-01T00:00:00Z",
        )
        e = CodeEntity(id="src/auth.py", type=EntityType.FILE, repo="my-repo")
        
        graph.add_decision(d)
        graph.add_constraint(c)
        graph.add_code_entity(e)
        graph.link("use-jwt", "c-stateless", "enforces")
        graph.link("use-jwt", "src/auth.py", "affects")
        
        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(app, ["query", "file", "src/auth.py", "--json"])
        
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        
        assert "summary" in data
        assert "file" in data
        assert "decisions" in data
        assert "constraints" in data
        assert data["file"] == "src/auth.py"
        assert isinstance(data["summary"], str)
    
    def test_query_file_json_includes_file_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """query file --json includes the queried file path in output."""
        from mementic.graph import GraphStore
        from mementic.models import (
            CodeEntity,
            Constraint,
            Decision,
            DecisionStatus,
            EntityType,
        )
        
        graph = GraphStore(db_path=None)
        
        d = Decision(
            id="use-async",
            title="Use Async I/O",
            rationale="Async everywhere",
            source="cli",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        c = Constraint(
            id="c-async",
            description="All I/O must be async",
            bound_entities=["src/io.py"],
            enforced_by="use-async",
            source="cli",
            status="active",
            created_at="2024-01-01T00:00:00Z",
        )
        e = CodeEntity(id="src/io.py", type=EntityType.FILE, repo="my-repo")
        
        graph.add_decision(d)
        graph.add_constraint(c)
        graph.add_code_entity(e)
        graph.link("use-async", "c-async", "enforces")
        graph.link("c-async", "src/io.py", "binds")
        
        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(app, ["query", "file", "src/io.py", "--json"])
        
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["file"] == "src/io.py"


class TestDeprecateMultiId:
    """Tests for `mementic deprecate` with multiple IDs."""
    
    def test_deprecate_multiple_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """deprecate with multiple IDs deprecates all and reports count."""
        from mementic.graph import GraphStore
        from mementic.models import Decision, DecisionStatus
        
        graph = GraphStore(db_path=None)
        
        d1 = Decision(
            id="dec-1",
            title="Decision 1",
            rationale="First decision",
            source="cli",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        d2 = Decision(
            id="dec-2",
            title="Decision 2",
            rationale="Second decision",
            source="cli",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        
        graph.add_decision(d1)
        graph.add_decision(d2)
        
        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(app, ["deprecate", "dec-1", "dec-2"])
        
        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "2 deprecated" in output
    
    def test_deprecate_mixed_valid_and_invalid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """deprecate with mixed valid/invalid IDs reports count for each."""
        from mementic.graph import GraphStore
        from mementic.models import Decision, DecisionStatus
        
        graph = GraphStore(db_path=None)
        
        d = Decision(
            id="dec-valid",
            title="Valid Decision",
            rationale="A valid decision",
            source="cli",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        
        graph.add_decision(d)
        
        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(app, ["deprecate", "dec-valid", "nonexistent"])
        
        assert result.exit_code == 1  # errors → exit code 1
        output = _strip_ansi(result.stdout)
        assert "1 deprecated" in output
        assert "1 errors" in output


class TestInitCommand:
    """Tests for mementic init command."""

    def test_init_help_shows_provider_and_model_flags(
        self,
    ) -> None:
        """init --help shows --provider and --model flags."""
        result = runner.invoke(app, ["init", "--help"])
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        output = _strip_ansi(result.stdout)
        assert "--provider" in output
        assert "--model" in output

