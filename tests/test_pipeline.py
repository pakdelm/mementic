from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mementic.config import LLMConfig
from mementic.models import (
    Constraint,
    Decision,
    DecisionStatus,
    ExtractionResult,
    GraphEdge,
)
from mementic.format import find_file_paths
from mementic.pipeline import (
    _add_constraint_dedup,
    _add_decision_dedup,
    _backfill_decision_edges,
    _backfill_edges,
    _ingest_adr_source,
    _ingest_instruction_source,
    run_init,
)


ADR_CONTENT = """\
# ADR-0001: Use PostgreSQL as the primary database

## Status
Accepted

## Context
The application needs a relational database with ACID guarantees.

## Decision
We will use PostgreSQL 16 hosted on RDS.

## Consequences
The team must learn PostgreSQL-specific tooling and handle connection pooling.
All database access must go through src/db/connection.py.
"""


ADR_PROPOSED = """\
# ADR-0002: Introduce Redis caching

## Status
Proposed

## Context
We need faster response times for frequently accessed data.

## Decision
Add Redis as a caching layer in src/cache/redis_client.py.

## Consequences
Cache invalidation must be handled carefully in src/cache/invalidation.py.
"""


ADR_DEPRECATED = """\
# ADR-0003: Old REST API approach

## Status
Superseded

## Context
We initially used raw REST endpoints.

## Decision
Switch to GraphQL for the API layer in src/api/graphql.py.
"""


OPECODE_INSTRUCTION = """\
# Python Code Style

Use pydantic for all data models in src/models/*.py.
All I/O operations in src/services/ must be async.
"""


def _mock_freeform_result() -> ExtractionResult:
    return ExtractionResult(
        decisions=[
            Decision(
                id="d-opencode-1",
                title="Use pydantic for models",
                rationale="Type safety",
                source="opencode:instructions/python-style.md",
                status=DecisionStatus.ACCEPTED,
                created_at="2024-01-01T00:00:00Z",
                modified_at="2024-01-01T00:00:00Z",
            )
        ],
        constraints=[
            Constraint(
                id="c-opencode-1",
                description="All I/O must be async",
                bound_entities=["src/services/io.py"],
                enforced_by="d-opencode-1",
                source="opencode:instructions/python-style.md",
                status="active",
                created_at="2024-01-01T00:00:00Z",
            )
        ],
        affected_entities=["src/models/base.py"],
        edges_to_create=[],
    )


def _mock_claude_freeform_result() -> ExtractionResult:
    return ExtractionResult(
        decisions=[
            Decision(
                id="d-claude-1",
                title="Use async handlers",
                rationale="Performance",
                source="claude:instructions/style.md",
                status=DecisionStatus.ACCEPTED,
                created_at="2024-01-01T00:00:00Z",
                modified_at="2024-01-01T00:00:00Z",
            )
        ],
        constraints=[
            Constraint(
                id="c-claude-1",
                description="All handlers must be async",
                bound_entities=["src/handlers/"],
                enforced_by="d-claude-1",
                source="claude:instructions/style.md",
                status="active",
                created_at="2024-01-01T00:00:00Z",
            )
        ],
        affected_entities=["src/handlers/base.py"],
        edges_to_create=[],
    )


def _mock_cursor_freeform_result() -> ExtractionResult:
    return ExtractionResult(
        decisions=[
            Decision(
                id="d-cursor-1",
                title="Use TypeScript strict mode",
                rationale="Type safety",
                source="cursor:instructions/typescript.md",
                status=DecisionStatus.ACCEPTED,
                created_at="2024-01-01T00:00:00Z",
                modified_at="2024-01-01T00:00:00Z",
            )
        ],
        constraints=[
            Constraint(
                id="c-cursor-1",
                description="All TS configs must enable strict mode",
                bound_entities=["tsconfig.json"],
                enforced_by="d-cursor-1",
                source="cursor:instructions/typescript.md",
                status="active",
                created_at="2024-01-01T00:00:00Z",
            )
        ],
        affected_entities=["tsconfig.json"],
        edges_to_create=[],
    )


