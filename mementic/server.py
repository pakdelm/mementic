"""MCP server exposing Mementic knowledge graph tools."""

from __future__ import annotations

import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mementic.config import MementicConfig, load_config
from mementic.format import (
    DECISION_ID_MAX_LENGTH,
    CONSTRAINT_ID_MAX_LENGTH,
    count_tokens,
    detect_repo_path,
    extract_task_keywords,
    format_context_pack,
    format_evolution,
    resolve_repo_name,
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

_INSTRUCTIONS = """\
Mementic is an architectural memory layer. After EVERY user message (not just \
"before a task"), you MUST call `mementic_get_task_context` with a clear task \
description describing what you are about to do. Be specific about the domain, \
action, and concerns (e.g., "fix authentication bug in login flow" not just \
"fix bug"). Each user message is a new context window — do NOT assume previous \
context is still valid. Context packs are free (SQLite reads, no LLM). "Task" \
means ANY user request — questions, exploration, planning, and informational \
queries are NOT exempt. Output the sufficiency gate before proceeding. Do NOT \
read files or search code without calling this tool first.\
"""

mcp = FastMCP("Mementic", instructions=_INSTRUCTIONS)


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
    cfg = load_config(repo_path=detect_repo_path(check_global_config=True))

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



@mcp.tool()
async def get_task_context(
    task_description: str,
    project: str | None = None,
    max_tokens: int = 2000,
) -> str:
    """Return all relevant Decisions and Constraints for a given task.

    Call this AFTER EVERY USER MESSAGE — not just before starting work.
    Each message is a new context window. Context packs are free (SQLite reads,
    no LLM calls). Do not assume previous context is still valid.

    Args:
        task_description: Description of the task being started.
        project: Optional project name to target.  When ``None``,
            auto-resolution is attempted.
        max_tokens: Maximum token budget for the constraint and decision lines
            (default 2000).

    Returns:
        Formatted markdown with relevant architectural context.
    """
    graph, cfg = _load_graph(project)
    graph.clean_dead_references()
    constraints = graph.get_all_active_constraints()
    decisions = graph.get_all_active_decisions()
    task_keywords = extract_task_keywords(
        task_description,
        top=cfg.retrieval.yake_top_k,
        n=cfg.retrieval.yake_ngram,
    )
    pack_text = format_context_pack(
        task_description, decisions, constraints,
        max_constraint_tokens=max_tokens,
        max_decision_tokens=max_tokens,
        task_keywords=task_keywords,
    )
    pack_tokens = count_tokens(pack_text)
    entry = MetricsEntry(
        timestamp=timestamp(),
        operation="get_task_context",
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

    summary = (
        f"[mementic: get_task_context: "
        f"{len(constraints)} constraints, {len(decisions)} decisions "
        f"for '{task_description}']"
    )
    return f"{summary}\n\n{pack_text}"


@mcp.tool()
async def record_decision(
    title: str,
    rationale: str,
    rejected_alternatives: list[str] | None = None,
    affected_entities: list[str] | None = None,
) -> str:
    """Record a new architectural decision.

    Saved with status 'accepted'.

    Args:
        title: Short title for the decision.
        rationale: Justification for the decision.
        rejected_alternatives: Alternatives that were considered and rejected.
        affected_entities: Code entity paths affected by this decision.

    Returns:
        Confirmation message with save status.
    """
    graph, cfg = _load_graph()
    repo = resolve_repo_name(detect_repo_path(check_global_config=True))
    entity_list = affected_entities or []

    now = timestamp()
    decision = Decision(
        id=slugify(title, max_length=DECISION_ID_MAX_LENGTH),
        title=title,
        rationale=rationale,
        rejected_alternatives=rejected_alternatives or [],
        affected_entities=entity_list,
        source="mcp",
        status=DecisionStatus.ACCEPTED,
        created_at=now,
        modified_at=now,
        repo=repo,
    )

    graph.add_decision(decision)

    for entity_path in entity_list:
        if entity_path == decision.id or not entity_path:
            continue
        # Strip trailing :function / ::function suffixes from entity paths
        clean_path = re.sub(r"::[A-Za-z_]\w*$", "", entity_path)
        clean_path = re.sub(r":[A-Za-z_]\w*$", "", clean_path)
        entity = CodeEntity(id=clean_path, type=EntityType.FILE, repo=repo)
        graph.add_code_entity(entity)
        graph.link(decision.id, clean_path, "affects")

    _save_graph(graph, cfg)
    return f"Decision '{title}' saved (status: accepted)."


@mcp.tool()
async def record_constraint(
    description: str,
    bound_entities: list[str],
    decision_id: str | None = None,
    id: str | None = None,
) -> str:
    """Record a new constraint.

    Saved with status 'active'.

    Args:
        description: Description of the constraint.
        bound_entities: Code entity paths this constraint applies to.
        decision_id: Optional decision that enforces this constraint.
        id: Optional explicit ID for the constraint. If not provided,
            generated from the description (capped at 40 chars).

    Returns:
        Confirmation message with save status.
    """
    graph, cfg = _load_graph()
    repo = resolve_repo_name(detect_repo_path(check_global_config=True))

    constraint_id = (
        slugify(id, max_length=CONSTRAINT_ID_MAX_LENGTH)
        if id
        else slugify(description, max_length=CONSTRAINT_ID_MAX_LENGTH)
    )

    now = timestamp()
    constraint = Constraint(
        id=constraint_id,
        description=description,
        bound_entities=bound_entities,
        enforced_by=decision_id or "",
        source="mcp",
        status="active",
        created_at=now,
        repo=repo,
    )

    graph.add_constraint(constraint)

    for entity_path in bound_entities:
        clean_path = re.sub(r"::[A-Za-z_]\w*$", "", entity_path)
        clean_path = re.sub(r":[A-Za-z_]\w*$", "", clean_path)
        entity = CodeEntity(id=clean_path, type=EntityType.FILE, repo=repo)
        graph.add_code_entity(entity)
        graph.link(constraint.id, clean_path, "binds_to")

    _save_graph(graph, cfg)
    return f"Constraint '{constraint.id}' saved."


@mcp.tool()
async def deprecate_rule(
    id: str,
    reason: str = "",
) -> str:
    """Mark a decision or constraint rule as deprecated.

    Use this to retire outdated architectural decisions or constraints
    that no longer apply.  The entity remains in the graph for history
    but is flagged as deprecated and excluded from context packs.

    Args:
        id: The decision or constraint ID to deprecate.
        reason: Optional reason for deprecation.

    Returns:
        Confirmation message with the deprecated entity's title or description.
    """
    graph, cfg = _load_graph()

    result = graph.deprecate_entity(id, reason)
    _save_graph(graph, cfg)
    return result["message"]


@mcp.tool()
async def review_context(
    feature_description: str,
    deprecated_ids: list[str] | None = None,
) -> str:
    """Review and clean up mementic context after recording decisions/constraints.

    Call this after recording inline proposals during a session. It deprecates
    the given IDs, cleans dead references, and returns a summary of graph state.

    Args:
        feature_description: Description of the feature/task just completed.
        deprecated_ids: Optional list of decision or constraint IDs that were
            deprecated during review.

    Returns:
        Summary line with deprecated count and active entity counts.
    """
    graph, cfg = _load_graph()
    deprecated: list[str] = []
    already: list[str] = []
    not_found: list[str] = []

    for entity_id in (deprecated_ids or []):
        result = graph.deprecate_entity(entity_id)
        if result["status"] == "deprecated":
            deprecated.append(entity_id)
        elif result["status"] == "already_deprecated":
            already.append(entity_id)
        else:
            not_found.append(entity_id)

    _save_graph(graph, cfg)

    graph.clean_dead_references()
    _save_graph(graph, cfg)

    active_constraints = len(graph.get_all_active_constraints())
    active_decisions = len(graph.get_all_active_decisions())

    parts: list[str] = [
        f"[mementic: review_context: '{feature_description}'"
    ]
    if deprecated:
        parts.append(f"deprecated {', '.join(deprecated)}")
    if already:
        parts.append(f"(already deprecated: {', '.join(already)})")
    if not_found:
        parts.append(f"(not found: {', '.join(not_found)})")
    parts.append(f"{active_decisions + active_constraints} active "
                 f"({active_decisions} decisions, {active_constraints} constraints)")

    return " — ".join(parts) + "]"


@mcp.tool()
async def get_entity_history(
    entity_path: str,
    project: str | None = None,
    prefix_match: bool = False,
    max_tokens: int = 1500,
) -> str:
    """Trace all decisions that affected a specific code entity.

    Args:
        entity_path: Path of the code entity to trace.
        project: Optional project name to target.  When ``None``,
            auto-resolution is attempted.
        prefix_match: If True, use prefix matching for entity paths
            instead of exact match.
        max_tokens: Maximum token budget for the compact-formatted lines.
            Defaults to 1000.

    Returns:
        Markdown summary of all decisions affecting the entity.
    """
    graph, cfg = _load_graph(project)
    graph.clean_dead_references()
    decisions = graph.get_evolution(
        entity_path, prefix_match=prefix_match, annotate_overlap=True
    )
    evolution_text = format_evolution(entity_path, decisions, max_tokens=max_tokens)
    summary = (
        f"[mementic: get_entity_history: "
        f"{len(decisions)} decisions touching '{entity_path}']"
    )
    return f"{summary}\n\n{evolution_text}"
