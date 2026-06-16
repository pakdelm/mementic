from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mementic.config import load_config
from mementic.extraction import (
    extract_from_adr_consequences,
    extract_from_codebase_summary,
    extract_from_freeform,
)
from mementic.format import slugify
from mementic.graph import GraphStore
from mementic.models import (
    CodeEntity,
    Constraint,
    Decision,
    DecisionStatus,
    EntityType,
    ExtractionResult,
    UsageStats,
)
from mementic.sources.adr import scan_adr_dir
from mementic.sources.codebase import scan_codebase_structure
from mementic.sources.opencode import scan_opencode_instructions


def _entity_id(repo_path: str | None, relative_path: str) -> str:
    """Create a namespaced entity ID when repo is known."""
    if repo_path:
        return f"{repo_path}::{relative_path}"
    return relative_path


def find_file_paths(text: str) -> list[str]:
    """Find potential file paths in text matching common source file patterns."""
    pattern = r"[\w/\-]+\.(?:py|ts|js|java|go|rs|rb|yaml|yml|json|toml|md)\b"
    return list(set(re.findall(pattern, text)))


def _status_from_adr(status: str) -> DecisionStatus:
    s = status.strip().lower()
    if s in ("deprecated", "superseded"):
        return DecisionStatus.DEPRECATED
    return DecisionStatus.ACCEPTED


def _add_decision_dedup(graph: GraphStore, decision: Decision) -> bool:
    """Add decision if not duplicate. Merge sources on duplicate. Returns True if added."""
    if graph.has_node(decision.id):
        payload = graph.get_node_payload(decision.id)
        existing_source = payload.get("source", "")
        if decision.source not in existing_source:
            payload["source"] = f"{existing_source}, {decision.source}"
            graph.set_node_payload(decision.id, payload)
        return False
    graph.add_decision(decision)
    return True


def _add_constraint_dedup(graph: GraphStore, constraint: Constraint) -> bool:
    if graph.has_node(constraint.id):
        return False
    graph.add_constraint(constraint)
    return True


def _ingest_result(
    graph: GraphStore, result: ExtractionResult, source_path: str,
    repo_path: str | None = None,
) -> None:
    """Add extracted decisions, constraints, and entities to the graph."""
    for d in result.decisions:
        d.repo = repo_path or ""
        _add_decision_dedup(graph, d)
        for entity_path in result.affected_entities:
            eid = _entity_id(repo_path, entity_path)
            graph.add_code_entity(
                CodeEntity(id=eid, type=EntityType.FILE, repo=repo_path or "")
            )
            graph.link(d.id, eid, "affects")
    for c in result.constraints:
        c.repo = repo_path or ""
        if _add_constraint_dedup(graph, c):
            _ensure_code_entities(graph, c, source_path, result, repo_path=repo_path)
    for edge in result.edges_to_create:
        graph.link(edge.source, edge.target, edge.relation)


def _ensure_code_entities(
    graph: GraphStore, constraint: Any, source_path: str, result: Any,
    repo_path: str | None = None,
) -> None:
    """Ensure CodeEntity nodes exist for bound entities, falling back to source path."""
    entities = list(constraint.bound_entities)
    if not entities:
        entities = find_file_paths(constraint.description)
    if not entities:
        entities = [source_path]
    for entity_path in entities:
        eid = _entity_id(repo_path, entity_path)
        graph.add_code_entity(
            CodeEntity(id=eid, type=EntityType.FILE, repo=repo_path or "")
        )
        graph.link(constraint.id, eid, "binds_to")
    for entity_path in result.affected_entities:
        eid = _entity_id(repo_path, entity_path)
        graph.add_code_entity(
            CodeEntity(id=eid, type=EntityType.FILE, repo=repo_path or "")
        )
        graph.link(constraint.id, eid, "binds_to")


def _backfill_edges(graph: GraphStore, repo_path: str | None = None) -> int:
    """Ensure all constraints have entity links. Returns number of constraints fixed."""
    count = 0
    for node_id, node_type, payload in graph.iter_nodes():
        if node_type != "Constraint":
            continue
        if graph.has_outgoing_edges(node_id):
            continue
        constraint = Constraint(**payload)
        _ensure_code_entities(
            graph, constraint,
            payload.get("source", "unknown"),
            ExtractionResult(),
            repo_path=repo_path,
        )
        count += 1
    return count


def _backfill_decision_edges(
    graph: GraphStore, repo_path: str | None = None
) -> int:
    """Ensure all decisions have entity links. Returns number of decisions fixed."""
    count = 0
    for node_id, node_type, payload in graph.iter_nodes():
        if node_type != "Decision":
            continue
        if graph.has_outgoing_edges(node_id):
            continue
        text_to_scan = (
            payload.get("rationale", "")
            + " "
            + payload.get("source", "")
            + " "
            + payload.get("title", "")
        )
        paths = find_file_paths(text_to_scan)
        if not paths:
            paths = ["src/"]
        for entity_path in paths:
            eid = _entity_id(repo_path, entity_path)
            graph.add_code_entity(
                CodeEntity(id=eid, type=EntityType.FILE, repo=repo_path or "")
            )
            graph.link(node_id, eid, "affects")
        count += 1
    return count