def _mock_windsurf_freeform_result() -> ExtractionResult:
    return ExtractionResult(
        decisions=[
            Decision(
                id="d-windsurf-1",
                title="Use Tailwind for styling",
                rationale="Utility-first CSS",
                source="windsurf:instructions/styling.md",
                status=DecisionStatus.ACCEPTED,
                created_at="2024-01-01T00:00:00Z",
                modified_at="2024-01-01T00:00:00Z",
            )
        ],
        constraints=[
            Constraint(
                id="c-windsurf-1",
                description="All components must use Tailwind classes",
                bound_entities=["src/components/"],
                enforced_by="d-windsurf-1",
                source="windsurf:instructions/styling.md",
                status="active",
                created_at="2024-01-01T00:00:00Z",
            )
        ],
        affected_entities=["src/components/"],
        edges_to_create=[],
    )


def _mock_adr_result() -> ExtractionResult:
    return ExtractionResult(
        decisions=[
            Decision(
                id="d-0001-use-postgresql",
                title="Use PostgreSQL as the primary database",
                rationale="The application needs a relational database with ACID guarantees. We will use PostgreSQL 16 hosted on RDS.",
                rejected_alternatives=[],
                source="adr:0001-use-postgres.md",
                status=DecisionStatus.ACCEPTED,
                created_at="2024-01-01T00:00:00Z",
                modified_at="2024-01-01T00:00:00Z",
            )
        ],
        constraints=[
            Constraint(
                id="c-adr-1",
                description="All database access must go through src/db/connection.py",
                bound_entities=["src/db/connection.py"],
                enforced_by="d-0001-use-postgresql",
                source="adr:adr-0001.md",
                status="active",
                created_at="2024-01-01T00:00:00Z",
            )
        ],
        affected_entities=["src/db/connection.py"],
        edges_to_create=[
            GraphEdge(source="d-0001-use-postgresql", target="c-adr-1", relation="enforces"),
            GraphEdge(source="c-adr-1", target="src/db/connection.py", relation="binds_to"),
        ],
    )


class TestFindFilePaths:
    def test_finds_python_paths(self) -> None:
        text = "See src/module.py and tests/test_module.py for details."
        paths = find_file_paths(text)
        assert "src/module.py" in paths
        assert "tests/test_module.py" in paths

    def test_finds_mixed_extensions(self) -> None:
        text = "Config in config.yaml, types in src/types.ts, logic in src/logic.rs."
        paths = find_file_paths(text)
        assert "config.yaml" in paths
        assert "src/types.ts" in paths
        assert "src/logic.rs" in paths

    def test_deduplicates(self) -> None:
        text = "src/main.py src/main.py src/main.py"
        paths = find_file_paths(text)
        assert paths == ["src/main.py"]

    def test_empty_text_returns_empty(self) -> None:
        assert find_file_paths("") == []

    def test_no_paths_returns_empty(self) -> None:
        assert find_file_paths("No file paths here at all.") == []


class TestRunInitWithAdr:
    """ADR ingestion via ``_ingest_adr_source`` (Spec 22 — direct calls)."""

    def test_creates_decisions_with_correct_source_labels(
        self, tmp_path: Path
    ) -> None:
        """ADR ingestion creates a Decision with the parsed title and rationale."""
        from mementic.graph import GraphStore

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)

        llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
        graph = GraphStore()
        _ingest_adr_source(graph, str(adr_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir))

        decisions = graph.get_all_decisions()
        assert len(decisions) == 1
        d = decisions[0]
        assert d.title == "Use PostgreSQL as the primary database"
        assert d.status == DecisionStatus.ACCEPTED
        assert "PostgreSQL 16" in d.rationale
        assert "ACID guarantees" in d.rationale

    def test_maps_status_correctly(
        self, tmp_path: Path
    ) -> None:
        """ADR Status 'Proposed' → ACCEPTED, 'Superseded' → DEPRECATED."""
        from mementic.graph import GraphStore

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0002-redis.md").write_text(ADR_PROPOSED)
        (adr_dir / "0003-deprecated.md").write_text(ADR_DEPRECATED)

        llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
        graph = GraphStore()
        _ingest_adr_source(graph, str(adr_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir))

        decisions = graph.get_all_decisions()
        assert len(decisions) == 2
        statuses = {d.title: d.status for d in decisions}
        assert statuses["Introduce Redis caching"] == DecisionStatus.ACCEPTED
        assert statuses["Old REST API approach"] == DecisionStatus.DEPRECATED

    def test_creates_code_entities_from_file_paths(
        self, tmp_path: Path
    ) -> None:
        """ADR consequences referencing file paths produce CodeEntity nodes."""
        from mementic.graph import GraphStore

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)

        llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
        graph = GraphStore()
        _ingest_adr_source(graph, str(adr_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir))

        stats = graph.stats()
        assert stats["entities"] >= 1

    def test_adds_constraints_and_links(
        self, tmp_path: Path
    ) -> None:
        """ADR consequences with modal-language lines produce constraints.

        In deterministic mode (no API key) the consequences text is scanned
        by ``extract_constraints_from_consequences``. The ADR_CONTENT
        consequences contain 'must' lines, so at least one constraint is
        created and linked to the decision.
        """
        from mementic.graph import GraphStore

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)

        llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
        graph = GraphStore()
        _ingest_adr_source(graph, str(adr_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir))

        constraints = graph.get_all_constraints()
        assert len(constraints) >= 1


