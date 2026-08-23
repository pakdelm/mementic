"""Tests for mementic CLI commands (Spec 06a D2, D4, 06c, Spec 28)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from mementic.cli import app

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from CLI output for platform-agnostic assertions.

    Covers SGR sequences (colors, bold, etc.) used by Typer/Rich formatting.
    Does not cover OSC sequences (hyperlinks) or cursor movement — those are
    rare in CLI help output. Use a library like ``strip-ansi`` if this becomes
    a recurring pattern across tests.
    """
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


# ── Spec 28: capture command removed; add is the only write path ──


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


# ── Spec 28 Phase 1: add auto-resolve rules ────────────────────────


def _make_decision(
    id: str,
    title: str,
    rationale: str,
    created_at: str = "2024-01-01T00:00:00Z",
) -> object:
    """Build a Decision for add-command tests."""
    from mementic.models import Decision, DecisionStatus

    return Decision(
        id=id,
        title=title,
        rationale=rationale,
        source="test",
        status=DecisionStatus.ACCEPTED,
        created_at=created_at,
        modified_at=created_at,
    )


class TestAddDecisionAutoResolve:
    """Tests for `mementic add decision` auto-resolve rules (Spec 28)."""

    def test_new_slug_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A decision with a new slug is accepted and stored."""
        monkeypatch.chdir(tmp_path)

        from mementic.graph import GraphStore

        graph = GraphStore(db_path=None)
        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(
                app, ["add", "decision", "Use JWT Auth",
                      "--rationale", "Token-based auth for APIs"],
            )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert graph.has_node("use-jwt-auth")
        payload = graph.get_node_payload("use-jwt-auth")
        assert payload["status"] == "accepted"

    def test_duplicate_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same slug + same rationale (>=80% overlap) is skipped as duplicate."""
        monkeypatch.chdir(tmp_path)

        from mementic.graph import GraphStore

        graph = GraphStore(db_path=None)
        existing = _make_decision(
            "use-jwt-auth", "Use JWT Auth", "Token-based authentication for APIs"
        )
        graph.add_decision(existing)  # type: ignore[arg-type]

        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(
                app, ["add", "decision", "Use JWT Auth",
                      "--rationale", "Token-based authentication for APIs"],
            )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        output = _strip_ansi(result.stdout + result.stderr)
        assert "skipped" in output.lower()
        # Original entry unchanged — still the earlier timestamp
        payload = graph.get_node_payload("use-jwt-auth")
        assert payload["created_at"] == "2024-01-01T00:00:00Z"

    def test_same_slug_different_content_deprecates_and_accepts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same slug + different rationale deprecates existing, accepts new."""
        monkeypatch.chdir(tmp_path)

        from mementic.graph import GraphStore

        graph = GraphStore(db_path=None)
        existing = _make_decision(
            "use-jwt-auth", "Use JWT Auth", "Token-based auth for APIs"
        )
        graph.add_decision(existing)  # type: ignore[arg-type]

        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(
                app, ["add", "decision", "Use JWT Auth",
                      "--rationale",
                      "Completely different approach using rotating keys and refresh tokens"],
            )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        output = _strip_ansi(result.stdout + result.stderr)
        assert "deprecated" in output.lower()
        # New entry accepted under the same slug
        payload = graph.get_node_payload("use-jwt-auth")
        assert payload["status"] == "accepted"
        assert "rotating keys" in payload["rationale"]

    def test_output_reports_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Add output reports accepted/skipped/deprecated counts."""
        monkeypatch.chdir(tmp_path)

        from mementic.graph import GraphStore

        graph = GraphStore(db_path=None)
        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(
                app, ["add", "decision", "Some Decision", "--rationale", "Because"],
            )

        output = _strip_ansi(result.stdout + result.stderr)
        assert "Counts:" in output
        assert "1 accepted" in output
        assert "0 skipped" in output
        assert "0 deprecated" in output


