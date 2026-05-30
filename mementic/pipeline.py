from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mementic.config import load_config
from mementic.extraction import extract_from_adr_consequences, extract_from_freeform
from mementic.format import slugify
from mementic.graph import GraphStore
from mementic.models import (
    CodeEntity,
    Constraint,
    Decision,
    DecisionStatus,
    EntityType,
    ExtractionResult,
)
from mementic.sources.adr import scan_adr_dir
from mementic.sources.opencode import scan_opencode_instructions


def find_file_paths(text: str) -> list[str]:
    """Find potential file paths in text matching common source file patterns."""
    pattern = r"[\w/\-]+\.(?:py|ts|js|java|go|rs|rb|yaml|yml|json|toml|md)"
    return list(set(re.findall(pattern, text)))


def _status_from_adr(status: str) -> DecisionStatus:
    s = status.strip().lower()
    if s == "proposed":
        return DecisionStatus.PROPOSED
    if s in ("deprecated", "superseded"):
        return DecisionStatus.SUPERSEDED
    return DecisionStatus.ACCEPTED


def _add_decision_dedup(graph: GraphStore, decision: Decision) -> bool:
    """Add decision if not duplicate. Merge sources on duplicate. Returns True if added."""
    if decision.id in graph._g:
        node = graph._g.nodes[decision.id]
        existing_source = node.get("payload", {}).get("source", "")
        if decision.source not in existing_source:
            node["payload"]["source"] = f"{existing_source}, {decision.source}"
        return False
    graph.add_decision(decision)
    return True


def _add_constraint_dedup(graph: GraphStore, constraint: Constraint) -> bool:
    if constraint.id in graph._g:
        return False
    graph.add_constraint(constraint)
    return True


def _ingest_result(graph: GraphStore, result: ExtractionResult, source_path: str) -> None:
    """Add extracted decisions, constraints, and entities to the graph."""
    for d in result.decisions:
        _add_decision_dedup(graph, d)
    for c in result.constraints:
        if _add_constraint_dedup(graph, c):
            _ensure_code_entities(graph, c, source_path, result)
    for entity_path in result.affected_entities:
        graph.add_code_entity(CodeEntity(id=entity_path, type=EntityType.FILE))
    for edge in result.edges_to_create:
        graph.link(edge.source, edge.target, edge.relation)


def _ensure_code_entities(
    graph: GraphStore, constraint: Any, source_path: str, result: Any
) -> None:
    """Ensure CodeEntity nodes exist for bound entities, falling back to source path."""
    entities = list(constraint.bound_entities)
    if not entities:
        entities = find_file_paths(constraint.description)
    if not entities:
        entities = [source_path]
    for entity_path in entities:
        graph.add_code_entity(CodeEntity(id=entity_path, type=EntityType.FILE))
        graph.link(constraint.id, entity_path, "binds_to")
    for entity_path in result.affected_entities:
        graph.add_code_entity(CodeEntity(id=entity_path, type=EntityType.FILE))


def run_init(config_path: str = ".mementic.json") -> GraphStore:
    """Initialize the knowledge graph from all configured sources."""
    cfg = load_config(config_path)
    graph = GraphStore()

    for adr_source in cfg.sources.adr_dirs:
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
                timestamp=adr.timestamp,
            )
            _add_decision_dedup(graph, decision)

            if adr.consequences.strip():
                constraints = extract_from_adr_consequences(adr.consequences, cfg.llm)
                for c in constraints:
                    c.enforced_by = decision_id
                    if _add_constraint_dedup(graph, c):
                        _ensure_code_entities(graph, c, adr.path, ExtractionResult())

            for path_ in find_file_paths(adr.decision + " " + adr.consequences):
                graph.add_code_entity(CodeEntity(id=path_, type=EntityType.FILE))

    for oc_source in cfg.sources.opencode_instructions:
        instructions = scan_opencode_instructions(oc_source.path)
        for instr in instructions:
            result = extract_from_freeform(instr.content, instr.path, cfg.llm)
            _ingest_result(graph, result, instr.path)

    Path(cfg.output.graph_path).parent.mkdir(parents=True, exist_ok=True)
    graph.save(cfg.output.graph_path)
    return graph