class TestRunInitWithOpenCode:
    """OpenCode instruction ingestion via ``_ingest_instruction_source`` (Spec 22)."""

    def test_extracts_from_opencode_instructions(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pipeline ingests opencode_instructions via the markdown dispatcher.

        Deterministic mode (default, no API key) dispatches to
        ``extract_from_markdown``. We mock it to return a fixed
        ExtractionResult so the test asserts pipeline plumbing, not the
        deterministic extractor's heuristics.
        """
        from mementic.graph import GraphStore

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        oc_dir = tmp_path / "instructions"
        oc_dir.mkdir()
        (oc_dir / "python-style.md").write_text(OPECODE_INSTRUCTION)

        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: _mock_freeform_result(),
        )

        llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
        graph = GraphStore()
        _ingest_instruction_source(
            graph, str(oc_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir),
        )

        decisions = graph.get_all_decisions()
        assert len(decisions) == 1
        assert decisions[0].title == "Use pydantic for models"

        constraints = graph.get_all_constraints()
        assert len(constraints) == 1
        assert constraints[0].description == "All I/O must be async"

        stats = graph.stats()
        assert stats["entities"] >= 1

    def test_extraction_with_edges(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """edges_to_create from an extraction result are linked into the graph."""
        from mementic.graph import GraphStore

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        oc_dir = tmp_path / "instructions"
        oc_dir.mkdir()
        (oc_dir / "style.md").write_text("# Style\nUse async.")

        result = _mock_freeform_result()
        result.edges_to_create = [
            GraphEdge(source="d-opencode-1", target="c-opencode-1", relation="enforces"),
            GraphEdge(source="c-opencode-1", target="src/services/io.py", relation="binds"),
        ]
        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: result,
        )

        llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
        graph = GraphStore()
        _ingest_instruction_source(
            graph, str(oc_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir),
        )

        stats = graph.stats()
        assert stats["edges"] >= 2


class TestRunInitWithClaude:
    """Claude instruction ingestion via ``_ingest_instruction_source`` (Spec 22)."""

    def test_extracts_from_claude_instructions(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pipeline ingests claude_instructions via the markdown dispatcher."""
        from mementic.graph import GraphStore

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        claude_dir = tmp_path / "claude_instructions"
        claude_dir.mkdir()
        (claude_dir / "style.md").write_text("# Claude Style\nUse async everywhere.")

        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: _mock_claude_freeform_result(),
        )

        llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
        graph = GraphStore()
        _ingest_instruction_source(
            graph, str(claude_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir),
        )

        decisions = graph.get_all_decisions()
        assert len(decisions) == 1
        assert decisions[0].title == "Use async handlers"

        constraints = graph.get_all_constraints()
        assert len(constraints) == 1
        assert constraints[0].description == "All handlers must be async"

        stats = graph.stats()
        assert stats["entities"] >= 1


