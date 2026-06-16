"""MCP server exposing Mementic knowledge graph tools."""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mementic.config import MementicConfig, load_config
from mementic.format import (
    count_tokens,
    format_context_pack,
    format_evolution,
    slugify,
    timestamp,
)
from mementic.graph import GraphStore
from mementic.metrics import UsageStore
from mementic.models import (
    CodeEntity,
    Constraint,
    Decision,
    DecisionStatus,
    EntityType,
    MetricsEntry,
)

mcp = FastMCP("Mementic")


def _load_graph(project: str | None = None) -> tuple[GraphStore, MementicConfig]:
    """Load the knowledge graph, preferring the project database over legacy JSON.

    Tries to resolve a project (auto-detected or explicit) and use its SQLite
    database.  Falls back to the legacy ``graph.json`` when no project database
    is available.

    Args:
        project: Optional project name for explicit selection.  When ``None``,
            auto-resolution is attempted.

    Returns:
        A tuple of (GraphStore, MementicConfig).
    """
    config_path = os.environ.get("MEMENTIC_CONFIG", ".mementic.json")
    cfg = load_config(config_path)

    # Try project database first
    try:
        from mementic.config import resolve_project

        proj_name, global_cfg = resolve_project(project)
        proj_entry = global_cfg.projects[proj_name]
        db_path = Path(proj_entry.db_path).expanduser()
        if db_path.exists():
            graph = GraphStore(db_path=db_path)
            return graph, cfg
    except (ValueError, KeyError, ImportError):
        pass

    # Fall back to legacy graph.json
    graph_path = cfg.output.graph_path
    if Path(graph_path).exists():
        return GraphStore.load(graph_path), cfg
    return GraphStore(), cfg


def _save_graph(graph: GraphStore, cfg: MementicConfig) -> None:
    """Save the graph — no-op in project mode (auto-persisted to SQLite)."""
    if graph._db_path is not None:
        return
    graph.save(cfg.output.graph_path)


def _repo_from_env() -> str:
    """Detect the current repository from the environment.

    Returns the ``MEMENTIC_REPO`` environment variable, or an empty string
    when not set.
    """
    return os.environ.get("MEMENTIC_REPO", "")


@mcp.tool()
async def get_context_pack(
    task_description: str,
    affected_files: list[str],
    project: str | None = None,
    prefix_match: bool = False,
    max_tokens: int = 1200,
) -> str:
    """Return all relevant Decisions and Constraints for a given task.

    Call this BEFORE starting work on a task.

    Args:
        task_description: Description of the task being started.
        affected_files: List of file paths that will be affected.
        project: Optional project name to target.  When ``None``,
            auto-resolution is attempted.
        prefix_match: If True, use prefix matching for entity paths
            (e.g. directory-level) instead of exact match.
        max_tokens: Maximum token budget for the constraint + decision lines.
            Defaults to 1200.

    Returns:
        Formatted markdown with relevant architectural context.
    """
    graph, cfg = _load_graph(project)
    graph.clean_dead_references()
    constraints = graph.get_constraints_for_entities(
        affected_files, prefix_match=prefix_match, annotate_overlap=True
    )
    decisions = graph.get_decisions_for_entities(
        affected_files, prefix_match=prefix_match, annotate_overlap=True
    )
    pack_text = format_context_pack(
        task_description, decisions, constraints, max_tokens=max_tokens
    )
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

    if graph._conn is not None:
        metrics_store = UsageStore(conn=graph._conn)
        metrics_store.append(entry)

    return pack_text


@mcp.tool()
async def propose_decision(
    title: str,
    rationale: str,
    rejected_alternatives: list[str] | None = None,
    affected_entities: list[str] | None = None,
) -> str:
    """Propose a new architectural decision.

    Will be saved with status 'accepted'.

    Args:
        title: Short title for the decision.
        rationale: Justification for the decision.
        rejected_alternatives: Alternatives that were considered and rejected.
        affected_entities: Code entity paths affected by this decision.

    Returns:
        Confirmation message with save status.
    """
    graph, cfg = _load_graph()
    repo = _repo_from_env()

    now = timestamp()
    decision = Decision(
        id=slugify(title, max_length=80),
        title=title,
        rationale=rationale,
        rejected_alternatives=rejected_alternatives or [],
        source=f"agent-proposal:{now}",
        status=DecisionStatus.ACCEPTED,
        author="opencode",
        created_at=now,
        modified_at=now,
        repo=repo,
    )

    graph.add_decision(decision)

    for entity_path in affected_entities or []:
        if entity_path == decision.id or not entity_path:
            continue
        entity = CodeEntity(id=entity_path, type=EntityType.FILE, repo=repo)
        graph.add_code_entity(entity)
        graph.link(decision.id, entity_path, "affects")

    _save_graph(graph, cfg)
    return (
        f"Decision '{title}' saved (status: accepted)."
    )


@mcp.tool()
async def propose_constraint(
    description: str,
    bound_entities: list[str],
    decision_id: str | None = None,
    id: str | None = None,
) -> str:
    """Propose a new constraint.

    Args:
        description: Description of the constraint.
        bound_entities: Code entity paths this constraint applies to.
        decision_id: Optional decision that enforces this constraint.
        id: Optional explicit ID for the constraint. If not provided,
            generated from the description (capped at 80 chars).

    Returns:
        Confirmation message with save status.
    """
    graph, cfg = _load_graph()
    repo = _repo_from_env()

    constraint_id = id or slugify(description, max_length=30)

    now = timestamp()
    constraint = Constraint(
        id=constraint_id,
        description=description,
        bound_entities=bound_entities,
        enforced_by=decision_id or "",
        source=f"agent-proposal:{now}",
        status="active",
        created_at=now,
        repo=repo,
    )

    graph.add_constraint(constraint)

    for entity_path in bound_entities:
        entity = CodeEntity(id=entity_path, type=EntityType.FILE, repo=repo)
        graph.add_code_entity(entity)
        graph.link(constraint.id, entity_path, "binds_to")

    _save_graph(graph, cfg)
    return f"Constraint '{constraint.id}' saved."


@mcp.tool()
async def get_system_evolution(
    entity_path: str,
    project: str | None = None,
    prefix_match: bool = False,
    max_tokens: int = 1200,
) -> str:
    """Trace all decisions that affected a specific code entity.

    Args:
        entity_path: Path of the code entity to trace.
        project: Optional project name to target.  When ``None``,
            auto-resolution is attempted.
        prefix_match: If True, use prefix matching for entity paths
            instead of exact match.
        max_tokens: Maximum token budget for the compact-formatted lines.
            Defaults to 1200.

    Returns:
        Markdown summary of all decisions affecting the entity.
    """
    graph, cfg = _load_graph(project)
    graph.clean_dead_references()
    decisions = graph.get_evolution(
        entity_path, prefix_match=prefix_match, annotate_overlap=True
    )
    return format_evolution(entity_path, decisions, max_tokens=max_tokens)
