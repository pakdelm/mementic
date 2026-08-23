from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import typer
from rich.markdown import Markdown
from mementic.console import (
    C_DIM,
    C_FAIL,
    C_HEADER,
    C_ID,
    C_PATH,
    C_SUCCESS,
    C_WARN,
    STATUS_DEPRECATED,
    STATUS_FAIL,
    STATUS_OK,
    console,
    kv_table,
    panel,
    rule,
    sanitize,
    table,
    text,
)

from mementic.format import (
    CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
    CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
    DECISION_ID_MAX_LENGTH,
    CONSTRAINT_ID_MAX_LENGTH,
    detect_repo_path,
    format_evolution,
    format_memory_context,
    slugify,
    timestamp,
)
from mementic.graph import GraphStore
from mementic.models import (
    CodeEntity,
    Constraint,
    Decision,
    DecisionStatus,
    EntityType,
)

app = typer.Typer(help="Mementic — semantic memory for engineering teams and AI agents")


def _resolve_graph() -> GraphStore:
    """Open the local knowledge graph at .mementic/graph.db."""
    return GraphStore(db_path=Path(".mementic/graph.db"))


def _interactive() -> bool:
    """Return True when a human is driving the terminal."""
    return sys.stdin.isatty()


# Minimum word-set overlap (Jaccard) for two entries to count as the same
# content. Below this threshold a same-slug entry is treated as new content.
_DUPLICATE_OVERLAP_THRESHOLD: float = 0.8


def _word_overlap(text_a: str, text_b: str) -> float:
    """Compute word overlap ratio between two texts.

    Returns a float between 0.0 and 1.0, where 1.0 means identical word sets.
    Uses set intersection over union of lowercase words.
    """
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a and not words_b:
        return 1.0
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def _auto_resolve(
    graph: GraphStore,
    entity_id: str,
    content_field: str,
    content: str,
) -> str:
    """Apply deterministic auto-resolve rules against an existing entry.

    Four outcomes, zero inference:
    1. New slug (no existing node with this ID) → ``"accepted"``.
    2. Same slug + ≥80% word overlap with the existing entry's
       ``content_field`` → ``"skipped"`` (duplicate).
    3. Same slug + different content → deprecate the existing entry via
       :meth:`GraphStore.deprecate_entity` and return ``"deprecated"``
       (the caller then adds the new entry).
    4. Existing entry is already deprecated (tombstone) → ``"replaced"``
       without deprecating again; the caller overwrites it with the new
       content. Deprecated entries never block re-adding.

    Args:
        graph: The current knowledge graph.
        entity_id: Slug ID of the incoming entry.
        content_field: Payload field holding comparable content
            (``"rationale"`` for decisions, ``"description"`` for constraints).
        content: The incoming entry's content text.

    Returns:
        One of ``"accepted"``, ``"skipped"``, ``"deprecated"``, or
        ``"replaced"``.
    """
    if not graph.has_node(entity_id):
        return "accepted"
    payload = graph.get_node_payload(entity_id)
    if str(payload.get("status", "")) == "deprecated":
        # Tombstone: re-adding replaces it; no duplicate skip, no re-deprecation.
        return "replaced"
    if content_field not in payload:
        # Cross-type ID collision — no comparable entry, treat as new.
        return "accepted"
    overlap = _word_overlap(content, payload.get(content_field, ""))
    if overlap >= _DUPLICATE_OVERLAP_THRESHOLD:
        return "skipped"
    graph.deprecate_entity(entity_id, reason="superseded by re-added entry")
    return "deprecated"


def _counts_panel_text(counts: dict[str, int]) -> str:
    """Format accepted/skipped/deprecated counts as a single dim summary line."""
    return (
        f"[{C_DIM}]Counts: {counts['accepted']} accepted, "
        f"{counts['skipped']} skipped, {counts['deprecated']} deprecated[/]"
    )


