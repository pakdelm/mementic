from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mementic.models import Constraint, Decision, DecisionStatus, ExtractionResult, GraphEdge
from mementic.pipeline import find_file_paths, run_init


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
                timestamp="2024-01-01T00:00:00Z",
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
                timestamp="2024-01-01T00:00:00Z",
            )
        ],
        affected_entities=["src/models/base.py"],
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
                author="unknown",
                timestamp="2024-01-01T00:00:00Z",
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
                timestamp="2024-01-01T00:00:00Z",
            )
        ],
        affected_entities=["src/db/connection.py"],
        edges_to_create=[
            GraphEdge(source="d-0001-use-postgresql", target="c-adr-1", relation="enforces"),
            GraphEdge(source="c-adr-1", target="src/db/connection.py", relation="binds_to"),
        ],
    )


def _mock_adr_proposed_result() -> ExtractionResult:
    return ExtractionResult(
        decisions=[
            Decision(
                id="d-0002-redis",
                title="Introduce Redis caching",
                rationale="We need faster response times for frequently accessed data. Add Redis as a caching layer in src/cache/redis_client.py.",
                source="adr:0002-redis.md",
                status=DecisionStatus.PROPOSED,
                timestamp="2024-01-01T00:00:00Z",
            )
        ],
        constraints=[],
        affected_entities=["src/cache/redis_client.py"],
        edges_to_create=[],
    )


def _mock_adr_deprecated_result() -> ExtractionResult:
    return ExtractionResult(
        decisions=[
            Decision(
                id="d-0003-deprecated",
                title="Old REST API approach",
                rationale="We initially used raw REST endpoints. Switch to GraphQL for the API layer in src/api/graphql.py.",
                source="adr:0003-deprecated.md",
                status=DecisionStatus.SUPERSEDED,
                timestamp="2024-01-01T00:00:00Z",
            )
        ],
        constraints=[],
        affected_entities=["src/api/graphql.py"],
        edges_to_create=[],
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
    def test_creates_decisions_with_correct_source_labels(
        self, tmp_path: Path
    ) -> None:
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)

        config_path = tmp_path / ".mementic.json"
        config_data = {
            "sources": {"adr_dirs": [{"path": str(adr_dir)}]},
            "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
        }
        config_path.write_text(json.dumps(config_data))

        with patch(
            "mementic.pipeline.extract_from_adr_consequences",
            return_value=[],
        ):
            gs = run_init(str(config_path))

        decisions = gs.get_all_decisions()
        assert len(decisions) == 1
        d = decisions[0]
        assert d.title == "Use PostgreSQL as the primary database"
        assert d.status == DecisionStatus.ACCEPTED
        assert "PostgreSQL 16" in d.rationale
        assert "ACID guarantees" in d.rationale

    def test_maps_status_correctly(self, tmp_path: Path) -> None:
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0002-redis.md").write_text(ADR_PROPOSED)
        (adr_dir / "0003-deprecated.md").write_text(ADR_DEPRECATED)

        config_path = tmp_path / ".mementic.json"
        config_data = {
            "sources": {"adr_dirs": [{"path": str(adr_dir)}]},
            "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
        }
        config_path.write_text(json.dumps(config_data))

        with patch(
            "mementic.pipeline.extract_from_adr_consequences",
            return_value=[],
        ):
            gs = run_init(str(config_path))

        decisions = gs.get_all_decisions()
        assert len(decisions) == 2
        statuses = {d.title: d.status for d in decisions}
        assert statuses["Introduce Redis caching"] == DecisionStatus.PROPOSED
        assert statuses["Old REST API approach"] == DecisionStatus.SUPERSEDED

    def test_creates_code_entities_from_file_paths(self, tmp_path: Path) -> None:
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)

        config_path = tmp_path / ".mementic.json"
        config_data = {
            "sources": {"adr_dirs": [{"path": str(adr_dir)}]},
            "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
        }
        config_path.write_text(json.dumps(config_data))

        with patch(
            "mementic.pipeline.extract_from_adr_consequences",
            return_value=[],
        ):
            gs = run_init(str(config_path))

        stats = gs.stats()
        assert stats["entities"] >= 1

    def test_adds_constraints_and_links(self, tmp_path: Path) -> None:
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)

        config_path = tmp_path / ".mementic.json"
        config_data = {
            "sources": {"adr_dirs": [{"path": str(adr_dir)}]},
            "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
        }
        config_path.write_text(json.dumps(config_data))

        with patch(
            "mementic.pipeline.extract_from_adr_consequences",
            return_value=_mock_adr_result().constraints,
        ):
            gs = run_init(str(config_path))

        constraints = gs.get_all_constraints()
        assert len(constraints) >= 1