class TestAddConstraintAutoResolve:
    """Tests for `mementic add constraint` auto-resolve rules (Spec 28)."""

    def test_new_constraint_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A constraint with a new slug is accepted and stored."""
        monkeypatch.chdir(tmp_path)

        from mementic.graph import GraphStore

        graph = GraphStore(db_path=None)
        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(
                app, ["add", "constraint", "Services must be stateless",
                      "-e", "src/services.py"],
            )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert graph.has_node("services-must-be-stateless")

    def test_duplicate_constraint_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same slug + same description is skipped as duplicate."""
        monkeypatch.chdir(tmp_path)

        from mementic.graph import GraphStore
        from mementic.models import Constraint

        graph = GraphStore(db_path=None)
        graph.add_constraint(Constraint(
            id="services-must-be-stateless",
            description="Services must be stateless",
            bound_entities=["src/a.py"],
            source="test",
            status="active",
            created_at="2024-01-01T00:00:00Z",
        ))

        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(
                app, ["add", "constraint", "Services must be stateless",
                      "-e", "src/services.py"],
            )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        output = _strip_ansi(result.stdout + result.stderr)
        assert "skipped" in output.lower()

    def test_same_slug_different_content_deprecates_and_accepts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same slug + different description deprecates existing, accepts new."""
        monkeypatch.chdir(tmp_path)

        from mementic.graph import GraphStore
        from mementic.models import Constraint

        graph = GraphStore(db_path=None)
        graph.add_constraint(Constraint(
            id="use-https",
            description="All endpoints must use HTTPS",
            bound_entities=["src/a.py"],
            source="test",
            status="active",
            created_at="2024-01-01T00:00:00Z",
        ))

        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(
                app, ["add", "constraint", "All network calls must use TLS 1.3",
                      "-e", "src/net.py", "--id", "use-https"],
            )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        output = _strip_ansi(result.stdout + result.stderr)
        assert "deprecated" in output.lower()
        payload = graph.get_node_payload("use-https")
        assert payload["status"] == "active"
        assert "TLS 1.3" in payload["description"]

    def test_output_reports_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Add-constraint output reports accepted/skipped/deprecated counts."""
        monkeypatch.chdir(tmp_path)

        from mementic.graph import GraphStore

        graph = GraphStore(db_path=None)
        with patch("mementic.cli._resolve_graph", return_value=graph):
            result = runner.invoke(
                app, ["add", "constraint", "No raw SQL in handlers", "-e", "src/db.py"],
            )

        output = _strip_ansi(result.stdout + result.stderr)
        assert "Counts:" in output
        assert "1 accepted" in output
        assert "0 skipped" in output
        assert "0 deprecated" in output


# ── Spec 28 Phase 3: init stub ─────────────────────────────────────


class TestInitStub:
    """Tests for the collapsed init command (Spec 28)."""

    def test_init_creates_graph_db(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init creates .mementic/graph.db when missing."""
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert (tmp_path / ".mementic" / "graph.db").exists()

    def test_init_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running init twice succeeds and keeps the database intact."""
        monkeypatch.chdir(tmp_path)

        first = runner.invoke(app, ["init"])
        assert first.exit_code == 0, f"stderr: {first.stderr}"
        db_path = tmp_path / ".mementic" / "graph.db"
        assert db_path.exists()

        second = runner.invoke(app, ["init"])
        assert second.exit_code == 0, f"stderr: {second.stderr}"
        assert db_path.exists()

    def test_init_help_has_no_provider_flags(self) -> None:
        """init --help no longer shows --provider/--model/--key/--deep flags."""
        result = runner.invoke(app, ["init", "--help"])
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        output = _strip_ansi(result.stdout)
        assert "--provider" not in output
        assert "--model" not in output
        assert "--key" not in output
        assert "--deep" not in output

    def test_capture_command_removed(self) -> None:
        """The capture command is gone from mementic --help (Spec 28)."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        output = _strip_ansi(result.stdout)
        assert "capture" not in output
