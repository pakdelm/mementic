"""Tests for mementic graph linting (Spec 06c F7)."""

from __future__ import annotations

from pathlib import Path

from mementic.graph import GraphStore
from mementic.lint import LintFinding, LintResult, lint, lint_fix
from mementic.models import CodeEntity, Constraint, Decision, DecisionStatus, EntityType


def make_decision(
    id: str = "d-001",
    title: str = "Use async",
    rationale: str = "Better performance",
    status: DecisionStatus = DecisionStatus.ACCEPTED,
    tags: list[str] | None = None,
) -> Decision:
    return Decision(
        id=id,
        title=title,
        rationale=rationale,
        source="test",
        status=status,
        timestamp="2024-01-01T00:00:00Z",
        tags=tags or [],
    )


def make_constraint(
    id: str = "c-001",
    description: str = "Must use async I/O",
    enforced_by: str = "d-001",
    status: str = "active",
) -> Constraint:
    return Constraint(
        id=id,
        description=description,
        bound_entities=[],
        enforced_by=enforced_by,
        source="test",
        status=status,
        timestamp="2024-01-01T00:00:00Z",
    )


class TestLintResult:
    def test_counts_empty(self) -> None:
        result = LintResult()
        assert result.error_count == 0
        assert result.warning_count == 0
        assert result.info_count == 0

    def test_counts_mixed(self) -> None:
        result = LintResult(findings=[
            LintFinding(rule="r1", severity="error", message="e1"),
            LintFinding(rule="r2", severity="error", message="e2"),
            LintFinding(rule="r3", severity="warning", message="w1"),
            LintFinding(rule="r4", severity="info", message="i1"),
        ])
        assert result.error_count == 2
        assert result.warning_count == 1
        assert result.info_count == 1


class TestLintCleanGraph:
    def test_empty_graph_is_clean(self) -> None:
        graph = GraphStore()
        result = lint(graph)
        assert len(result.findings) == 0

    def test_healthy_graph_no_issues(self) -> None:
        graph = GraphStore()
        d = make_decision(tags=["performance"])
        c = make_constraint(enforced_by="d-001")
        e = CodeEntity(id="src/test.py", type=EntityType.FILE)
        graph.add_decision(d)
        graph.add_constraint(c)
        graph.add_code_entity(e)
        graph.link("d-001", "c-001", "enforces")
        graph.link("c-001", "src/test.py", "binds_to")
        graph.link("d-001", "src/test.py", "affects")

        result = lint(graph)
        errors = [f for f in result.findings if f.severity == "error"]
        assert len(errors) == 0


class TestLintOrphanedConstraint:
    def test_constraint_with_missing_enforcing_decision(self) -> None:
        graph = GraphStore()
        c = make_constraint(
            id="c-orphan",
            enforced_by="nonexistent-decision",
        )
        graph.add_constraint(c)

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "orphaned-constraint"]
        assert len(findings) == 1
        assert findings[0].node_id == "c-orphan"
        assert "nonexistent-decision" in findings[0].message

    def test_constraint_with_valid_enforcing_decision(self) -> None:
        graph = GraphStore()
        d = make_decision(id="d-valid")
        c = make_constraint(id="c-valid", enforced_by="d-valid")
        graph.add_decision(d)
        graph.add_constraint(c)

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "orphaned-constraint"]
        assert len(findings) == 0

    def test_constraint_with_empty_enforced_by(self) -> None:
        """Constraint with empty enforced_by is not flagged as orphaned."""
        graph = GraphStore()
        c = make_constraint(id="c-no-enforcer", enforced_by="")
        graph.add_constraint(c)

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "orphaned-constraint"]
        assert len(findings) == 0


class TestLintUnlinkedDecision:
    def test_decision_with_no_outgoing_edges(self) -> None:
        graph = GraphStore()
        d = make_decision(tags=["test"])
        graph.add_decision(d)

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "unlinked-decision"]
        assert len(findings) == 1
        assert findings[0].node_id == "d-001"

    def test_decision_with_outgoing_edges(self) -> None:
        graph = GraphStore()
        d = make_decision(tags=["test"])
        graph.add_decision(d)
        graph.add_code_entity(CodeEntity(id="src/test.py", type=EntityType.FILE))
        graph.link("d-001", "src/test.py", "affects")

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "unlinked-decision"]
        assert len(findings) == 0


class TestLintStaleEntity:
    def test_entity_referencing_nonexistent_file(self, tmp_path: Path) -> None:
        graph = GraphStore()
        # Use a path that definitely doesn't exist
        nonexistent = str(tmp_path / "nonexistent_file.py")
        e = CodeEntity(id=nonexistent, type=EntityType.FILE)
        graph.add_code_entity(e)

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "stale-entity"]
        assert len(findings) == 1
        assert findings[0].node_id == nonexistent

    def test_entity_referencing_existing_file(self, tmp_path: Path) -> None:
        graph = GraphStore()
        existing = tmp_path / "real_file.py"
        existing.write_text("# test")
        e = CodeEntity(id=str(existing), type=EntityType.FILE)
        graph.add_code_entity(e)

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "stale-entity"]
        assert len(findings) == 0

    def test_non_file_entity_not_checked(self) -> None:
        graph = GraphStore()
        e = CodeEntity(id="src.models.BaseModel", type=EntityType.CLASS)
        graph.add_code_entity(e)

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "stale-entity"]
        assert len(findings) == 0