class TestRunInitEmptyConfig:
    def test_empty_config_returns_empty_graph(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".mementic.json"
        config_data = {
            "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
        }
        config_path.write_text(json.dumps(config_data))

        gs = run_init(str(config_path))

        stats = gs.stats()
        assert stats["decisions"] == 0
        assert stats["constraints"] == 0
        assert stats["entities"] == 0
        assert stats["edges"] == 0

    def test_missing_config_file_returns_empty_graph(self, tmp_path: Path) -> None:
        config_path = str(tmp_path / "nonexistent.json")
        gs = run_init(config_path)

        stats = gs.stats()
        assert stats == {"decisions": 0, "constraints": 0, "entities": 0, "edges": 0}


class TestRunInitWithOpenCode:
    def test_extracts_from_opencode_instructions(self, tmp_path: Path) -> None:
        oc_dir = tmp_path / "instructions"
        oc_dir.mkdir()
        (oc_dir / "python-style.md").write_text(OPECODE_INSTRUCTION)

        config_path = tmp_path / ".mementic.json"
        config_data = {
            "sources": {"opencode_instructions": [{"path": str(oc_dir)}]},
            "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
        }
        config_path.write_text(json.dumps(config_data))

        with patch(
            "mementic.pipeline.extract_from_freeform",
            return_value=_mock_freeform_result(),
        ):
            gs = run_init(str(config_path))

        decisions = gs.get_all_decisions()
        assert len(decisions) == 1
        assert decisions[0].title == "Use pydantic for models"

        constraints = gs.get_all_constraints()
        assert len(constraints) == 1
        assert constraints[0].description == "All I/O must be async"

        stats = gs.stats()
        assert stats["entities"] >= 1

    def test_extraction_with_edges(self, tmp_path: Path) -> None:
        oc_dir = tmp_path / "instructions"
        oc_dir.mkdir()
        (oc_dir / "style.md").write_text("# Style\nUse async.")

        config_path = tmp_path / ".mementic.json"
        config_data = {
            "sources": {"opencode_instructions": [{"path": str(oc_dir)}]},
            "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
        }
        config_path.write_text(json.dumps(config_data))

        result = _mock_freeform_result()
        from mementic.models import GraphEdge

        result.edges_to_create = [
            GraphEdge(source="d-opencode-1", target="c-opencode-1", relation="enforces"),
            GraphEdge(source="c-opencode-1", target="src/services/io.py", relation="binds"),
        ]

        with patch(
            "mementic.pipeline.extract_from_freeform", return_value=result
        ):
            gs = run_init(str(config_path))

        stats = gs.stats()
        assert stats["edges"] >= 2


class TestRunInitCombined:
    def test_both_adr_and_opencode_sources(self, tmp_path: Path) -> None:
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)

        oc_dir = tmp_path / "instructions"
        oc_dir.mkdir()
        (oc_dir / "style.md").write_text(OPECODE_INSTRUCTION)

        config_path = tmp_path / ".mementic.json"
        config_data = {
            "sources": {
                "adr_dirs": [{"path": str(adr_dir)}],
                "opencode_instructions": [{"path": str(oc_dir)}],
            },
            "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
        }
        config_path.write_text(json.dumps(config_data))

        with patch(
            "mementic.pipeline.extract_from_freeform",
            return_value=_mock_freeform_result(),
        ), patch(
            "mementic.pipeline.extract_from_adr_consequences",
            return_value=[],
        ):
            gs = run_init(str(config_path))

        decisions = gs.get_all_decisions()
        assert len(decisions) == 2
        sources = {d.source for d in decisions}
        assert any(s.startswith("adr:") for s in sources)
        assert any(s.startswith("opencode:") for s in sources)


class TestSaveAndLoad:
    def test_graph_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)

        graph_path = tmp_path / ".mementic" / "graph.json"

        config_path = tmp_path / ".mementic.json"
        config_data = {
            "sources": {"adr_dirs": [{"path": str(adr_dir)}]},
            "output": {"graph_path": str(graph_path)},
        }
        config_path.write_text(json.dumps(config_data))

        with patch(
            "mementic.pipeline.extract_from_adr_consequences",
            return_value=[],
        ):
            gs1 = run_init(str(config_path))

        assert graph_path.is_file()

        from mementic.graph import GraphStore

        gs2 = GraphStore.load(str(graph_path))
        assert gs2.get_all_decisions() == gs1.get_all_decisions()
        assert gs2.get_all_constraints() == gs1.get_all_constraints()
        assert gs2.stats() == gs1.stats()
