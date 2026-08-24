from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

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
    DECISION_ID_MAX_LENGTH,
    CONSTRAINT_ID_MAX_LENGTH,
    build_context_pack,
    detect_repo_path,
    format_evolution,
    slugify,
    split_pack_budget,
    timestamp,
)
from mementic.store import MementicStore, successor_timestamp
from mementic.models import Constraint, Decision

app = typer.Typer(help="Mementic — semantic memory for engineering teams and AI agents")


def _resolve_store() -> MementicStore:
    """Open the local memory store at .mementic/graph.db.

    Exits cleanly when the database exists but has a foreign schema version
    (e.g. v4) so agents see the migration hint instead of a traceback.
    """
    try:
        return MementicStore(db_path=Path(".mementic/graph.db"))
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


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


def _clean_entity_path(raw: str) -> str:
    """Strip trailing ``::symbol`` / ``:symbol`` qualifiers from a path."""
    clean = re.sub(r"::[A-Za-z_]\w*$", "", raw)
    clean = re.sub(r":[A-Za-z_]\w*$", "", clean)
    return clean


def _decision(store: MementicStore, entry_id: str) -> Decision | None:
    """Fetch a decision by ID, or None when the ID is not a decision."""
    return store.get_decision(entry_id)


def _constraint(store: MementicStore, entry_id: str) -> Constraint | None:
    """Fetch a constraint by ID, or None when the ID is not a constraint."""
    return store.get_constraint(entry_id)


def _auto_resolve(
    store: MementicStore,
    entry_type: str,
    entity_id: str,
    content: str,
) -> tuple[str, str | None]:
    """Apply deterministic auto-resolve rules against an existing entry.

    Four outcomes, zero inference:
    1. New slug (no existing entry with this ID) → ``"accepted"``.
    2. Same slug + ≥80% word overlap with the existing entry's content
       → ``"skipped"`` (duplicate).
    3. Same slug + different content → deprecate the existing entry via
       :meth:`MementicStore.deprecate` (tombstone row with ``replaced_by``
       pointing at the incoming slug) and return ``"deprecated"`` (the
       caller then adds the new entry).
    4. Existing entry is already deprecated (tombstone) → ``"replaced"``
       without deprecating again; the caller overwrites it with the new
       content. Deprecated entries never block re-adding.

    Args:
        store: The current memory store.
        entry_type: ``"decision"`` or ``"constraint"``.
        entity_id: Slug ID of the incoming entry.
        content: The incoming entry's content text (rationale/description).

    Returns:
        ``(disposition, tombstone_at)`` where disposition is one of
        ``"accepted"``, ``"skipped"``, ``"deprecated"``, or ``"replaced"``,
        and ``tombstone_at`` is the ISO 8601 stamp of the tombstone written
        by this call (None when none was). Callers must derive a successor
        incarnation's ``created_at`` from ``tombstone_at`` via
        :func:`mementic.store.successor_timestamp` so the new incarnation
        structurally postdates the tombstone even when consecutive
        wall-clock reads collide.
    """
    existing_type = store.get_entry_type(entity_id)
    if existing_type is None:
        return "accepted", None
    if existing_type != entry_type:
        # Cross-type collision: let the store raise on add so the CLI can
        # report a clean error instead of comparing incompatible content.
        return "accepted", None
    existing = (
        _decision(store, entity_id)
        if entry_type == "decision"
        else _constraint(store, entity_id)
    )
    if existing is None:  # pragma: no cover - get_entry_type just found it
        raise RuntimeError(f"entry {entity_id} vanished during auto-resolve")
    if store.is_deprecated(entry_type, entity_id):
        # Tombstone: re-adding replaces it; no duplicate skip, no
        # re-deprecation. Record the succession so the prior tombstone
        # gains a replaced_by pointer (stamped before the new incarnation).
        stamp = store.record_succession(
            entry_type,
            entity_id,
            replaced_by=entity_id,
            reason="superseded by re-added entry",
        )
        return "replaced", stamp
    stored_content = (
        existing.rationale
        if isinstance(existing, Decision)
        else existing.description
    )
    if _word_overlap(content, stored_content) >= _DUPLICATE_OVERLAP_THRESHOLD:
        return "skipped", None
    result = store.deprecate(
        entry_type,
        entity_id,
        reason="superseded by re-added entry",
        replaced_by=entity_id,
    )
    return "deprecated", result["at"]


