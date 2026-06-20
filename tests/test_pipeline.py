from __future__ import annotations

from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest

from mementic.config import (
    AdrDirSource,
    ClaudeSource,
    CursorSource,
    OpenCodeSource,
    Sources,
    WindsurfSource,
    load_global_config,
    save_global_config,
)
from mementic.models import (
    Constraint,
    Decision,
    DecisionStatus,
    ExtractionResult,
    GraphEdge,
    UsageStats,
)
from mementic.format import find_file_paths
from mementic.pipeline import (
    _add_constraint_dedup,
    _add_decision_dedup,
    _backfill_decision_edges,
    _backfill_edges,
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


# ── Test helpers ────────────────────────────────────────────────────


_SOURCE_MAP = {
    "adr_dirs": AdrDirSource,
    "opencode_instructions": OpenCodeSource,
    "claude_instructions": ClaudeSource,
    "cursor_instructions": CursorSource,
    "windsurf_instructions": WindsurfSource,
}


def _configure_repo(
    repo_path: str,
    sources: dict[str, list[str]] | None = None,
    extraction_mode: Literal["auto", "deterministic", "llm"] = "deterministic",
) -> None:
    """Set sources and extraction_mode on the registered repo in the global config.

    Sources are attached to the RepoEntry whose path resolves to ``repo_path``.
    The global config is loaded, mutated, and saved in place. This relies on
    the ``tmp_global_config`` / autouse fixture having pointed
    MEMENTIC_GLOBAL_CONFIG at a writable temp file.

    Args:
        repo_path: Absolute path to the registered repo.
        sources: Optional mapping of source-type name -> list of dir paths.
        extraction_mode: ``"deterministic"``, ``"llm"``, or ``"auto"``.
    """
    global_cfg = load_global_config()
    global_cfg.llm.extraction_mode = extraction_mode

    sources_obj = Sources()
    if sources:
        for key, paths in sources.items():
            cls = _SOURCE_MAP[key]
            setattr(sources_obj, key, [cls(path=p) for p in paths])

    resolved = Path(repo_path).resolve()
    for proj in global_cfg.projects.values():
        for repo in proj.repos.values():
            if Path(repo.path).resolve() == resolved:
                repo.sources = sources_obj
    save_global_config(global_cfg)


def _enable_llm_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set a fake API key so ``_should_use_llm`` returns True under ``auto`` mode.

    Also forces the LLM provider's key env var to be present. Tests that need
    LLM dispatch should set ``extraction_mode="llm"`` via ``_configure_repo``
    and call this helper.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")


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
        self, tmp_global_config: tuple[Path, str], tmp_path: Path
    ) -> None:
        """ADR ingestion creates a Decision with the parsed title and rationale."""
        _config_path, repo_path = tmp_global_config
        # Place the ADR dir OUTSIDE the repo so the repo-wide markdown scan
        # (Phase 0) doesn't pick it up too.
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)
        _configure_repo(repo_path, sources={"adr_dirs": [str(adr_dir)]})

        gs, _all_usage = run_init(repo_path=repo_path)

        decisions = gs.get_all_decisions()
        assert len(decisions) == 1
        d = decisions[0]
        assert d.title == "Use PostgreSQL as the primary database"
        assert d.status == DecisionStatus.ACCEPTED
        assert "PostgreSQL 16" in d.rationale
        assert "ACID guarantees" in d.rationale

    def test_maps_status_correctly(
        self, tmp_global_config: tuple[Path, str], tmp_path: Path
    ) -> None:
        """ADR Status 'Proposed' → ACCEPTED, 'Superseded' → DEPRECATED."""
        _config_path, repo_path = tmp_global_config
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0002-redis.md").write_text(ADR_PROPOSED)
        (adr_dir / "0003-deprecated.md").write_text(ADR_DEPRECATED)
        _configure_repo(repo_path, sources={"adr_dirs": [str(adr_dir)]})

        gs, _all_usage = run_init(repo_path=repo_path)

        decisions = gs.get_all_decisions()
        assert len(decisions) == 2
        statuses = {d.title: d.status for d in decisions}
        assert statuses["Introduce Redis caching"] == DecisionStatus.ACCEPTED
        assert statuses["Old REST API approach"] == DecisionStatus.DEPRECATED

    def test_creates_code_entities_from_file_paths(
        self, tmp_global_config: tuple[Path, str], tmp_path: Path
    ) -> None:
        """ADR consequences referencing file paths produce CodeEntity nodes."""
        _config_path, repo_path = tmp_global_config
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)
        _configure_repo(repo_path, sources={"adr_dirs": [str(adr_dir)]})

        gs, _all_usage = run_init(repo_path=repo_path)

        stats = gs.stats()
        assert stats["entities"] >= 1

    def test_adds_constraints_and_links(
        self, tmp_global_config: tuple[Path, str], tmp_path: Path
    ) -> None:
        """ADR consequences with modal-language lines produce constraints.

        In deterministic mode (no API key) the consequences text is scanned
        by ``extract_constraints_from_consequences``. The ADR_CONTENT
        consequences contain 'must' lines, so at least one constraint is
        created and linked to the decision.
        """
        _config_path, repo_path = tmp_global_config
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)
        _configure_repo(repo_path, sources={"adr_dirs": [str(adr_dir)]})

        gs, _all_usage = run_init(repo_path=repo_path)

        constraints = gs.get_all_constraints()
        assert len(constraints) >= 1


