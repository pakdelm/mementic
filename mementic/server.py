"""MCP server exposing Mementic knowledge graph tools."""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mementic.config import MementicConfig, load_config
from mementic.format import format_context_pack, slugify, timestamp
from mementic.graph import GraphStore
from mementic.models import CodeEntity, Constraint, Decision, DecisionStatus, EntityType

mcp = FastMCP("Mementic")


def _load_graph() -> tuple[GraphStore, MementicConfig]:
    """Load the knowledge graph and config from the configured path.

    Returns:
        A tuple of (GraphStore, MementicConfig).
    """
    config_path = os.environ.get("MEMENTIC_CONFIG", ".mementic.json")
    cfg = load_config(config_path)

    graph_path = cfg.output.graph_path
    if Path(graph_path).exists():
        graph = GraphStore.load(graph_path)
    else:
        graph = GraphStore()

    return graph, cfg


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
    return format_context_pack(task_description, decisions, constraints)


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
    graph, cfg = _load_graph()

    decision = Decision(
        id=slugify(title),
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
        entity = CodeEntity(id=entity_path, type=EntityType.FILE, repo="")
        graph.add_code_entity(entity)
        graph.link(decision.id, entity_path, "affects")

    Path(cfg.output.graph_path).parent.mkdir(parents=True, exist_ok=True)
    graph.save(cfg.output.graph_path)
    return f"Decision '{title}' saved for review (status: proposed). Use `mementic review` to accept."


@mcp.tool()
async def propose_constraint(
    description: str,
    bound_entities: list[str],
    decision_id: str | None = None,
) -> str:
    """Propose a new constraint for human review.

    Args:
        description: Description of the constraint.
        bound_entities: Code entity paths this constraint applies to.
        decision_id: Optional decision that enforces this constraint.

    Returns:
        Confirmation message with save status.
    """
    graph, cfg = _load_graph()

    constraint = Constraint(
        id=slugify(description),
        description=description,
        bound_entities=bound_entities,
        enforced_by=decision_id or "",
        source=f"agent-proposal:{timestamp()}",
        status="proposed",
        timestamp=timestamp(),
    )

    graph.add_constraint(constraint)

    for entity_path in bound_entities:
        graph.link(constraint.id, entity_path, "binds_to")

    Path(cfg.output.graph_path).parent.mkdir(parents=True, exist_ok=True)
    graph.save(cfg.output.graph_path)
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