def run_init(
    config_path: str = ".mementic.json",
    repo_path: str | None = None,
    no_scan: bool = False,
    db_path: str | None = None,
) -> tuple[GraphStore, list[UsageStats]]:
    """Initialize the knowledge graph from all configured sources.

    When ``repo_path`` is provided (or CWD is auto-detected as a codebase),
    a deterministic structural scan runs first to detect architectural
    patterns from the codebase itself. This is skipped when ``no_scan``
    is True or when no codebase indicators are found.

    When ``db_path`` is provided, the graph is persisted to SQLite instead
    of the legacy JSON file.  Entity IDs are namespaced with the repo path
    and ``repo`` fields are populated on all decisions, constraints, and
    entities.

    Args:
        config_path: Path to .mementic.json config.
        repo_path: Optional path to the repository root for codebase scanning.
        no_scan: If True, skip the codebase structural scan.
        db_path: Optional path to a SQLite database for persistent storage.

    Returns:
        A tuple of (GraphStore, list[UsageStats]) — usage stats from all
        LLM extraction calls made during init.
    """
    cfg = load_config(config_path)
    graph = GraphStore(db_path=db_path) if db_path else GraphStore()
    all_usage: list[UsageStats] = []

    # Phase 1: Codebase structural scan (Spec 12a) — cold-start killer
    # Only runs when repo_path is explicitly provided (auto-detection is CLI concern)
    if repo_path and not no_scan:
        summary = scan_codebase_structure(repo_path)
        if summary.strip():
            result, usage = extract_from_codebase_summary(summary, cfg.llm)
            all_usage.append(usage)
            if result.decisions:
                sys.stderr.write(
                    f"Codebase scan: detected {len(result.decisions)} "
                    f"potential decision(s) from structural patterns.\n"
                )
            _ingest_result(
                graph, result, source_path=f"repo:{repo_path}",
                repo_path=repo_path,
            )

    for adr_source in cfg.sources.adr_dirs:
        if not Path(adr_source.path).is_dir():
            sys.stderr.write(
                f"Warning: Source '{adr_source.path}' not found. Skipping.\n"
            )
            continue
        adrs = scan_adr_dir(adr_source.path)
        for adr in adrs:
            decision_id = slugify(adr.title)

            decision = Decision(
                id=decision_id,
                title=adr.title,
                rationale=f"Context: {adr.context}\n\nDecision: {adr.decision}",
                rejected_alternatives=[],
                source=f"adr:{adr.path}",
                status=_status_from_adr(adr.status),
                author="adr",
                created_at=adr.timestamp,
                modified_at=adr.timestamp,
                repo=repo_path or "",
            )
            _add_decision_dedup(graph, decision)

            if adr.consequences.strip():
                constraints, usage = extract_from_adr_consequences(adr.consequences, cfg.llm)
                all_usage.append(usage)
                for c in constraints:
                    c.repo = repo_path or ""
                    c.enforced_by = decision_id
                    if _add_constraint_dedup(graph, c):
                        _ensure_code_entities(
                            graph, c, adr.path, ExtractionResult(),
                            repo_path=repo_path,
                        )

            for path_ in find_file_paths(adr.decision + " " + adr.consequences):
                eid = _entity_id(repo_path, path_)
                graph.add_code_entity(
                    CodeEntity(id=eid, type=EntityType.FILE, repo=repo_path or "")
                )
                graph.link(decision.id, eid, "affects")

    for oc_source in cfg.sources.opencode_instructions:
        if not Path(oc_source.path).is_dir():
            sys.stderr.write(
                f"Warning: Source '{oc_source.path}' not found. Skipping.\n"
            )
            continue
        instructions = scan_opencode_instructions(oc_source.path)
        for instr in instructions:
            result, usage = extract_from_freeform(instr.content, instr.path, cfg.llm)
            all_usage.append(usage)
            _ingest_result(graph, result, instr.path, repo_path=repo_path)

    for claude_source in cfg.sources.claude_instructions:
        if not Path(claude_source.path).is_dir():
            sys.stderr.write(
                f"Warning: Source '{claude_source.path}' not found. Skipping.\n"
            )
            continue
        instructions = scan_opencode_instructions(claude_source.path)
        for instr in instructions:
            result, usage = extract_from_freeform(instr.content, instr.path, cfg.llm)
            all_usage.append(usage)
            _ingest_result(graph, result, instr.path, repo_path=repo_path)

    for cursor_source in cfg.sources.cursor_instructions:
        instructions = scan_opencode_instructions(cursor_source.path)
        for instr in instructions:
            result, usage = extract_from_freeform(instr.content, instr.path, cfg.llm)
            all_usage.append(usage)
            _ingest_result(graph, result, instr.path, repo_path=repo_path)

    for windsurf_source in cfg.sources.windsurf_instructions:
        instructions = scan_opencode_instructions(windsurf_source.path)
        for instr in instructions:
            result, usage = extract_from_freeform(instr.content, instr.path, cfg.llm)
            all_usage.append(usage)
            _ingest_result(graph, result, instr.path, repo_path=repo_path)

    has_sources = bool(
        cfg.sources.adr_dirs
        or cfg.sources.opencode_instructions
        or cfg.sources.claude_instructions
        or cfg.sources.cursor_instructions
        or cfg.sources.windsurf_instructions
    )
    if has_sources or (repo_path and not no_scan):
        _backfill_edges(graph, repo_path=repo_path)
        _backfill_decision_edges(graph, repo_path=repo_path)
        if db_path is None:
            # Legacy: save to graph.json
            Path(cfg.output.graph_path).parent.mkdir(parents=True, exist_ok=True)
            graph.save(cfg.output.graph_path)
        # When db_path is set, data is auto-persisted to SQLite via _write_back()

    # Track the repo in project_sources for multi-repo awareness
    if db_path and repo_path and graph._conn is not None:
        graph._conn.execute(
            "INSERT OR REPLACE INTO project_sources (repo_path, ingested_at) "
            "VALUES (?, ?)",
            (repo_path, datetime.now(timezone.utc).isoformat()),
        )
        graph._conn.commit()

    return graph, all_usage