class TestLintDuplicateSlug:
    def test_no_duplicate_ids(self) -> None:
        graph = GraphStore()
        graph.add_decision(make_decision(id="d1", title="One"))
        graph.add_decision(make_decision(id="d2", title="Two"))

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "duplicate-slug"]
        assert len(findings) == 0


class TestLintStatusInconsistency:
    def test_deprecated_decision_with_active_constraint(self) -> None:
        graph = GraphStore()
        d = make_decision(id="d-dep", status=DecisionStatus.DEPRECATED)
        c = make_constraint(id="c-active", enforced_by="d-dep", status="active")
        graph.add_decision(d)
        graph.add_constraint(c)

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "status-inconsistency"]
        assert len(findings) == 1
        assert findings[0].node_id == "d-dep"
        assert "c-active" in findings[0].message

    def test_deprecated_decision_with_deprecated_constraint_no_flag(self) -> None:
        graph = GraphStore()
        d = make_decision(id="d-dep", status=DecisionStatus.DEPRECATED)
        c = make_constraint(id="c-dep", enforced_by="d-dep", status="deprecated")
        graph.add_decision(d)
        graph.add_constraint(c)

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "status-inconsistency"]
        assert len(findings) == 0

    def test_accepted_decision_no_inconsistency(self) -> None:
        graph = GraphStore()
        d = make_decision(id="d-ok", status=DecisionStatus.ACCEPTED)
        c = make_constraint(id="c-active", enforced_by="d-ok", status="active")
        graph.add_decision(d)
        graph.add_constraint(c)

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "status-inconsistency"]
        assert len(findings) == 0


class TestLintOrphanedEntity:
    def test_entity_with_no_incoming_edges(self) -> None:
        graph = GraphStore()
        e = CodeEntity(id="src/lonely.py", type=EntityType.FILE)
        graph.add_code_entity(e)

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "orphaned-entity"]
        assert len(findings) == 1
        assert findings[0].node_id == "src/lonely.py"

    def test_entity_with_incoming_edges(self) -> None:
        graph = GraphStore()
        d = make_decision()
        e = CodeEntity(id="src/connected.py", type=EntityType.FILE)
        graph.add_decision(d)
        graph.add_code_entity(e)
        graph.link("d-001", "src/connected.py", "affects")

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "orphaned-entity"]
        assert len(findings) == 0


class TestLintUntaggedDecision:
    def test_decision_with_no_tags(self) -> None:
        graph = GraphStore()
        d = make_decision(tags=[])
        graph.add_decision(d)

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "untagged-decision"]
        assert len(findings) == 1
        assert findings[0].node_id == "d-001"

    def test_decision_with_tags(self) -> None:
        graph = GraphStore()
        d = make_decision(tags=["performance", "architecture"])
        graph.add_decision(d)

        result = lint(graph)
        findings = [f for f in result.findings if f.rule == "untagged-decision"]
        assert len(findings) == 0


class TestLintFix:
    def test_fix_stale_entity_removes_node(self) -> None:
        graph = GraphStore()
        e = CodeEntity(id="/does/not/exist.py", type=EntityType.FILE)
        graph.add_code_entity(e)

        result = lint(graph)
        # The stale entity is also orphaned (no incoming edges), so we get two findings
        assert len(result.findings) >= 1

        resolved = lint_fix(graph, result)
        assert resolved >= 1
        assert not graph.has_node("/does/not/exist.py")

    def test_fix_status_inconsistency_cascades_deprecation(self) -> None:
        graph = GraphStore()
        d = make_decision(id="d-dep", status=DecisionStatus.DEPRECATED)
        c = make_constraint(id="c-active", enforced_by="d-dep", status="active")
        graph.add_decision(d)
        graph.add_constraint(c)

        result = lint(graph)
        inconsistencies = [f for f in result.findings if f.rule == "status-inconsistency"]
        assert len(inconsistencies) == 1

        resolved = lint_fix(graph, result)
        assert resolved >= 1

        # Constraint should now be deprecated
        payload = graph.get_node_payload("c-active")
        assert payload["status"] == "deprecated"

    def test_fix_orphaned_entity_removes_node(self) -> None:
        graph = GraphStore()
        e = CodeEntity(id="src/orphan.py", type=EntityType.FILE)
        graph.add_code_entity(e)

        result = lint(graph)
        resolved = lint_fix(graph, result)
        assert resolved >= 1
        assert not graph.has_node("src/orphan.py")

    def test_fix_combined_resolves_multiple(self) -> None:
        graph = GraphStore()
        # Stale entity
        graph.add_code_entity(CodeEntity(id="/does/not/exist.py", type=EntityType.FILE))
        # Orphaned entity
        graph.add_code_entity(CodeEntity(id="src/orphan.py", type=EntityType.FILE))

        result = lint(graph)
        stale_count = len([f for f in result.findings if f.rule == "stale-entity"])
        orphan_count = len([f for f in result.findings if f.rule == "orphaned-entity"])
        assert stale_count + orphan_count >= 2

        resolved = lint_fix(graph, result)
        assert resolved >= 2