def _successor_created_at(tombstone_at: str | None) -> str:
    """Stamp a fresh incarnation: one microsecond after its tombstone.

    When no tombstone was written this add, fall back to the current wall
    clock. Otherwise build the stamp structurally from the tombstone so
    identical successive clock reads can never strand a dead incarnation.
    """
    if tombstone_at is None:
        return timestamp()
    return successor_timestamp(tombstone_at)


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

    Creates ``.mementic/graph.db`` as an empty memory store when missing
    (idempotent — running it again is a no-op) and injects enforcement
    instructions into AGENTS.md when absent.
    """
    cwd = Path(detect_repo_path())
    db_path = Path(".mementic/graph.db")

    created = False
    if not db_path.exists():
        store = MementicStore(db_path=db_path)
        store.close()
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


def _split_token_budget(
    total: int | None,
) -> tuple[int, int]:
    """Split a total token budget across the two pack sections.

    Thin delegate to :func:`mementic.format.split_pack_budget`, the
    canonical formula (floor on the constraint share, remainder to the
    decisions section) shared with the probe harness so neither can drift.

    Args:
        total: Combined token budget, or ``None`` for the defaults.

    Returns:
        ``(constraint_tokens, decision_tokens)``.
    """
    return split_pack_budget(total)


@query_app.command()
def keywords(
    keywords: list[str] = typer.Argument(
        ..., help="Keywords for matching (e.g. auth login session pydantic)"
    ),
    max_tokens: int | None = typer.Option(
        None,
        "--max-tokens",
        help="Total token budget for the pack; overrides both section "
        "budgets proportionally",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Return ranked context pack of constraints and decisions matched by keywords."""
    if max_tokens is not None and max_tokens < 1:
        typer.echo(
            f"Error: --max-tokens must be at least 1 (got {max_tokens}). "
            "Pass a positive total token budget, or omit the flag to use "
            "the Spec 30 defaults.",
            err=True,
        )
        raise typer.Exit(code=2)
    store = _resolve_store()
    # Derived liveness: tombstoned entries are excluded at the SQL layer.
    constraints = store.get_live_constraints()
    decisions = store.get_live_decisions()
    max_constraint_tokens, max_decision_tokens = _split_token_budget(max_tokens)
    # Dead-path checks must resolve repo-relative bound paths against the
    # stable repo root (graph.db's parent's parent), not the process CWD,
    # which varies with where the CLI was invoked (Spec 30 review).
    base_dir = Path(".mementic/graph.db").parent.parent

    if json_output:
        from mementic.format import format_context_pack_json
        result_json = format_context_pack_json(
            decisions, constraints,
            max_constraint_tokens=max_constraint_tokens,
            max_decision_tokens=max_decision_tokens,
            keywords=keywords,
            base_dir=base_dir,
        )
        typer.echo(json.dumps(result_json, indent=2, default=str))
        return

    result = build_context_pack(
        decisions, constraints,
        max_constraint_tokens=max_constraint_tokens,
        max_decision_tokens=max_decision_tokens,
        keywords=keywords,
        base_dir=base_dir,
    )
    stats = result.stats
    kw_list = ", ".join(keywords)
    # Agent-enforcement contract: a plain-text summary line agents must echo.
    typer.echo(
        f"[mementic: {stats.shown_constraints} of {stats.matched_constraints} constraints, "
        f"{stats.shown_decisions} of {stats.matched_decisions} decisions "
        f"matched ({kw_list}) shown]"
    )
    summary = text(
        f"[{C_DIM}]{stats.shown_constraints} of {stats.matched_constraints} constraints, "
        f"{stats.shown_decisions} of {stats.matched_decisions} decisions[/]"
    )
    console.print(
        panel(
            summary,
            rule(),
            Markdown(sanitize(result.text)),
            title=f"Context pack: {kw_list}",
        )
    )


def _path_exists(path: str) -> bool:
    """Return True when a stored entity path still exists on disk."""
    return Path(path).exists()