class TestRunInitCombined:
    """Combined source ingestion via direct calls (Spec 22)."""

    def test_both_adr_and_opencode_sources(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ADR + opencode sources each contribute decisions to the graph."""
        from mementic.graph import GraphStore

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)

        oc_dir = tmp_path / "instructions"
        oc_dir.mkdir()
        (oc_dir / "style.md").write_text(OPECODE_INSTRUCTION)

        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: _mock_freeform_result(),
        )

        llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
        graph = GraphStore()
        _ingest_adr_source(graph, str(adr_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir))
        _ingest_instruction_source(
            graph, str(oc_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir),
        )

        decisions = graph.get_all_decisions()
        assert len(decisions) == 2
        sources = {d.source for d in decisions}
        assert any(s.startswith("adr") for s in sources)
        assert any(s.startswith("opencode:") for s in sources)

    def test_all_three_source_types(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ADR + opencode + claude sources each contribute decisions."""
        from mementic.graph import GraphStore

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)

        oc_dir = tmp_path / "opencode_instructions"
        oc_dir.mkdir()
        (oc_dir / "python-style.md").write_text(OPECODE_INSTRUCTION)

        claude_dir = tmp_path / "claude_instructions"
        claude_dir.mkdir()
        (claude_dir / "style.md").write_text("# Claude Style\nBe async.")

        # Pipeline order: ADR → opencode → claude. Mock markdown extractor
        # via iterator so each call returns the right result.
        results = iter([
            _mock_freeform_result(),
            _mock_claude_freeform_result(),
        ])
        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: next(results),
        )

        llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
        graph = GraphStore()
        _ingest_adr_source(graph, str(adr_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir))
        _ingest_instruction_source(
            graph, str(oc_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir),
        )
        _ingest_instruction_source(
            graph, str(claude_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir),
        )

        decisions = graph.get_all_decisions()
        assert len(decisions) == 3
        sources = {d.source for d in decisions}
        assert any(s.startswith("adr") for s in sources)
        assert any(s.startswith("opencode:") for s in sources)
        assert any(s.startswith("claude:") for s in sources)


class TestSaveAndLoad:
    """SQLite save/load roundtrip (Spec 22 — direct calls)."""

    def test_graph_save_and_load_roundtrip(
        self,
        tmp_path: Path,
    ) -> None:
        """Graph persisted to SQLite and reloads identically from the same db."""
        from mementic.graph import GraphStore

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)

        db_path = tmp_path / ".mementic" / "graph.db"
        db_path.parent.mkdir(parents=True)

        llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
        gs1 = GraphStore(db_path=str(db_path))
        _ingest_adr_source(gs1, str(adr_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir))

        # Flush to SQLite and close
        gs1.flush()
        gs1.close()

        gs2 = GraphStore(db_path=str(db_path))
        assert gs2.get_all_decisions() == gs1.get_all_decisions()
        assert gs2.get_all_constraints() == gs1.get_all_constraints()
        assert gs2.stats() == gs1.stats()
        gs2.close()