def _accepted_status_label(disposition: str, noun: str) -> str:
    """Build the status line for an accepted add.

    Args:
        disposition: ``"accepted"``, ``"deprecated"``, or ``"replaced"``.
        noun: Entry kind used in the label text (e.g. ``"constraint"``).

    Returns:
        Rich markup for the status field of the add confirmation panel.
    """
    state = "active" if noun == "constraint" else "accepted"
    if disposition == "accepted":
        return f"[green]{state}[/]"
    if disposition == "replaced":
        return f"[yellow]{state} (replaced deprecated entry)[/]"
    return f"[yellow]{state} (previous entry deprecated)[/]"


@app.command()
def init() -> None:
    """Initialize mementic for this repository.

    Creates ``.mementic/graph.db`` as an empty knowledge graph when missing
    (idempotent — running it again is a no-op) and injects enforcement
    instructions into AGENTS.md when absent.
    """
    cwd = Path(detect_repo_path())
    db_path = Path(".mementic/graph.db")

    created = False
    if not db_path.exists():
        graph = GraphStore(db_path=db_path)
        graph.close()
        created = True

    # --- Inject enforcement template ---
    from mementic.inject import detect_agents, has_enforcement, inject_enforcement

    agents_md_path = cwd / "AGENTS.md"
    injection_note = ""
    if not has_enforcement(agents_md_path):
        detected = detect_agents(cwd)
        if _interactive():
            # Interactive human run: always ask before touching AGENTS.md.
            console.print()
            inject_ok = typer.confirm(
                "No enforcement instructions found. Add to AGENTS.md?",
                default=True,
            )
            if inject_ok:
                injection_note = inject_enforcement(agents_md_path)
                console.print(text(f"  [{C_SUCCESS}]{injection_note}[/]"))
            else:
                console.print(
                    text(
                        f"  [{C_WARN}]Warning: Without enforcement, "
                        f"mementic operates as CLI-only.[/]\n"
                        f"  [{C_DIM}]Agents won't automatically query "
                        f"the graph. Re-run 'mementic init' to retry.[/]"
                    )
                )
        elif detected:
            # Non-interactive run in a repo with agent markers (agent-driven
            # bootstrap): inject so the harness picks up enforcement.
            injection_note = inject_enforcement(agents_md_path)
        else:
            console.print(
                text(
                    f"  [{C_DIM}]No enforcement instructions found. "
                    f"Re-run 'mementic init' interactively to add them.[/]"
                )
            )

    graph_state = "created" if created else "already exists"
    enforcement_state = (
        injection_note
        if injection_note
        else "Enforcement instructions already present in AGENTS.md."
    )
    console.print(
        panel(
            text(
                f"[{C_HEADER} bold]Graph[/]: "
                f"[{C_PATH}].mementic/graph.db[/] [{C_DIM}]({graph_state})[/]"
            ),
            rule(),
            text(enforcement_state),
            rule(),
            text(
                f"[{C_DIM} italic]Next: agents record rules inline via "
                f"'mementic add decision|constraint', then query with "
                f"'mementic query keywords <kw1> <kw2> ...'[/]"
            ),
            title="Mementic initialized",
        )
    )


query_app = typer.Typer(help="Search and retrieve from the knowledge graph")
app.add_typer(query_app, name="query")


