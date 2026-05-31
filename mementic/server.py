"""MCP server exposing Mementic knowledge graph tools."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mementic.config import MementicConfig, load_config
from mementic.format import count_tokens, format_context_pack, slugify, timestamp
from mementic.graph import GraphStore
from mementic.metrics import UsageStore
from mementic.models import CodeEntity, Constraint, Decision, DecisionStatus, EntityType, MetricsEntry

mcp = FastMCP("Mementic")

_graph_lock = threading.Lock()
_metrics_store = UsageStore()


def _load_graph() -> tuple[GraphStore, MementicConfig]:
    """Load the knowledge graph and config from the configured path.

    Returns:
        A tuple of (GraphStore, MementicConfig).
    """
    config_path = os.environ.get("MEMENTIC_CONFIG", ".mementic.json")
    cfg = load_config(config_path)

    graph_path = cfg.output.graph_path
    if Path(graph_path).exists():
        return GraphStore.load(graph_path), cfg
    return GraphStore(), cfg


def _save_graph(graph: GraphStore, cfg: MementicConfig) -> None:
    """Thread-safe save of the graph to its configured path."""
    graph.save(cfg.output.graph_path)


@mcp.tool()
async def get_context_pack(task_description: str, affected_files: list[str]) -> str:
    """Return all relevant Decisions and Constraints for a given task.

    Call this BEFORE starting work on a task.

    Args:
        task_description: Description of the task being started.
        affected_files: List of file paths that will be affected.

    Returns:
        Formatted markdown with relevant architectural context.
    """
    graph, cfg = _load_graph()
    constraints = graph.get_constraints_for_entities(affected_files)
    decisions = graph.get_decisions_for_entities(affected_files)
    pack_text = format_context_pack(task_description, decisions, constraints)
    pack_tokens = count_tokens(pack_text)
    entry = MetricsEntry(
        timestamp=timestamp(),
        operation="mcp_context_pack",
        source_label=task_description[:80],
        input_tokens=0,
        output_tokens=0,
        total_tokens=pack_tokens,
        model="",
        status="success",
    )
    _metrics_store.append(entry)
    return pack_text


@mcp.tool()
async def propose_decision(
    title: str,
    rationale: str,
    rejected_alternatives: list[str] | None = None,
    affected_entities: list[str] | None = None,
) -> str:
    """Propose a new architectural decision for human review.

    Will be saved with status 'proposed' and must be accepted via `mementic review`.

    Args:
        title: Short title for the decision.
        rationale: Justification for the decision.
        rejected_alternatives: Alternatives that were considered and rejected.
        affected_entities: Code entity paths affected by this decision.

    Returns:
        Confirmation message with save status.
    """
    with _graph_lock:
        graph, cfg = _load_graph()

        decision = Decision(
            id=slugify(title, max_length=80),
            title=title,
            rationale=rationale,
            rejected_alternatives=rejected_alternatives or [],
            source=f"agent-proposal:{timestamp()}",
            status=DecisionStatus.PROPOSED,
            author="opencode",
            timestamp=timestamp(),
        )

        graph.add_decision(decision)

        for entity_path in affected_entities or []:
            if entity_path == decision.id or not entity_path:
                continue
            entity = CodeEntity(id=entity_path, type=EntityType.FILE, repo="")
            graph.add_code_entity(entity)
            graph.link(decision.id, entity_path, "affects")

        _save_graph(graph, cfg)
    return f"Decision '{title}' saved for review (status: proposed). Use `mementic review` to accept."


@mcp.tool()
async def propose_constraint(
    description: str,
    bound_entities: list[str],
    decision_id: str | None = None,
    id: str | None = None,
) -> str:
    """Propose a new constraint for human review.

    Args:
        description: Description of the constraint.
        bound_entities: Code entity paths this constraint applies to.
        decision_id: Optional decision that enforces this constraint.
        id: Optional explicit ID for the constraint. If not provided,
            generated from the description (capped at 80 chars).

    Returns:
        Confirmation message with save status.
    """
    with _graph_lock:
        graph, cfg = _load_graph()

        constraint_id = id or slugify(description, max_length=80)

        constraint = Constraint(
            id=constraint_id,
            description=description,
            bound_entities=bound_entities,
            enforced_by=decision_id or "",
            source=f"agent-proposal:{timestamp()}",
            status="proposed",
            timestamp=timestamp(),
        )

        graph.add_constraint(constraint)

        for entity_path in bound_entities:
            entity = CodeEntity(id=entity_path, type=EntityType.FILE, repo="")
            graph.add_code_entity(entity)
            graph.link(constraint.id, entity_path, "binds_to")

        _save_graph(graph, cfg)
    return f"Constraint '{constraint.id}' saved for review."


@mcp.tool()
async def get_system_evolution(entity_path: str) -> str:
    """Trace all decisions that affected a specific code entity.

    Args:
        entity_path: Path of the code entity to trace.

    Returns:
        Markdown summary of all decisions affecting the entity.
    """
    graph, cfg = _load_graph()
    decisions = graph.get_evolution(entity_path)

    if not decisions:
        return f"No architectural decisions found for {entity_path}."

    lines = [f"## Evolution of {entity_path}\n"]
    for d in decisions:
        lines.append(f"- [{d.timestamp}] **{d.title}**: {d.rationale}")
        if d.rejected_alternatives:
            lines.append(f"  Rejected: {', '.join(d.rejected_alternatives)}")

    return "\n".join(lines)