@query_app.command()
def file(
    path: str = typer.Argument(..., help="File path to trace evolution for"),
    prefix: bool = typer.Option(False, "--prefix", help="Use prefix matching for entity paths"),
    max_tokens: int = typer.Option(1500, "--max-tokens", help="Maximum token budget for output"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show constraints and decisions affecting a specific file."""
    store = _resolve_store()
    # Path-overlap and filesystem-liveness semantics live in the store;
    # overlap counts surviving matched paths.
    constraints = store.get_constraints_for_path(
        path, prefix=prefix, path_exists=_path_exists,
    )
    decisions = store.get_decisions_for_path(
        path, prefix=prefix, path_exists=_path_exists,
    )

    if json_output:
        from mementic.format import format_file_history_json
        result = format_file_history_json(path, decisions, constraints)
        typer.echo(json.dumps(result, indent=2, default=str))
        return

    evolution_text = format_evolution(path, decisions, max_tokens=max_tokens)
    if constraints:
        evolution_text += "\n\n### Constraints\n"
        for c in constraints:
            entities = ", ".join(c.paths) if c.paths else "none"
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
    store = _resolve_store()
    all_d = store.get_all_decisions()
    all_c = store.get_all_constraints()
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
    store = _resolve_store()
    s = store.stats()
    stats_table = kv_table()
    stats_table.add_column("Metric", style="bold", width=20)
    stats_table.add_column("Value", justify="right")
    labels = {
        "decisions": "Decisions",
        "constraints": "Constraints",
        "paths": "Entry paths",
        "deprecated": "Deprecated",
    }
    for k, v in s.items():
        stats_table.add_row(labels.get(k, k.title()), str(v))
    console.print(panel(stats_table, title="Graph statistics"))


@app.command()
def log(
    n: int = typer.Option(
        20, "--n", "-n", help="Number of log entries to show",
    ),
) -> None:
    """Show recent graph mutations from the audit log."""
    store = _resolve_store()

    rows = store.get_audit_entries(limit=n)
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
        store = MementicStore(db_path=Path(".mementic/graph.db"))
    except (sqlite3.Error, OSError, ValueError, RuntimeError):
        # Health command must degrade gracefully, not traceback on a
        # corrupt/locked/missing/mis-versioned store — check_health
        # reports the failure.
        store = None

    try:
        repo = Path(detect_repo_path())
    except (ValueError, OSError):
        repo = Path.cwd()
    items = check_health(store, repo_path=repo)
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


list_app = typer.Typer(help="Browse decisions, constraints, and entities")
app.add_typer(list_app, name="list")

_VALID_STATUSES = {"accepted", "deprecated", "active"}


def _validate_status(status: str | None) -> None:
    """Validate --status flag, exit on invalid value."""
    if status and status not in _VALID_STATUSES:
        typer.echo(
            f"Invalid status '{status}'. "
            f"Valid: {', '.join(sorted(_VALID_STATUSES))}",
            err=True,
        )
        raise typer.Exit(code=1)


def _superseded_label(superseded_by: str | None) -> str:
    """Render the Superseded-by cell: successor ID, ``retired``, or blank."""
    if superseded_by is None:
        return ""
    return "retired" if superseded_by == "retired" else superseded_by


@app.command()
def history(
    entry_id: str | None = typer.Argument(
        None, help="Decision or constraint ID to trace (omit for full table)"
    ),
) -> None:
    """Show deprecation records: one entry's lifecycle chain or the full table."""
    store = _resolve_store()

    if entry_id is None:
        rows = store.get_all_deprecations()
        if not rows:
            typer.echo("No deprecation history recorded yet.")
            return
        dep_table = table()
        dep_table.add_column("At", style=C_DIM)
        dep_table.add_column("Type")
        dep_table.add_column("Entry", style=C_ID)
        dep_table.add_column("Replaced By")
        dep_table.add_column("Reason", overflow="fold")
        for r in rows:
            successor = r["replaced_by"] or "[dim]retired[/]"
            dep_table.add_row(
                r["at"], r["entry_type"], r["entry_id"], successor, r["reason"] or "",
            )
        console.print(panel(dep_table, title=f"Deprecation history ({len(rows)})"))
        return

    # Resolve the entry's type; fall back to scanning both tables.
    etype = store.get_entry_type(entry_id)
    records: list = []
    for candidate in ([etype] if etype else []) + (
        ["decision", "constraint"] if not etype else []
    ):
        records = store.get_replacement_chain(candidate, entry_id)
        if records:
            break

    if not records:
        typer.echo(f"No deprecation history for '{entry_id}'.")
        return

    # Arrow chain of distinct IDs (self-replacements don't extend the chain).
    chain_ids: list[str] = [entry_id]
    for rec in records:
        successor = rec["replaced_by"]
        if successor and successor != chain_ids[-1]:
            chain_ids.append(successor)

    dep_table = table()
    dep_table.add_column("At", style=C_DIM)
    dep_table.add_column("Entry", style=C_ID)
    dep_table.add_column("Replaced By")
    dep_table.add_column("Reason", overflow="fold")
    for rec in records:
        successor = rec["replaced_by"] or "[dim]retired[/]"
        dep_table.add_row(
            rec["at"], rec["entry_id"], successor, rec["reason"] or "",
        )
    console.print(
        panel(
            text(f"[{C_ID} bold]{' -> '.join(chain_ids)}[/]"),
            rule(),
            dep_table,
            title=f"History: {entry_id}",
        )
    )


@list_app.command()
def decisions(
    n: int = typer.Option(20, "-n", help="Show only the last N entries"),
    status: str | None = typer.Option(
        None, "--status",
        help="Filter by status: accepted, deprecated, active",
    ),
) -> None:
    """List architectural decisions in a scannable table."""
    store = _resolve_store()
    _validate_status(status)

    pairs = store.get_decisions_with_liveness()
    if status == "deprecated":
        pairs = [p for p in pairs if p[1] is not None]
    elif status in ("accepted", "active"):
        pairs = [p for p in pairs if p[1] is None]
    if n > 0:
        pairs = pairs[-n:]

    if not pairs:
        typer.echo("No decisions found.")
        raise typer.Exit()

    decision_table = table()
    decision_table.add_column("", width=3)  # status icon
    decision_table.add_column("ID", style=C_ID)
    decision_table.add_column("Date", style=C_DIM)
    decision_table.add_column("Title", overflow="fold")
    decision_table.add_column("Superseded By", style=C_WARN, overflow="fold")
    for d, superseded_by in pairs:
        date = d.created_at[:10] if d.created_at else ""
        icon = STATUS_OK if superseded_by is None else STATUS_DEPRECATED
        color = C_SUCCESS if superseded_by is None else C_WARN
        decision_table.add_row(
            f"[{color}]{icon}[/]",
            d.id,
            date,
            d.title,
            _superseded_label(superseded_by),
        )
    console.print(
        panel(decision_table, title=f"Decisions ({len(pairs)})")
    )


@list_app.command()
def constraints(
    n: int = typer.Option(20, "-n", help="Show only the last N entries"),
    status: str | None = typer.Option(
        None, "--status",
        help="Filter by status: accepted, deprecated, active",
    ),
) -> None:
    """List constraints in a scannable table."""
    store = _resolve_store()
    _validate_status(status)

    pairs = store.get_constraints_with_liveness()
    if status == "deprecated":
        pairs = [p for p in pairs if p[1] is not None]
    elif status in ("accepted", "active"):
        pairs = [p for p in pairs if p[1] is None]
    if n > 0:
        pairs = pairs[-n:]

    if not pairs:
        typer.echo("No constraints found.")
        raise typer.Exit()

    constraint_table = table()
    constraint_table.add_column("", width=3)  # status icon
    constraint_table.add_column("ID", style=C_ID)
    constraint_table.add_column("Enforced By", style=C_DIM)
    constraint_table.add_column("Description", overflow="fold")
    constraint_table.add_column("Superseded By", style=C_WARN, overflow="fold")
    for c, superseded_by in pairs:
        icon = STATUS_OK if superseded_by is None else STATUS_DEPRECATED
        color = C_SUCCESS if superseded_by is None else C_WARN
        enforced = f"via {c.enforced_by}" if c.enforced_by else ""
        constraint_table.add_row(
            f"[{color}]{icon}[/]",
            c.id,
            enforced,
            c.description,
            _superseded_label(superseded_by),
        )
    console.print(
        panel(constraint_table, title=f"Constraints ({len(pairs)})")
    )


@list_app.command()
def entities(
    n: int = typer.Option(20, "-n", help="Show only the last N entries"),
    stale: bool = typer.Option(
        False, "--stale",
        help="Show only paths referencing files that no longer exist",
    ),
) -> None:
    """List referenced file paths in a scannable table."""
    store = _resolve_store()

    all_p = store.get_all_paths()
    if stale:
        all_p = [p for p in all_p if not Path(p).exists()]
    if n > 0:
        all_p = all_p[-n:]

    if not all_p:
        typer.echo("No entities found.")
        raise typer.Exit()

    entity_table = table()
    entity_table.add_column("Path", style=C_ID, overflow="fold")
    for p in all_p:
        entity_table.add_row(p)
    console.print(
        panel(entity_table, title=f"Entities ({len(all_p)})")
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

    Writes a tombstone row (with reason) into the deprecations table;
    deprecated entries remain listed for history but are excluded from
    mementic memory queries.
    """
    store = _resolve_store()

    deprecated = 0
    already = 0
    errors = 0
    details: list[str] = []

    for id_ in ids:
        etype = store.get_entry_type(id_)
        if etype is None:
            typer.echo(f"Entity '{id_}' not found.", err=True)
            errors += 1
            continue
        result = store.deprecate(etype, id_, reason)

        if result["status"] == "deprecated":
            deprecated += 1
            row = store.find_deprecation(etype, id_)
            if row is not None:
                successor = row["replaced_by"] or "none (retired)"
                details.append(
                    f"[{C_DIM}]{id_}: reason: {row['reason'] or '-'} | "
                    f"successor: {successor} | at: {row['at']}[/]"
                )
        elif result["status"] == "already_deprecated":
            already += 1
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

    body: list = [text(", ".join(parts)), text(f"[{C_DIM}]IDs: {', '.join(ids)}[/]")]
    body.extend(text(line) for line in details)
    console.print(panel(*body, title="Deprecated"))

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
) -> None:
    """Record a new architectural decision.

    Auto-resolves against an existing entry with the same slug:
    new slug → accept; ≥80% word overlap → skip as duplicate;
    different content → deprecate the existing entry and accept the new one.
    """
    store = _resolve_store()
    entry_id = slugify(title, max_length=DECISION_ID_MAX_LENGTH)

    counts: dict[str, int] = {"accepted": 0, "skipped": 0, "deprecated": 0}
    disposition, tombstone_at = _auto_resolve(store, "decision", entry_id, rationale)
    if disposition == "skipped":
        counts["skipped"] += 1
        console.print(
            panel(
                text(
                    f"Title:  [{C_HEADER}]{title}[/]\n"
                    f"ID:     [{C_ID}]{entry_id}[/]\n"
                    f"Status: [{C_WARN}]skipped (duplicate of existing entry)[/]"
                ),
                _counts_panel_text(counts),
                title="Decision skipped",
            )
        )
        return

    counts["deprecated" if disposition == "deprecated" else "accepted"] += 1
    try:
        # Stamp created_at AFTER any auto-deprecation, structurally one
        # microsecond past the tombstone, so the new incarnation always
        # derives as live.
        store.add_decision(
            entry_id=entry_id,
            title=title,
            rationale=rationale,
            created_at=_successor_created_at(tombstone_at),
            paths=[_clean_entity_path(p) for p in (entities or [])],
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        console.print(
            panel(text(f"[{C_FAIL}]Error: {exc}[/]"), title="Decision not added")
        )
        raise typer.Exit(code=1) from exc
    status_label = _accepted_status_label(disposition, noun="decision")
    console.print(
        panel(
            text(
                f"Title:  [{C_HEADER}]{title}[/]\n"
                f"ID:     [{C_ID}]{entry_id}[/]\n"
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
    store = _resolve_store()
    cid = (
        slugify(rule_id, max_length=CONSTRAINT_ID_MAX_LENGTH)
        if rule_id
        else slugify(description, max_length=CONSTRAINT_ID_MAX_LENGTH)
    )

    counts: dict[str, int] = {"accepted": 0, "skipped": 0, "deprecated": 0}
    # Validate the enforcing decision BEFORE auto-resolve: a typo'd
    # --decision-id must not orphan a tombstone over the existing entry.
    if decision_id and _decision(store, decision_id) is None:
        console.print(
            panel(
                text(
                    f"[{C_FAIL}]Error: enforcing decision "
                    f"'{decision_id}' does not exist.[/]"
                ),
                title="Constraint not added",
            )
        )
        raise typer.Exit(code=1)
    disposition, tombstone_at = _auto_resolve(store, "constraint", cid, description)
    if disposition == "skipped":
        counts["skipped"] += 1
        console.print(
            panel(
                text(
                    f"ID:       [{C_ID}]{cid}[/]\n"
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
        store.add_constraint(
            entry_id=cid,
            description=description,
            enforced_by=decision_id or "",
            created_at=_successor_created_at(tombstone_at),
            paths=[_clean_entity_path(p) for p in entities],
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        console.print(
            panel(text(f"[{C_FAIL}]Error: {exc}[/]"), title="Constraint not added")
        )
        raise typer.Exit(code=1) from exc
    bound_list = ", ".join(entities[:5])
    if len(entities) > 5:
        bound_list += f" +{len(entities) - 5} more"
    status_label = _accepted_status_label(disposition, noun="constraint")
    console.print(
        panel(
            text(
                f"ID:       [{C_ID}]{cid}[/]\n"
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
