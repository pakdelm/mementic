from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mementic.config import load_config
from mementic.extraction import extract_from_freeform
from mementic.graph import GraphStore
from mementic.models import CodeEntity, EntityType, ExtractionResult
from mementic.sources.adr import scan_adr_dir
from mementic.sources.opencode import scan_opencode_instructions


def find_file_paths(text: str) -> list[str]:
    """Find potential file paths in text matching common source file patterns."""
    pattern = r"[\w/\-]+\.(?:py|ts|js|java|go|rs|rb|yaml|yml|json|toml|md)"
    return list(set(re.findall(pattern, text)))


def _ingest_result(graph: GraphStore, result: ExtractionResult, source_path: str) -> None:
    """Add extracted decisions, constraints, and entities to the graph."""
    for d in result.decisions:
        graph.add_decision(d)
    for c in result.constraints:
        graph.add_constraint(c)
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
            text = (
                f"Title: {adr.title}\n\n"
                f"Context: {adr.context}\n\n"
                f"Decision: {adr.decision}\n\n"
                f"Consequences: {adr.consequences}"
            )
            result = extract_from_freeform(text, adr.path, cfg.llm)
            _ingest_result(graph, result, adr.path)

    for oc_source in cfg.sources.opencode_instructions:
        instructions = scan_opencode_instructions(oc_source.path)
        for instr in instructions:
            result = extract_from_freeform(instr.content, instr.path, cfg.llm)
            _ingest_result(graph, result, instr.path)

    Path(cfg.output.graph_path).parent.mkdir(parents=True, exist_ok=True)
    graph.save(cfg.output.graph_path)
    return graph
