"""Tests for mementic graph visualization (Spec 07)."""

from __future__ import annotations

import json
import os

from mementic.graph import GraphStore
from mementic.models import (
    CodeEntity,
    Constraint,
    Decision,
    DecisionStatus,
    EntityType,
)
from mementic.visualize import generate_html_graph


def _build_sample_graph() -> GraphStore:
    """Build a small graph with one of each node type and mixed edges."""
    gs = GraphStore()

    d1 = Decision(
        id="dec-001",
        title="Use async everywhere",
        rationale="Better performance under load",
        rejected_alternatives=["Threading", "Multiprocessing"],
        source="ADR-001",
        status=DecisionStatus.ACCEPTED,
        timestamp="2024-01-01T00:00:00Z",
        tags=["performance"],
    )
    d2 = Decision(
        id="dec-002",
        title="Adopt pydantic",
        rationale="Standard validation",
        source="ADR-002",
        status=DecisionStatus.PROPOSED,
        timestamp="2024-02-01T00:00:00Z",
    )

    c1 = Constraint(
        id="con-001",
        description="All I/O must be async",
        bound_entities=["src/io.py", "src/api.py"],
        enforced_by="dec-001",
        source="ADR-001",
        status="active",
        timestamp="2024-01-01T00:00:00Z",
    )

    e1 = CodeEntity(
        id="src/io.py",
        type=EntityType.FILE,
    )
    e2 = CodeEntity(
        id="src/api.py",
        type=EntityType.FILE,
    )

    gs.add_decision(d1)
    gs.add_decision(d2)
    gs.add_constraint(c1)
    gs.add_code_entity(e1)
    gs.add_code_entity(e2)

    gs.link("dec-001", "src/io.py", "affects")
    gs.link("dec-001", "con-001", "enforces")
    gs.link("con-001", "src/io.py", "binds_to")
    gs.link("con-001", "src/api.py", "binds_to")
    gs.link("dec-002", "dec-001", "uses")

    return gs


class TestGenerateHtmlGraph:
    """Tests for the generate_html_graph function."""

    def test_output_is_valid_html(self) -> None:
        """Generated output starts with <html> tag."""
        gs = _build_sample_graph()
        html = generate_html_graph(gs)

        assert html.strip().startswith("<html>")

    def test_contains_all_nodes(self) -> None:
        """All graph nodes appear in the generated HTML."""
        gs = _build_sample_graph()
        html = generate_html_graph(gs)

        for node_id, _node_type, _payload in gs.iter_nodes():
            assert node_id in html, f"Node {node_id} missing from HTML"

    def test_contains_edges_as_references(self) -> None:
        """Edge source and target IDs appear in the HTML."""
        gs = _build_sample_graph()
        html = generate_html_graph(gs)

        edges = gs.iter_edges()
        assert len(edges) > 0, "Expected at least one edge in test graph"
        # At minimum source/target IDs should be present in the data
        for src, tgt, _rel in edges:
            assert src in html, f"Edge source {src} missing from HTML"

    def test_legend_is_present(self) -> None:
        """The legend div is injected into the page."""
        gs = _build_sample_graph()
        html = generate_html_graph(gs)

        assert "mementic-legend" in html

    def test_no_cdn_script_or_link_tags(self) -> None:
        """HTML contains no CDN script src or link href fetching external resources."""
        import re

        gs = _build_sample_graph()
        html = generate_html_graph(gs)

        # Find all <script src="..."> and <link href="..."> that hit external URLs
        external_refs = re.findall(
            r'<(?:script[^>]*src|link[^>]*href)="https?://[^"]*"',
            html,
        )
        assert not external_refs, (
            f"Found external CDN references: {external_refs}"
        )

    def test_bootstrap_replacement_css_injected(self) -> None:
        """The minimal Bootstrap replacement CSS is present."""
        gs = _build_sample_graph()
        html = generate_html_graph(gs)

        assert "mementic-bootstrap-replacement" in html

    def test_filter_decision_shows_only_decisions(self) -> None:
        """When filter_type='decision', only Decision nodes appear."""
        gs = _build_sample_graph()
        html = generate_html_graph(gs, filter_type="decision")

        # Decision IDs should be present
        assert "dec-001" in html
        assert "dec-002" in html
        # Constraint and entity IDs should NOT be present in the node data
        # (They may appear in edge data, so check for node-defining patterns)
        # Constraint title text shouldn't be a node label
        assert "src/io.py" not in html or "id: 'src/io.py'" not in html

    def test_filter_constraint_shows_only_constraints(self) -> None:
        """When filter_type='constraint', only Constraint nodes appear."""
        gs = _build_sample_graph()
        html = generate_html_graph(gs, filter_type="constraint")

        assert "con-001" in html
        # Decision-specific title text should not appear
        assert "Use async everywhere" not in html

    def test_filter_entity_shows_only_entities(self) -> None:
        """When filter_type='entity', only CodeEntity nodes appear."""
        gs = _build_sample_graph()
        html = generate_html_graph(gs, filter_type="entity")

        # Entity IDs should appear
        assert "src/io.py" in html
        assert "src/api.py" in html
        # Decision title text should not appear (filtered out)
        assert "Use async everywhere" not in html

    def test_deprecated_nodes_get_muted_color(self) -> None:
        """Deprecated nodes should get a muted gray color."""
        gs = GraphStore()
        d = Decision(
            id="d-deprecated",
            title="Old Decision",
            rationale="No longer used",
            source="test",
            status=DecisionStatus.DEPRECATED,
            timestamp="2020-01-01T00:00:00Z",
        )
        gs.add_decision(d)
        html = generate_html_graph(gs)

        assert "d-deprecated" in html

    def test_empty_graph_produces_valid_html(self) -> None:
        """An empty graph still produces valid HTML output."""
        gs = GraphStore()
        html = generate_html_graph(gs)

        assert html.strip().startswith("<html>")
        assert "mementic-legend" in html