@query_app.command()
def keywords(
    keywords: list[str] = typer.Argument(
        ..., help="Keywords for matching (e.g. auth login session pydantic)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Return ranked context pack of constraints and decisions matched by keywords."""
    graph = _resolve_graph()
    graph.clean_dead_references()
    constraints = graph.get_all_active_constraints()
    decisions = graph.get_all_active_decisions()

    if json_output:
        from mementic.format import format_context_pack_json
        result = format_context_pack_json(
            decisions, constraints,
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
            keywords=keywords,
        )
        typer.echo(json.dumps(result, indent=2, default=str))
        return

    output = format_memory_context(
        decisions, constraints,
        max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
        max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
        keywords=keywords,
    )
    kw_list = ", ".join(keywords)
    # Agent-enforcement contract: a plain-text summary line agents must echo.
    typer.echo(
        f"[mementic: {len(constraints)} constraints, {len(decisions)} decisions "
        f"matched ({kw_list})]"
    )
    summary = text(
        f"[{C_DIM}]{len(constraints)} constraints, "
        f"{len(decisions)} decisions[/]"
    )
    console.print(
        panel(
            summary,
            rule(),
            Markdown(sanitize(output)),
            title=f"Context pack: {kw_list}",
        )
    )


@query_app.command()
def file(
    path: str = typer.Argument(..., help="File path to trace evolution for"),
    prefix: bool = typer.Option(False, "--prefix", help="Use prefix matching for entity paths"),
    max_tokens: int = typer.Option(1500, "--max-tokens", help="Maximum token budget for output"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show constraints and decisions affecting a specific file."""
    graph = _resolve_graph()
    graph.clean_dead_references()
    constraints = graph.get_constraints_for_entities([path], prefix_match=prefix)
    decisions = graph.get_evolution(path, prefix_match=prefix, annotate_overlap=True)

    if json_output:
        from mementic.format import format_file_history_json
        result = format_file_history_json(path, decisions, constraints)
        typer.echo(json.dumps(result, indent=2, default=str))
        return

    evolution_text = format_evolution(path, decisions, max_tokens=max_tokens)
    if constraints:
        evolution_text += "\n\n### Constraints\n"
        for c in constraints:
            entities = ", ".join(c.bound_entities) if c.bound_entities else "none"
            evolution_text += f"- {c.description} (#{c.id}, bound: {entities})\n"
    # Agent-enforcement contract: a plain-text summary line agents must echo.
    typer.echo(
        f"[mementic: {len(constraints)} constraints, {len(decisions)} decisions "
        f"matched ({path})]"
    )
    console.print(
        panel(Markdown(sanitize(evolution_text)), title=f"File history: {path}")
    )


@query_app.command()
def search(
    term: str = typer.Argument(..., help="Search term for titles, rationales, and descriptions"),
) -> None:
    """Full-text search across decisions and constraints."""
    graph = _resolve_graph()
    all_d = graph.get_all_decisions()
    all_c = graph.get_all_constraints()
    t = term.lower()
    matching_d = [d for d in all_d if t in d.title.lower() or t in d.rationale.lower()]
    matching_c = [c for c in all_c if t in c.description.lower()]

    panel_lines: list = []
    if matching_d:
        decisions_table = table(show_header=False, show_edge=False, padding=(0, 1))
        decisions_table.add_column("ID", style=C_ID)
        decisions_table.add_column("Title")
        for d in matching_d:
            decisions_table.add_row(d.id, d.title)
        panel_lines.append(
            text(f"[{C_HEADER} bold]Decisions ({len(matching_d)})[/]")
        )
        panel_lines.append(decisions_table)
    if matching_c:
        if panel_lines:
            panel_lines.append(text(""))
        constraints_table = table(show_header=False, show_edge=False, padding=(0, 1))
        constraints_table.add_column("ID", style=C_ID)
        constraints_table.add_column("Description")
        for c in matching_c:
            constraints_table.add_row(c.id, c.description)
        panel_lines.append(
            text(f"[{C_HEADER} bold]Constraints ({len(matching_c)})[/]")
        )
        panel_lines.append(constraints_table)
    # Agent-enforcement contract: a plain-text summary line agents must echo.
    typer.echo(
        f"[mementic: {len(matching_c)} constraints, {len(matching_d)} decisions "
        f'matched ("{term}")]'
    )
    console.print(
        panel(*panel_lines, title=f'Search: "{term}"')
    )


@query_app.command()
def stats(
) -> None:
    """Show graph statistics."""
    graph = _resolve_graph()
    s = graph.stats()
    stats_table = kv_table()
    stats_table.add_column("Metric", style="bold", width=20)
    stats_table.add_column("Value", justify="right")
    for k, v in s.items():
        stats_table.add_row(k.title(), str(v))
    console.print(panel(stats_table, title="Graph statistics"))


@app.command()
def log(
    n: int = typer.Option(
        20, "--n", "-n", help="Number of log entries to show",
    ),
) -> None:
    """Show recent graph mutations from the audit log."""
    graph = _resolve_graph()

    rows = graph.get_audit_entries(limit=n)
    if rows:
        audit_table = table()
        audit_table.add_column("Timestamp", style=C_DIM)
        audit_table.add_column("Action")
        audit_table.add_column("Rule ID", style=C_ID)
        audit_table.add_column("Summary")
        for row in rows:
            entity_type = row["entity_type"]
            action = row["action"]
            entity_id = row["entity_id"]
            timestamp_val = row["timestamp"]
            details_str: str = row["details"]
            try:
                details = json.loads(details_str)
            except (json.JSONDecodeError, TypeError):
                details = details_str
            if isinstance(details, dict) and "title" in details:
                summary = details["title"]
            elif isinstance(details, dict) and "description" in details:
                summary = details["description"]
            elif isinstance(details, dict) and "relation" in details:
                summary = (
                    f"{details.get('source', '')} -> "
                    f"{details.get('target', '')} ({details['relation']})"
                )
            elif isinstance(details, str):
                summary = details
            else:
                summary = "-"
            action_style = {
                "add": C_SUCCESS,
                "remove": C_FAIL,
                "deprecate": C_WARN,
                "update": C_SUCCESS,
            }.get(action, "")
            action_text = (
                f"[{action_style}]{entity_type}/{action}[/]"
                if action_style
                else f"{entity_type}/{action}"
            )
            audit_table.add_row(
                str(timestamp_val),
                action_text,
                entity_id,
                summary,
            )
        console.print(
            panel(audit_table, title=f"Audit log (last {n})")
        )
        return

    # Fallback: no SQLite project database attached — try legacy JSONL
    audit_path = Path(".mementic/audit.jsonl")
    if not audit_path.exists():
        typer.echo("No audit log found. Run init or add first.")
        raise typer.Exit()

    lines = [line for line in audit_path.read_text().strip().split("\n") if line.strip()]
    if not lines:
        typer.echo("Audit log is empty.")
        raise typer.Exit()
    for line in lines[-n:]:
        entry = json.loads(line)
        typer.echo(f"[{entry['timestamp']}] {entry['action']}: {entry.get('id', '-')}")
    console.print(
        panel(
            text(
                f"[{C_DIM}]No SQLite audit log found. "
                f"Read {len(lines)} legacy entries.[/]"
            ),
            title="Audit log",
        )
    )


@app.command()
def status(
) -> None:
    """Show health checklist for mementic."""
    from mementic.health import check_health

    try:
        graph = _resolve_graph()
    except (sqlite3.Error, OSError, ValueError):
        # Health command must degrade gracefully, not traceback on a
        # corrupt/locked/missing graph — check_health reports the failure.
        graph = None

    try:
        repo = Path(detect_repo_path())
    except (ValueError, OSError):
        repo = Path.cwd()
    items = check_health(graph, repo_path=repo)
    health_table = table(show_header=False, show_edge=False, padding=(0, 1))
    health_table.add_column("Status", width=7)
    health_table.add_column("Check", style="bold")
    health_table.add_column("Hint", style=C_DIM)
    ok_count = 0
    for item in items:
        if item.status == "ok":
            health_table.add_row(
                f"[{C_SUCCESS}]{STATUS_OK}[/]", item.label, item.hint or ""
            )
            ok_count += 1
        else:
            health_table.add_row(
                f"[{C_FAIL}]{STATUS_FAIL}[/]", item.label, item.hint or ""
            )
    console.print(
        panel(
            health_table,
            rule(),
            text(f"[{C_DIM}]{ok_count}/{len(items)} checks passed[/]"),
            title="Status",
        )
    )


def _status_icon(status: str) -> str:
    """Return a compact status marker for list output."""
    icons = {
        "accepted": STATUS_OK,
        "deprecated": STATUS_DEPRECATED,
        "rejected": STATUS_FAIL,
        "active": STATUS_OK,
    }
    return icons.get(status, " ")


list_app = typer.Typer(help="Browse decisions, constraints, and entities")
app.add_typer(list_app, name="list")

_VALID_STATUSES = {"accepted", "deprecated", "rejected", "active"}


def _validate_status(status: str | None) -> None:
    """Validate --status flag, exit on invalid value."""
    if status and status not in _VALID_STATUSES:
        typer.echo(
            f"Invalid status '{status}'. "
            f"Valid: {', '.join(sorted(_VALID_STATUSES))}",
            err=True,
        )
        raise typer.Exit(code=1)


@list_app.command()
def decisions(
    n: int = typer.Option(20, "-n", help="Show only the last N entries"),
    status: str | None = typer.Option(
        None, "--status",
        help="Filter by status: accepted, deprecated, rejected, active",
    ),
) -> None:
    """List architectural decisions in a scannable table."""
    graph = _resolve_graph()
    _validate_status(status)

    all_d = graph.get_all_decisions()
    if status:
        all_d = [d for d in all_d if d.status == status]
    if n > 0:
        all_d = all_d[-n:]

    if not all_d:
        typer.echo("No decisions found.")
        raise typer.Exit()

    decision_table = table()
    decision_table.add_column("", width=3)  # status icon
    decision_table.add_column("ID", style=C_ID)
    decision_table.add_column("Date", style=C_DIM)
    decision_table.add_column("Title", overflow="fold")
    for d in all_d:
        icon = _status_icon(d.status)
        color_map = {
            STATUS_OK: C_SUCCESS,
            STATUS_DEPRECATED: C_WARN,
            STATUS_FAIL: C_FAIL,
        }
        color = color_map.get(icon, "")
        date = d.created_at[:10] if d.created_at else ""
        decision_table.add_row(f"[{color}]{icon}[/]", d.id, date, d.title)
    console.print(
        panel(decision_table, title=f"Decisions ({len(all_d)})")
    )


@list_app.command()
def constraints(
    n: int = typer.Option(20, "-n", help="Show only the last N entries"),
    status: str | None = typer.Option(
        None, "--status",
        help="Filter by status: accepted, deprecated, rejected, active",
    ),
) -> None:
    """List constraints in a scannable table."""
    graph = _resolve_graph()
    _validate_status(status)

    all_c = graph.get_all_constraints()
    if status:
        all_c = [c for c in all_c if c.status == status]
    if n > 0:
        all_c = all_c[-n:]

    if not all_c:
        typer.echo("No constraints found.")
        raise typer.Exit()

    constraint_table = table()
    constraint_table.add_column("", width=3)  # status icon
    constraint_table.add_column("ID", style=C_ID)
    constraint_table.add_column("Enforced By", style=C_DIM)
    constraint_table.add_column("Description", overflow="fold")
    for c in all_c:
        icon = _status_icon(c.status)
        color_map = {
            STATUS_OK: C_SUCCESS,
            STATUS_DEPRECATED: C_WARN,
            STATUS_FAIL: C_FAIL,
        }
        color = color_map.get(icon, "")
        enforced = f"via {c.enforced_by}" if c.enforced_by else ""
        constraint_table.add_row(
            f"[{color}]{icon}[/]", c.id, enforced, c.description
        )
    console.print(
        panel(constraint_table, title=f"Constraints ({len(all_c)})")
    )


@list_app.command()
def entities(
    n: int = typer.Option(20, "-n", help="Show only the last N entries"),
    stale: bool = typer.Option(
        False, "--stale",
        help="Show only entities referencing files that no longer exist",
    ),
) -> None:
    """List code entities in a scannable table."""
    graph = _resolve_graph()

    all_e: list[tuple[str, dict[str, Any]]] = []
    for nid, ntype, payload in graph.iter_nodes():
        if ntype == "CodeEntity":
            all_e.append((nid, payload))

    if stale:
        all_e = [
            (nid, p) for nid, p in all_e
            if p.get("type") == "file" and not Path(nid).exists()
        ]
    if n > 0:
        all_e = all_e[-n:]

    if not all_e:
        typer.echo("No entities found.")
        raise typer.Exit()

    entity_table = table()
    entity_table.add_column("Path", style=C_ID, overflow="fold")
    entity_table.add_column("Type")
    entity_table.add_column("Repo", style=C_DIM)
    for eid, p in all_e:
        etype = p.get("type", "file")
        repo = p.get("repo", "-")
        entity_table.add_row(eid, etype, repo)
    console.print(
        panel(entity_table, title=f"Entities ({len(all_e)})")
    )


@app.command()
def deprecate(
    ids: list[str] = typer.Argument(..., help="Decision or constraint IDs to deprecate"),
    reason: str = typer.Option(
        "", "--reason", "-r",
        help="Reason for deprecation",
    ),
) -> None:
    """Mark one or more decisions or constraints as deprecated.

    Appends a deprecation note and writes audit entries.
    Deprecated entities remain in the graph for history but are
    excluded from mementic memory.
    """
    graph = _resolve_graph()

    deprecated = 0
    already = 0
    errors = 0

    for id_ in ids:
        result = graph.deprecate_entity(id_, reason)

        if result["status"] == "deprecated":
            deprecated += 1
        elif result["status"] == "already_deprecated":
            already += 1
        elif result["status"] == "wrong_type":
            typer.echo(result["message"], err=True)
            errors += 1
        else:
            typer.echo(result["message"], err=True)
            errors += 1

    parts: list[str] = []
    if deprecated:
        parts.append(f"[{C_SUCCESS}]{deprecated} deprecated[/]")
    if already:
        parts.append(f"[{C_WARN}]{already} already deprecated[/]")
    if errors:
        parts.append(f"[{C_FAIL}]{errors} errors[/]")
    if reason:
        parts.append(f"[{C_DIM}]reason: {reason}[/]")

    console.print(
        panel(
            text(
                ", ".join(parts)
                + f"\n[{C_DIM}]IDs: {', '.join(ids)}[/]"
            ),
            title="Deprecated",
        )
    )

    if errors:
        raise typer.Exit(code=1)


add_app = typer.Typer(help="Add decisions and constraints to the graph")
app.add_typer(add_app, name="add")


@add_app.command()
def decision(
    title: str = typer.Argument(..., help="Short title for the decision"),
    rationale: str = typer.Option(
        ..., "--rationale", "-r", help="Justification for the decision"
    ),
    entities: list[str] | None = typer.Option(
        None, "--entity", "-e", help="Affected file paths (repeatable)"
    ),
    rejected: list[str] | None = typer.Option(
        None, "--rejected", help="Rejected alternatives (repeatable)"
    ),
) -> None:
    """Record a new architectural decision.

    Auto-resolves against an existing entry with the same slug:
    new slug → accept; ≥80% word overlap → skip as duplicate;
    different content → deprecate the existing entry and accept the new one.
    """
    graph = _resolve_graph()
    now = timestamp()
    d = Decision(
        id=slugify(title, max_length=DECISION_ID_MAX_LENGTH),
        title=title,
        rationale=rationale,
        rejected_alternatives=rejected or [],
        affected_entities=entities or [],
        source="cli",
        status=DecisionStatus.ACCEPTED,
        created_at=now,
        modified_at=now,
        repo=str(Path(detect_repo_path()).resolve().name),
    )

    counts: dict[str, int] = {"accepted": 0, "skipped": 0, "deprecated": 0}
    disposition = _auto_resolve(graph, d.id, "rationale", rationale)
    if disposition == "skipped":
        counts["skipped"] += 1
        console.print(
            panel(
                text(
                    f"Title:  [{C_HEADER}]{title}[/]\n"
                    f"ID:     [{C_ID}]{d.id}[/]\n"
                    f"Status: [{C_WARN}]skipped (duplicate of existing entry)[/]"
                ),
                _counts_panel_text(counts),
                title="Decision skipped",
            )
        )
        return

    counts["deprecated" if disposition == "deprecated" else "accepted"] += 1
    try:
        graph.add_decision(d)
    except ValueError as exc:
        console.print(
            panel(text(f"[{C_FAIL}]Error: {exc}[/]"), title="Decision not added")
        )
        raise typer.Exit(code=1) from exc
    for ep in (entities or []):
        clean = re.sub(r"::[A-Za-z_]\w*$", "", ep)
        clean = re.sub(r":[A-Za-z_]\w*$", "", clean)
        e = CodeEntity(id=clean, type=EntityType.FILE, repo=d.repo)
        graph.add_code_entity(e)
        graph.link(d.id, clean, "affects")
    status_label = _accepted_status_label(disposition, noun="decision")
    console.print(
        panel(
            text(
                f"Title:  [{C_HEADER}]{title}[/]\n"
                f"ID:     [{C_ID}]{d.id}[/]\n"
                f"Status: {status_label}"
            ),
            _counts_panel_text(counts),
            title="Decision added",
        )
    )


@add_app.command()
def constraint(
    description: str = typer.Argument(..., help="Constraint description"),
    entities: list[str] = typer.Option(
        ..., "--entity", "-e", help="Bound entity paths (repeatable, required)"
    ),
    decision_id: str | None = typer.Option(
        None, "--decision-id", "-d", help="Enforcing decision ID"
    ),
    rule_id: str | None = typer.Option(
        None, "--id", help="Explicit rule ID (auto-generated if omitted)"
    ),
) -> None:
    """Record a new constraint.

    Auto-resolves against an existing entry with the same slug:
    new slug → accept; ≥80% word overlap → skip as duplicate;
    different content → deprecate the existing entry and accept the new one.
    """
    graph = _resolve_graph()
    now = timestamp()
    cid = (
        slugify(rule_id, max_length=CONSTRAINT_ID_MAX_LENGTH)
        if rule_id
        else slugify(description, max_length=CONSTRAINT_ID_MAX_LENGTH)
    )
    c = Constraint(
        id=cid,
        description=description,
        bound_entities=entities,
        enforced_by=decision_id or "",
        source="cli",
        status="active",
        created_at=now,
        repo=str(Path(detect_repo_path()).resolve().name),
    )

    counts: dict[str, int] = {"accepted": 0, "skipped": 0, "deprecated": 0}
    disposition = _auto_resolve(graph, c.id, "description", description)
    if disposition == "skipped":
        counts["skipped"] += 1
        console.print(
            panel(
                text(
                    f"ID:       [{C_ID}]{c.id}[/]\n"
                    f"Desc:     [{C_DIM}]{description}[/]\n"
                    f"Status:   [{C_WARN}]skipped (duplicate of existing entry)[/]"
                ),
                _counts_panel_text(counts),
                title="Constraint skipped",
            )
        )
        return

    counts["deprecated" if disposition == "deprecated" else "accepted"] += 1
    try:
        graph.add_constraint(c)
    except ValueError as exc:
        console.print(
            panel(text(f"[{C_FAIL}]Error: {exc}[/]"), title="Constraint not added")
        )
        raise typer.Exit(code=1) from exc
    for ep in entities:
        clean = re.sub(r"::[A-Za-z_]\w*$", "", ep)
        clean = re.sub(r":[A-Za-z_]\w*$", "", clean)
        e = CodeEntity(id=clean, type=EntityType.FILE, repo=c.repo)
        graph.add_code_entity(e)
        graph.link(c.id, clean, "binds_to")
    bound_list = ", ".join(entities[:5])
    if len(entities) > 5:
        bound_list += f" +{len(entities) - 5} more"
    status_label = _accepted_status_label(disposition, noun="constraint")
    console.print(
        panel(
            text(
                f"ID:       [{C_ID}]{c.id}[/]\n"
                f"Bound to: [{C_PATH}]{bound_list}[/]\n"
                f"Desc:     [{C_DIM}]{description}[/]\n"
                f"Status:   {status_label}"
            ),
            _counts_panel_text(counts),
            title="Constraint added",
        )
    )


if __name__ == "__main__":
    app()