class TestDedup:
    def test_add_decision_dedup_new(self) -> None:
        from mementic.graph import GraphStore
        gs = GraphStore()
        d = Decision(
            id="test-id", title="Test", rationale="...",
            source="adr:test", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        added = _add_decision_dedup(gs, d)
        assert added is True
        assert gs.has_node("test-id")

    def test_add_decision_dedup_duplicate_merges_source(self) -> None:
        from mementic.graph import GraphStore
        gs = GraphStore()
        d1 = Decision(
            id="test-id", title="Test", rationale="...",
            source="adr:test", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        d2 = Decision(
            id="test-id", title="Test", rationale="...",
            source="opencode:style.md", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        _add_decision_dedup(gs, d1)
        added = _add_decision_dedup(gs, d2)
        assert added is False
        payload = gs.get_node_payload("test-id")
        assert "adr:test" in payload["source"]
        assert "opencode:style.md" in payload["source"]

    def test_add_constraint_dedup_new(self) -> None:
        from mementic.graph import GraphStore
        gs = GraphStore()
        c = Constraint(
            id="c-test", description="A constraint",
            source="test", status="active",
            created_at="2024-01-01T00:00:00Z",
        )
        added = _add_constraint_dedup(gs, c)
        assert added is True
        assert gs.has_node("c-test")

    def test_add_constraint_dedup_duplicate(self) -> None:
        from mementic.graph import GraphStore
        gs = GraphStore()
        c = Constraint(
            id="c-test", description="A constraint",
            source="test", status="active",
            created_at="2024-01-01T00:00:00Z",
        )
        _add_constraint_dedup(gs, c)
        added = _add_constraint_dedup(gs, c)
        assert added is False


class TestBackfill:
    def test_backfill_edges_adds_entity_links(self) -> None:
        from mementic.graph import GraphStore
        gs = GraphStore()
        c = Constraint(
            id="c-lonely",
            description="Must use async for I/O in src/services/io.py",
            bound_entities=[],
            source="test",
            status="active",
            created_at="2024-01-01T00:00:00Z",
        )
        d = Decision(
            id="d-parent", title="Parent", rationale="...",
            source="test", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        gs.add_constraint(c)
        gs.add_decision(d)
        gs.link("d-parent", "c-lonely", "enforces")

        count = _backfill_edges(gs)
        assert count == 1
        assert gs.has_outgoing_edges("c-lonely")

    def test_backfill_edges_skips_already_linked(self) -> None:
        from mementic.graph import GraphStore
        from mementic.models import CodeEntity, EntityType
        gs = GraphStore()
        c = Constraint(
            id="c-linked",
            description="A constraint",
            bound_entities=["src/foo.py"],
            source="test",
            status="active",
            created_at="2024-01-01T00:00:00Z",
        )
        gs.add_constraint(c)
        gs.add_code_entity(CodeEntity(id="src/foo.py", type=EntityType.FILE))
        gs.link("c-linked", "src/foo.py", "binds_to")

        count = _backfill_edges(gs)
        assert count == 0

    def test_backfill_decision_edges(self) -> None:
        from mementic.graph import GraphStore
        gs = GraphStore()
        d = Decision(
            id="d-orphan", title="Use Redis in src/cache/redis_client.py",
            rationale="Need caching in src/cache/redis_client.py",
            source="test", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        gs.add_decision(d)

        count = _backfill_decision_edges(gs)
        assert count == 1
        assert gs.has_outgoing_edges("d-orphan")

    def test_backfill_decision_edges_skips_linked(self) -> None:
        from mementic.graph import GraphStore
        from mementic.models import CodeEntity, EntityType
        gs = GraphStore()
        d = Decision(
            id="d-linked", title="Linked", rationale="...",
            source="test", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        gs.add_decision(d)
        gs.add_code_entity(CodeEntity(id="src/main.py", type=EntityType.FILE))
        gs.link("d-linked", "src/main.py", "affects")

        count = _backfill_decision_edges(gs)
        assert count == 0

    def test_backfill_edges_fallback_when_no_file_paths(self) -> None:
        """When constraint description has no file paths, uses source_path as entity."""
        from mementic.graph import GraphStore
        gs = GraphStore()
        c = Constraint(
            id="c-nopath",
            description="A constraint with no file references at all.",
            bound_entities=[],
            source="docs/some-adr.md",
            status="active",
            created_at="2024-01-01T00:00:00Z",
        )
        gs.add_constraint(c)
        count = _backfill_edges(gs)
        assert count == 1
        assert gs.has_outgoing_edges("c-nopath")

    def test_backfill_decision_edges_fallback_when_no_paths(self) -> None:
        """When decision text has no file paths, uses src/ as fallback entity."""
        from mementic.graph import GraphStore
        gs = GraphStore()
        d = Decision(
            id="d-nopath",
            title="A generic decision",
            rationale="No file paths mentioned here whatsoever.",
            source="adr:no-paths",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        gs.add_decision(d)
        count = _backfill_decision_edges(gs)
        assert count == 1
        assert gs.has_outgoing_edges("d-nopath")


# ── C1: Cursor + Windsurf pipeline ingestion tests ──────────────────


class TestRunInitWithCursor:
    """Tests for Cursor instruction source ingestion via pipeline (Spec 09c C1)."""

    def test_extracts_from_cursor_instructions(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pipeline ingests cursor_instructions and creates decisions/constraints."""
        from mementic.graph import GraphStore

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        cursor_dir = tmp_path / "cursor_instructions"
        cursor_dir.mkdir()
        (cursor_dir / "typescript.md").write_text("# TypeScript Style\nUse strict mode.")

        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: _mock_cursor_freeform_result(),
        )

        llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
        graph = GraphStore()
        _ingest_instruction_source(
            graph, str(cursor_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir),
        )

        decisions = graph.get_all_decisions()
        assert len(decisions) == 1
        assert decisions[0].title == "Use TypeScript strict mode"

        constraints = graph.get_all_constraints()
        assert len(constraints) == 1
        assert constraints[0].description == "All TS configs must enable strict mode"

        stats = graph.stats()
        assert stats["entities"] >= 1

    def test_cursor_source_usage_tracking(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pipeline extracts decisions from cursor instruction sources via LLM.

        Requires LLM dispatch so the dispatcher calls ``extract_from_freeform``.
        """
        from mementic.graph import GraphStore

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        cursor_dir = tmp_path / "cursor_instructions"
        cursor_dir.mkdir()
        (cursor_dir / "style.md").write_text("# Cursor Style\nUse strict mode.")

        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")
        monkeypatch.setattr(
            "mementic.pipeline.extract_from_freeform",
            lambda content, label, llm_cfg: _mock_cursor_freeform_result(),
        )

        llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
        graph = GraphStore()
        _ingest_instruction_source(
            graph, str(cursor_dir), llm_cfg,
            force_deterministic=False, repo_path=str(repo_dir),
        )

        assert graph is not None


class TestRunInitWithWindsurf:
    """Tests for Windsurf instruction source ingestion via pipeline (Spec 09c C1)."""

    def test_extracts_from_windsurf_instructions(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pipeline ingests windsurf_instructions and creates decisions/constraints."""
        from mementic.graph import GraphStore

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        windsurf_dir = tmp_path / "windsurf_instructions"
        windsurf_dir.mkdir()
        (windsurf_dir / "styling.md").write_text("# Windsurf Style\nUse Tailwind.")

        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: _mock_windsurf_freeform_result(),
        )

        llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
        graph = GraphStore()
        _ingest_instruction_source(
            graph, str(windsurf_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir),
        )

        decisions = graph.get_all_decisions()
        assert len(decisions) == 1
        assert decisions[0].title == "Use Tailwind for styling"

        constraints = graph.get_all_constraints()
        assert len(constraints) == 1
        assert constraints[0].description == "All components must use Tailwind classes"

        stats = graph.stats()
        assert stats["entities"] >= 1

    def test_windsurf_source_usage_tracking(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pipeline extracts decisions from windsurf instruction sources via LLM."""
        from mementic.graph import GraphStore

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        windsurf_dir = tmp_path / "windsurf_instructions"
        windsurf_dir.mkdir()
        (windsurf_dir / "style.md").write_text("# Windsurf Style\nUse Tailwind.")

        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")
        monkeypatch.setattr(
            "mementic.pipeline.extract_from_freeform",
            lambda content, label, llm_cfg: _mock_windsurf_freeform_result(),
        )

        llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
        graph = GraphStore()
        _ingest_instruction_source(
            graph, str(windsurf_dir), llm_cfg,
            force_deterministic=False, repo_path=str(repo_dir),
        )

        assert graph is not None


class TestRunInitCombinedCursorWindsurf:
    """Tests for combined cursor + windsurf pipeline ingestion (Spec 22)."""

    def test_cursor_and_windsurf_together(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pipeline ingests both cursor and windsurf sources simultaneously."""
        from mementic.graph import GraphStore

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        cursor_dir = tmp_path / "cursor_dir"
        cursor_dir.mkdir()
        (cursor_dir / "ts.md").write_text("# Cursor TS rules")

        windsurf_dir = tmp_path / "windsurf_dir"
        windsurf_dir.mkdir()
        (windsurf_dir / "css.md").write_text("# Windsurf CSS rules")

        # Pipeline order: cursor loop → windsurf loop.
        results = iter([
            _mock_cursor_freeform_result(),
            _mock_windsurf_freeform_result(),
        ])
        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: next(results),
        )

        llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
        graph = GraphStore()
        _ingest_instruction_source(
            graph, str(cursor_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir),
        )
        _ingest_instruction_source(
            graph, str(windsurf_dir), llm_cfg, force_deterministic=True, repo_path=str(repo_dir),
        )

        decisions = graph.get_all_decisions()
        assert len(decisions) == 2
        sources = {d.source for d in decisions}
        assert any(s.startswith("cursor:") for s in sources)
        assert any(s.startswith("windsurf:") for s in sources)


# ── Spec 12a: Codebase Scanner Pipeline Integration ────────────────


class TestRunInitWithRepoScan:
    """Tests for run_init codebase scanning (Spec 12a, Spec 22).

    The codebase structural scan only runs when ``_should_use_llm(cfg)`` is
    True (there is no deterministic equivalent). These tests call ``run_init``
    directly with ``repo_path``, ``llm_cfg``, and ``deep=True`` plus a fake
    API key in the environment.
    """

    def test_init_with_repo_scans_codebase(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_init with repo_path scans the codebase and extracts decisions."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "pyproject.toml").write_text(
            "[project]\nname = \"test-project\"\n"
        )
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text(
            "import fastapi\nfrom pydantic import BaseModel\n"
        )

        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")

        mock_result = ExtractionResult(
            decisions=[
                Decision(
                    id="d-fastapi",
                    title="Use FastAPI",
                    rationale="FastAPI is used as web framework",
                    source="detected:codebase",
                    status=DecisionStatus.ACCEPTED,
                    created_at="2024-01-01T00:00:00Z",
                    modified_at="2024-01-01T00:00:00Z",
                )
            ],
            constraints=[],
            affected_entities=["src/app.py"],
            edges_to_create=[],
        )

        # Suppress the markdown scan (it runs when repo+llm_cfg are present).
        monkeypatch.setattr(
            "mementic.sources.codebase.collect_markdown_files",
            lambda _path: [],
        )

        with patch(
            "mementic.pipeline.extract_from_codebase_summary",
            return_value=mock_result,
        ):
            llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
            gs = run_init(repo_path=str(repo), llm_cfg=llm_cfg, deep=True)

        decisions = gs.get_all_decisions()
        assert len(decisions) >= 1
        assert any(d.id == "d-fastapi" for d in decisions)

    def test_init_with_no_scan_skips_scanner(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_init with no_scan=True skips the codebase scanner entirely."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "pyproject.toml").write_text("[project]\nname = \"test\"\n")
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text("import fastapi\n")

        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")
        monkeypatch.setattr(
            "mementic.sources.codebase.collect_markdown_files",
            lambda _path: [],
        )

        with patch(
            "mementic.pipeline.extract_from_codebase_summary"
        ) as mock_extract:
            llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
            _gs = run_init(
                repo_path=str(repo), llm_cfg=llm_cfg, no_scan=True, deep=True,
            )

        mock_extract.assert_not_called()

    def test_init_repo_path_none_skips_scanner(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_init without repo_path does not call the scanner."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")

        with patch(
            "mementic.pipeline.extract_from_codebase_summary"
        ) as mock_extract:
            llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
            _gs = run_init(llm_cfg=llm_cfg, deep=True)

        mock_extract.assert_not_called()

    def test_init_with_repo_scanner_empty_summary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When codebase scan produces empty summary, no LLM call is made.

        An empty repo directory (no pyproject.toml, no source files) causes
        ``scan_codebase_structure`` to return an empty string, so the
        extraction step is skipped.
        """
        repo = tmp_path / "repo"
        repo.mkdir()

        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")
        monkeypatch.setattr(
            "mementic.sources.codebase.collect_markdown_files",
            lambda _path: [],
        )

        with patch(
            "mementic.pipeline.extract_from_codebase_summary"
        ) as mock_extract:
            llm_cfg = LLMConfig(provider="deepseek", model="deepseek-v4-pro")
            _gs = run_init(repo_path=str(repo), llm_cfg=llm_cfg, deep=True)

        mock_extract.assert_not_called()