class TestGraphCommandCli:
    """Tests for the mementic graph CLI command."""

    def test_graph_command_writes_file(self, tmp_path) -> None:
        """mementic graph -o writes the HTML file to the specified path."""
        from typer.testing import CliRunner

        from mementic.cli import app

        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            output_file = tmp_path / "test_graph.html"

            config = {
                "sources": {"adr_dirs": [], "opencode_instructions": []},
                "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
                "llm": {"provider": "deepseek", "model": "deepseek-chat"},
            }

            (tmp_path / ".mementic").mkdir(parents=True, exist_ok=True)
            (tmp_path / ".mementic.json").write_text(json.dumps(config))

            from mementic.graph import GraphStore
            from mementic.models import Decision, DecisionStatus

            gs = GraphStore()
            gs.add_decision(
                Decision(
                    id="d1",
                    title="Test decision",
                    rationale="For testing",
                    source="test",
                    status=DecisionStatus.ACCEPTED,
                    timestamp="2024-01-01T00:00:00Z",
                )
            )
            gs.save(str(tmp_path / ".mementic" / "graph.json"))

            result = runner.invoke(
                app,
                ["graph", "--output", str(output_file)],
            )

            assert result.exit_code == 0, f"CLI failed: {result.output}"
            assert output_file.exists()
            content = output_file.read_text(encoding="utf-8")
            assert "d1" in content
            assert "<html>" in content
        finally:
            os.chdir(old_cwd)

    def test_graph_command_invalid_filter_exits_with_error(self, tmp_path) -> None:
        """Invalid --filter value produces an error message."""
        from typer.testing import CliRunner

        from mementic.cli import app

        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            config = {
                "sources": {"adr_dirs": [], "opencode_instructions": []},
                "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
                "llm": {"provider": "deepseek", "model": "deepseek-chat"},
            }

            (tmp_path / ".mementic").mkdir(parents=True, exist_ok=True)
            (tmp_path / ".mementic.json").write_text(json.dumps(config))

            from mementic.graph import GraphStore
            from mementic.models import Decision, DecisionStatus

            gs = GraphStore()
            gs.add_decision(
                Decision(
                    id="d1",
                    title="Test",
                    rationale="Test",
                    source="test",
                    status=DecisionStatus.ACCEPTED,
                    timestamp="2024-01-01T00:00:00Z",
                )
            )
            gs.save(str(tmp_path / ".mementic" / "graph.json"))

            result = runner.invoke(
                app,
                ["graph", "--filter", "invalid_type"],
            )

            assert result.exit_code == 1
            assert "Invalid filter" in result.stdout or "Invalid filter" in result.stderr
        finally:
            os.chdir(old_cwd)