class TestRunInitEmptyConfig:
    def test_empty_config_returns_empty_graph(
        self, tmp_global_config: tuple[Path, str]
    ) -> None:
        """A registered repo with no sources produces an empty graph."""
        _config_path, repo_path = tmp_global_config

        gs, _all_usage = run_init(repo_path=repo_path)

        stats = gs.stats()
        assert stats["decisions"] == 0
        assert stats["constraints"] == 0
        assert stats["entities"] == 0
        assert stats["edges"] == 0

    def test_unregistered_repo_returns_empty_graph(self, tmp_path: Path) -> None:
        """A repo not registered in the global config yields defaults (empty).

        This is the new-API equivalent of the old 'missing .mementic.json'
        test: when load_config cannot find the repo in the global config it
        returns a MementicConfig with empty sources.
        """
        # The autouse _isolate_global_config fixture points
        # MEMENTIC_GLOBAL_CONFIG at a nonexistent path, so load_global_config
        # returns a default empty GlobalConfig with no projects.
        repo_path = tmp_path / "unregistered_repo"
        repo_path.mkdir()

        gs, _all_usage = run_init(repo_path=str(repo_path))

        stats = gs.stats()
        assert stats == {"decisions": 0, "constraints": 0, "entities": 0, "edges": 0}


class TestRunInitWithOpenCode:
    def test_extracts_from_opencode_instructions(
        self,
        tmp_global_config: tuple[Path, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pipeline ingests opencode_instructions via the markdown dispatcher.

        Deterministic mode (default, no API key) dispatches to
        ``extract_from_markdown``. We mock it to return a fixed
        ExtractionResult so the test asserts pipeline plumbing, not the
        deterministic extractor's heuristics.
        """
        _config_path, repo_path = tmp_global_config
        oc_dir = tmp_path / "instructions"
        oc_dir.mkdir()
        (oc_dir / "python-style.md").write_text(OPECODE_INSTRUCTION)
        _configure_repo(
            repo_path, sources={"opencode_instructions": [str(oc_dir)]}
        )

        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: _mock_freeform_result(),
        )

        gs, _all_usage = run_init(repo_path=repo_path)

        decisions = gs.get_all_decisions()
        assert len(decisions) == 1
        assert decisions[0].title == "Use pydantic for models"

        constraints = gs.get_all_constraints()
        assert len(constraints) == 1
        assert constraints[0].description == "All I/O must be async"

        stats = gs.stats()
        assert stats["entities"] >= 1

    def test_extraction_with_edges(
        self,
        tmp_global_config: tuple[Path, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """edges_to_create from an extraction result are linked into the graph."""
        _config_path, repo_path = tmp_global_config
        oc_dir = tmp_path / "instructions"
        oc_dir.mkdir()
        (oc_dir / "style.md").write_text("# Style\nUse async.")
        _configure_repo(
            repo_path, sources={"opencode_instructions": [str(oc_dir)]}
        )

        result = _mock_freeform_result()
        result.edges_to_create = [
            GraphEdge(source="d-opencode-1", target="c-opencode-1", relation="enforces"),
            GraphEdge(source="c-opencode-1", target="src/services/io.py", relation="binds"),
        ]
        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: result,
        )

        gs, _all_usage = run_init(repo_path=repo_path)

        stats = gs.stats()
        assert stats["edges"] >= 2


class TestRunInitWithClaude:
    def test_extracts_from_claude_instructions(
        self,
        tmp_global_config: tuple[Path, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pipeline ingests claude_instructions via the markdown dispatcher."""
        _config_path, repo_path = tmp_global_config
        claude_dir = tmp_path / "claude_instructions"
        claude_dir.mkdir()
        (claude_dir / "style.md").write_text("# Claude Style\nUse async everywhere.")
        _configure_repo(
            repo_path, sources={"claude_instructions": [str(claude_dir)]}
        )

        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: _mock_claude_freeform_result(),
        )

        gs, _all_usage = run_init(repo_path=repo_path)

        decisions = gs.get_all_decisions()
        assert len(decisions) == 1
        assert decisions[0].title == "Use async handlers"

        constraints = gs.get_all_constraints()
        assert len(constraints) == 1
        assert constraints[0].description == "All handlers must be async"

        stats = gs.stats()
        assert stats["entities"] >= 1


class TestRunInitCombined:
    def test_both_adr_and_opencode_sources(
        self,
        tmp_global_config: tuple[Path, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ADR + opencode sources each contribute decisions to the graph."""
        _config_path, repo_path = tmp_global_config
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)

        oc_dir = tmp_path / "instructions"
        oc_dir.mkdir()
        (oc_dir / "style.md").write_text(OPECODE_INSTRUCTION)

        _configure_repo(
            repo_path,
            sources={
                "adr_dirs": [str(adr_dir)],
                "opencode_instructions": [str(oc_dir)],
            },
        )

        # Deterministic dispatcher: mock the markdown extractor for the
        # opencode source. ADR decisions are produced by scan_adr_dir directly,
        # and ADR consequences run through extract_constraints_from_consequences
        # (deterministic) — no mock needed for the decision count.
        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: _mock_freeform_result(),
        )

        gs, _all_usage = run_init(repo_path=repo_path)

        decisions = gs.get_all_decisions()
        assert len(decisions) == 2
        sources = {d.source for d in decisions}
        assert "adr" in sources
        assert "opencode:instructions/python-style.md" in sources

    def test_all_three_source_types(
        self,
        tmp_global_config: tuple[Path, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ADR + opencode + claude sources each contribute decisions."""
        _config_path, repo_path = tmp_global_config
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)

        oc_dir = tmp_path / "opencode_instructions"
        oc_dir.mkdir()
        (oc_dir / "python-style.md").write_text(OPECODE_INSTRUCTION)

        claude_dir = tmp_path / "claude_instructions"
        claude_dir.mkdir()
        (claude_dir / "style.md").write_text("# Claude Style\nBe async.")

        _configure_repo(
            repo_path,
            sources={
                "adr_dirs": [str(adr_dir)],
                "opencode_instructions": [str(oc_dir)],
                "claude_instructions": [str(claude_dir)],
            },
        )

        # Pipeline order: ADR loop → opencode loop → claude loop. The markdown
        # dispatcher is called once per opencode file then once per claude file.
        results = iter([
            _mock_freeform_result(),
            _mock_claude_freeform_result(),
        ])
        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: next(results),
        )

        gs, _all_usage = run_init(repo_path=repo_path)

        decisions = gs.get_all_decisions()
        assert len(decisions) == 3
        sources = {d.source for d in decisions}
        assert "adr" in sources
        assert "opencode:instructions/python-style.md" in sources
        assert "claude:instructions/style.md" in sources


class TestSaveAndLoad:
    def test_graph_save_and_load_roundtrip(
        self,
        tmp_global_config: tuple[Path, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Graph persisted to SQLite and reloads identically from the same db.

        run_init resolves the db_path from the project config, writes to
        SQLite, and a second GraphStore loaded from the same db_path sees
        identical data.
        """
        _config_path, repo_path = tmp_global_config
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "0001-use-postgres.md").write_text(ADR_CONTENT)
        _configure_repo(repo_path, sources={"adr_dirs": [str(adr_dir)]})

        # Resolve the db_path the same way run_init does
        from mementic.config import load_global_config
        global_cfg = load_global_config()
        default_proj = global_cfg.projects[global_cfg.default_project]
        db_path = Path(default_proj.db_path).expanduser()

        gs1, _all_usage = run_init(repo_path=repo_path)

        assert db_path.is_file()

        from mementic.graph import GraphStore

        gs2 = GraphStore(db_path=db_path)
        assert gs2.get_all_decisions() == gs1.get_all_decisions()
        assert gs2.get_all_constraints() == gs1.get_all_constraints()
        assert gs2.stats() == gs1.stats()


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
        tmp_global_config: tuple[Path, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pipeline ingests cursor_instructions and creates decisions/constraints."""
        _config_path, repo_path = tmp_global_config
        cursor_dir = tmp_path / "cursor_instructions"
        cursor_dir.mkdir()
        (cursor_dir / "typescript.md").write_text("# TypeScript Style\nUse strict mode.")
        _configure_repo(
            repo_path, sources={"cursor_instructions": [str(cursor_dir)]}
        )

        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: _mock_cursor_freeform_result(),
        )

        gs, _all_usage = run_init(repo_path=repo_path)

        decisions = gs.get_all_decisions()
        assert len(decisions) == 1
        assert decisions[0].title == "Use TypeScript strict mode"

        constraints = gs.get_all_constraints()
        assert len(constraints) == 1
        assert constraints[0].description == "All TS configs must enable strict mode"

        stats = gs.stats()
        assert stats["entities"] >= 1

    def test_cursor_source_usage_tracking(
        self,
        tmp_global_config: tuple[Path, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pipeline tracks LLM UsageStats from cursor instruction extraction.

        Requires LLM dispatch (extraction_mode='llm' + API key) so the
        dispatcher calls ``extract_from_freeform`` and surfaces its
        UsageStats. Deterministic mode would emit ``model='deterministic'``.
        """
        _config_path, repo_path = tmp_global_config
        cursor_dir = tmp_path / "cursor_instructions"
        cursor_dir.mkdir()
        (cursor_dir / "style.md").write_text("# Cursor Style\nUse strict mode.")
        _configure_repo(
            repo_path,
            sources={"cursor_instructions": [str(cursor_dir)]},
            extraction_mode="llm",
        )
        _enable_llm_mode(monkeypatch)

        monkeypatch.setattr(
            "mementic.pipeline.extract_from_freeform",
            lambda content, label, llm_cfg: (
                _mock_cursor_freeform_result(),
                UsageStats(model="test-model", status="success"),
            ),
        )

        _gs, all_usage = run_init(repo_path=repo_path)

        assert len(all_usage) == 1
        assert all_usage[0].model == "test-model"
        assert all_usage[0].status == "success"


class TestRunInitWithWindsurf:
    """Tests for Windsurf instruction source ingestion via pipeline (Spec 09c C1)."""

    def test_extracts_from_windsurf_instructions(
        self,
        tmp_global_config: tuple[Path, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pipeline ingests windsurf_instructions and creates decisions/constraints."""
        _config_path, repo_path = tmp_global_config
        windsurf_dir = tmp_path / "windsurf_instructions"
        windsurf_dir.mkdir()
        (windsurf_dir / "styling.md").write_text("# Windsurf Style\nUse Tailwind.")
        _configure_repo(
            repo_path, sources={"windsurf_instructions": [str(windsurf_dir)]}
        )

        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: _mock_windsurf_freeform_result(),
        )

        gs, _all_usage = run_init(repo_path=repo_path)

        decisions = gs.get_all_decisions()
        assert len(decisions) == 1
        assert decisions[0].title == "Use Tailwind for styling"

        constraints = gs.get_all_constraints()
        assert len(constraints) == 1
        assert constraints[0].description == "All components must use Tailwind classes"

        stats = gs.stats()
        assert stats["entities"] >= 1

    def test_windsurf_source_usage_tracking(
        self,
        tmp_global_config: tuple[Path, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pipeline tracks LLM UsageStats from windsurf instruction extraction."""
        _config_path, repo_path = tmp_global_config
        windsurf_dir = tmp_path / "windsurf_instructions"
        windsurf_dir.mkdir()
        (windsurf_dir / "style.md").write_text("# Windsurf Style\nUse Tailwind.")
        _configure_repo(
            repo_path,
            sources={"windsurf_instructions": [str(windsurf_dir)]},
            extraction_mode="llm",
        )
        _enable_llm_mode(monkeypatch)

        monkeypatch.setattr(
            "mementic.pipeline.extract_from_freeform",
            lambda content, label, llm_cfg: (
                _mock_windsurf_freeform_result(),
                UsageStats(model="test-model", status="success"),
            ),
        )

        _gs, all_usage = run_init(repo_path=repo_path)

        assert len(all_usage) == 1
        assert all_usage[0].model == "test-model"
        assert all_usage[0].status == "success"


class TestRunInitCombinedCursorWindsurf:
    """Tests for combined cursor + windsurf pipeline ingestion."""

    def test_cursor_and_windsurf_together(
        self,
        tmp_global_config: tuple[Path, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pipeline ingests both cursor and windsurf sources simultaneously."""
        _config_path, repo_path = tmp_global_config
        cursor_dir = tmp_path / "cursor_dir"
        cursor_dir.mkdir()
        (cursor_dir / "ts.md").write_text("# Cursor TS rules")

        windsurf_dir = tmp_path / "windsurf_dir"
        windsurf_dir.mkdir()
        (windsurf_dir / "css.md").write_text("# Windsurf CSS rules")

        _configure_repo(
            repo_path,
            sources={
                "cursor_instructions": [str(cursor_dir)],
                "windsurf_instructions": [str(windsurf_dir)],
            },
        )

        # Pipeline order: cursor loop → windsurf loop.
        results = iter([
            _mock_cursor_freeform_result(),
            _mock_windsurf_freeform_result(),
        ])
        monkeypatch.setattr(
            "mementic.pipeline.extract_from_markdown",
            lambda content, label: next(results),
        )

        gs, all_usage = run_init(repo_path=repo_path)

        decisions = gs.get_all_decisions()
        assert len(decisions) == 2
        sources = {d.source for d in decisions}
        assert any(s.startswith("cursor:") for s in sources)
        assert any(s.startswith("windsurf:") for s in sources)
        assert len(all_usage) == 2


# ── Spec 12a: Codebase Scanner Pipeline Integration ────────────────


class TestRunInitWithRepoScan:
    """Tests for run_init codebase scanning (Spec 12a).

    The codebase structural scan only runs when ``_should_use_llm(cfg)`` is
    True (there is no deterministic equivalent). These tests therefore set
    ``extraction_mode='llm'`` plus a fake API key.
    """

    def test_init_with_repo_scans_codebase(
        self,
        tmp_global_config: tuple[Path, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_init with repo_path scans the codebase and extracts decisions."""
        _config_path, repo_path = tmp_global_config
        # Create a pyproject.toml and Python file for the scanner to find.
        repo = Path(repo_path)
        (repo / "pyproject.toml").write_text(
            "[project]\nname = \"test-project\"\n"
        )
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text(
            "import fastapi\nfrom pydantic import BaseModel\n"
        )
        _configure_repo(repo_path, sources=None, extraction_mode="llm")
        _enable_llm_mode(monkeypatch)

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

        with patch(
            "mementic.pipeline.extract_from_codebase_summary",
            return_value=(mock_result, UsageStats(model="test-model", status="success")),
        ):
            gs, _all_usage = run_init(repo_path=repo_path)

        decisions = gs.get_all_decisions()
        assert len(decisions) >= 1
        assert any(d.id == "d-fastapi" for d in decisions)

    def test_init_with_no_scan_skips_scanner(
        self,
        tmp_global_config: tuple[Path, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_init with no_scan=True skips the codebase scanner entirely."""
        _config_path, repo_path = tmp_global_config
        repo = Path(repo_path)
        (repo / "pyproject.toml").write_text("[project]\nname = \"test\"\n")
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text("import fastapi\n")
        _configure_repo(repo_path, sources=None, extraction_mode="llm")
        _enable_llm_mode(monkeypatch)

        with patch(
            "mementic.pipeline.extract_from_codebase_summary"
        ) as mock_extract:
            _gs, all_usage = run_init(repo_path=repo_path, no_scan=True)

        mock_extract.assert_not_called()
        assert len(all_usage) == 0

    def test_init_repo_path_none_skips_scanner(
        self,
        tmp_global_config: tuple[Path, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_init without repo_path does not call the scanner."""
        _config_path, _repo_path = tmp_global_config
        _configure_repo(_repo_path, sources=None, extraction_mode="llm")
        _enable_llm_mode(monkeypatch)

        with patch(
            "mementic.pipeline.extract_from_codebase_summary"
        ) as mock_extract:
            _gs, all_usage = run_init()

        mock_extract.assert_not_called()
        assert len(all_usage) == 0

    def test_init_with_repo_scanner_empty_summary(
        self,
        tmp_global_config: tuple[Path, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When codebase scan produces empty summary, no LLM call is made.

        The registered repo from ``tmp_global_config`` is empty (no
        pyproject.toml, no source files), so ``scan_codebase_structure``
        returns an empty string and the extraction step is skipped.
        """
        _config_path, repo_path = tmp_global_config
        _configure_repo(repo_path, sources=None, extraction_mode="llm")
        _enable_llm_mode(monkeypatch)

        with patch(
            "mementic.pipeline.extract_from_codebase_summary"
        ) as mock_extract:
            _gs, all_usage = run_init(repo_path=repo_path)

        mock_extract.assert_not_called()
        assert len(all_usage) == 0
