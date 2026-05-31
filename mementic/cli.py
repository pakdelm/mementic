from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, TYPE_CHECKING

import typer
from dotenv import load_dotenv
from pydantic import ValidationError

load_dotenv()

from mementic.config import load_config  # noqa: E402
from mementic.extraction import extract_from_session  # noqa: E402
from mementic.format import count_tokens, format_context_pack, timestamp  # noqa: E402
from mementic.graph import GraphStore  # noqa: E402
from mementic.models import CodeEntity, Decision, DecisionStatus, EntityType, MetricsEntry  # noqa: E402
from mementic.pipeline import run_init  # noqa: E402
from mementic.conflict import detect_conflicts  # noqa: E402
from mementic.metrics import UsageStore  # noqa: E402

if TYPE_CHECKING:
    from mementic.lint import LintFinding  # noqa: F401

app = typer.Typer(help="Mementic — semantic memory for engineering teams and AI agents")

CHECKPOINT_FILE = ".mementic/checkpoint"


def _load_graph_safe(graph_path: str) -> GraphStore:
    """Load graph with distinct error messages for missing vs corrupt files.

    Args:
        graph_path: Path to the graph JSON file.

    Returns:
        A loaded GraphStore instance.

    Raises:
        typer.Exit: With code 1 and a clear message for missing or corrupt graph.
    """
    try:
        return GraphStore.load(graph_path)
    except FileNotFoundError:
        typer.echo(
            "No graph found. Run 'mementic init' first.", err=True
        )
        raise typer.Exit(code=1)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError, ValidationError):
        typer.echo(
            f"Graph file at {graph_path} is corrupt. "
            f"Run 'mementic init' to rebuild.",
            err=True,
        )
        raise typer.Exit(code=1)


def _load_checkpoint() -> str:
    p = Path(CHECKPOINT_FILE)
    return p.read_text().strip() if p.exists() else ""


def _save_checkpoint() -> None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True
        )
        if result.returncode == 0:
            Path(CHECKPOINT_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(CHECKPOINT_FILE).write_text(result.stdout.strip())
    except FileNotFoundError:
        typer.echo("git not found. Checkpoint not saved.", err=True)


def _run_git_diff(reference: str) -> tuple[str, int]:
    """Run git diff against a reference. Returns (diff_text, returncode)."""
    try:
        result = subprocess.run(
            ["git", "diff", reference], capture_output=True, text=True
        )
        return result.stdout or "", result.returncode
    except FileNotFoundError:
        typer.echo("git not found. Cannot compute diff.", err=True)
        return "", 1


@app.command()
def init(
    config_path: str = ".mementic.json",
    interactive: bool = typer.Option(
        False, "--interactive", help="Walk through setup interactively"
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Skip auto-bootstrap; fail if .mementic.json is missing"
    ),
) -> None:
    """Initialize the knowledge graph from configured sources.

    If .mementic.json doesn't exist, auto-discovers ./docs/adr/ and
    ./docs/instructions/ directories and creates a default config.
    """
    config_file = Path(config_path)

    if not config_file.exists():
        if force:
            typer.echo(
                f"No config found at {config_path} and --force is set. "
                f"Cannot proceed.",
                err=True,
            )
            raise typer.Exit(code=1)

        typer.echo("No config found — auto-discovering sources...")
        discovered: dict[str, list[dict[str, str]]] = {
            "adr_dirs": [],
            "opencode_instructions": [],
        }

        if Path("./docs/adr").is_dir():
            discovered["adr_dirs"].append({"path": "./docs/adr"})
            typer.echo("  Found: ./docs/adr/")
        if Path("./docs/instructions").is_dir():
            discovered["opencode_instructions"].append({"path": "./docs/instructions"})
            typer.echo("  Found: ./docs/instructions/")

        if not any(discovered.values()):
            typer.echo(
                "No sources discovered. Create .mementic.json manually or "
                "add docs/adr/ and docs/instructions/ directories.",
                err=True,
            )
            raise typer.Exit(code=1)

        auto_config: dict[str, Any] = {
            "sources": {k: v for k, v in discovered.items() if v},
            "output": {"graph_path": "./.mementic/graph.json"},
            "llm": {"provider": "deepseek", "model": "deepseek-chat"},
        }
        config_file.write_text(json.dumps(auto_config, indent=2))
        typer.echo(f"Created {config_path}")

    if interactive:
        typer.echo("Interactive setup is available. Edit .mementic.json directly:")
        typer.echo(f"  {config_file.absolute()}")
        typer.echo("Supported LLM providers: deepseek, openai, openrouter, ollama")
        provider = typer.prompt(
            "LLM provider", default="deepseek"
        )
        model = typer.prompt(
            f"Model for '{provider}'", default="deepseek-chat"
        )
        # Reload and update config
        cfg_data = json.loads(config_file.read_text())
        cfg_data.setdefault("llm", {})["provider"] = provider
        cfg_data.setdefault("llm", {})["model"] = model
        cfg_data.setdefault("llm", {})["extraction_model"] = model
        config_file.write_text(json.dumps(cfg_data, indent=2))
        typer.echo(f"Updated {config_path}")

    try:
        graph, all_usage = run_init(config_path)
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    stats = graph.stats()
    typer.echo(f"Mementic graph initialized: {stats}")

    # Log extraction costs to metrics
    if all_usage:
        store = UsageStore()
        for usage in all_usage:
            if usage.total_tokens > 0 or usage.status != "success":
                entry = MetricsEntry(
                    timestamp=timestamp(),
                    operation="init",
                    source_label=usage.model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    total_tokens=usage.total_tokens,
                    model=usage.model,
                    status=usage.status,
                )
                store.append(entry)


@app.command()
def capture(
    session: str | None = None,
    notes: str | None = None,
    agent: str | None = None,
) -> None:
    """Extract architectural decisions from an agent session.

    Decisions are auto-accepted unless flagged by the conflict detector.
    Flagged decisions are set to 'proposed' for review via `mementic review`.
    """
    from mementic.session import get_registry

    registry = get_registry()
    cfg = load_config()
    detected_agent: str | None = None

    if not session and not agent:
        # Auto-detect: find most recent session across all providers
        log_dir_overrides: dict[str, list[str]] = {}
        for name in ("opencode", "claude-code", "generic"):
            agent_cfg = getattr(cfg.sessions, name.replace("-", "_"))
            if agent_cfg.log_dirs:
                log_dir_overrides[name] = agent_cfg.log_dirs
        provider, detected = registry.detect(log_dirs=log_dir_overrides or None)
        if not detected:
            typer.echo("No session found. Provide --session PATH.", err=True)
            raise typer.Exit(code=1)
        session = detected
        detected_agent = provider.agent_name
        typer.echo(f"[{detected_agent}] Using session: {session}")
    elif agent:
        # Explicit agent, possibly with explicit session path
        provider, resolved = registry.detect(agent=agent, session_path=session)
        if not resolved:
            typer.echo(
                f"No session found for agent '{agent}'. Provide --session PATH.",
                err=True,
            )
            raise typer.Exit(code=1)
        session = resolved
        detected_agent = agent
        typer.echo(f"[{detected_agent}] Using session: {session}")
    elif session:
        # Explicit session path, detect which provider can read it
        provider, _ = registry.detect(session_path=session)
        detected_agent = provider.agent_name
        typer.echo(f"[{detected_agent}] Using session: {session}")
    else:
        typer.echo("No session found. Provide --session PATH.", err=True)
        raise typer.Exit(code=1)

    session_text = Path(session).read_text()

    checkpoint = _load_checkpoint()
    got_diff = False
    if checkpoint:
        git_diff, rc = _run_git_diff(checkpoint)
        got_diff = rc == 0
        if rc != 0:
            typer.echo(
                "Warning: Capture requires a git repository. "
                "Run 'git init' or use --notes to capture without git diff.",
                err=True,
            )
            git_diff = ""
    else:
        git_diff, rc = _run_git_diff("HEAD~1")
        got_diff = rc == 0
        if rc != 0:
            typer.echo(
                "Warning: Capture requires a git repository. "
                "Run 'git init' or use --notes to capture without git diff.",
                err=True,
            )
            git_diff = ""
        else:
            typer.echo("No checkpoint found — using HEAD~1 as baseline.")

    try:
        extraction, _usage = extract_from_session(session_text, git_diff, notes or "", cfg.llm)
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)

    if not extraction.decisions and not extraction.constraints:
        typer.echo("No architectural decisions or constraints found.")
        raise typer.Exit()

    graph_path = cfg.output.graph_path
    if Path(graph_path).exists():
        try:
            graph = _load_graph_safe(graph_path)
        except typer.Exit:
            typer.echo("Starting with empty graph.", err=True)
            graph = GraphStore()
    else:
        graph = GraphStore()

    # ── Conflict detection ──
    from mementic.format import slugify

    conflicts = detect_conflicts(
        graph,
        extraction.decisions,
        # Flat entity list shared across all decisions (extraction limitation)
        {d.id: extraction.affected_entities for d in extraction.decisions},
    )
    conflicting_existing_slugs = {c.id for c in conflicts}

    auto_accepted = 0
    flagged = 0

    for d in extraction.decisions:
        if slugify(d.title, max_length=80) in conflicting_existing_slugs:
            d.status = DecisionStatus.PROPOSED
            flagged += 1
        else:
            d.status = DecisionStatus.ACCEPTED
            auto_accepted += 1

        graph.add_decision(d)
        for entity_path in extraction.affected_entities:
            graph.add_code_entity(
                CodeEntity(id=entity_path, type=EntityType.FILE)
            )
            graph.link(d.id, entity_path, "affects")

    for c in extraction.constraints:
        graph.add_constraint(c)
        for entity_path in c.bound_entities:
            graph.link(c.id, entity_path, "binds_to")
    for edge in extraction.edges_to_create:
        graph.link(edge.source, edge.target, edge.relation)

    Path(graph_path).parent.mkdir(parents=True, exist_ok=True)
    graph.save(graph_path)

    # ── Summary output (F6) ──
    typer.echo()
    typer.echo("=" * 50)
    typer.echo("Capture complete")
    typer.echo("=" * 50)
    typer.echo(f"  Extracted: {len(extraction.decisions)} decisions, "
               f"{len(extraction.constraints)} constraints")
    typer.echo(f"  Auto-accepted: {auto_accepted}")
    if auto_accepted:
        for d in extraction.decisions:
            if d.status == DecisionStatus.ACCEPTED:
                typer.echo(f"    - [{d.id}] {d.title}")
    if flagged:
        typer.echo(f"  Flagged for review: {flagged}")
        for d in extraction.decisions:
            if slugify(d.title, max_length=80) in conflicting_existing_slugs:
                typer.echo(f"    - [{d.id}] {d.title}")
        typer.echo()
        typer.echo("  Run 'mementic review' to resolve flagged conflicts.")
    typer.echo(f"  Written to {graph_path}")

    # Log capture metrics
    metrics_store = UsageStore()
    entry = MetricsEntry(
        timestamp=timestamp(),
        operation="capture",
        source_label=f"{detected_agent}:{session}" if detected_agent else (session or "auto-detected"),
        input_tokens=_usage.input_tokens,
        output_tokens=_usage.output_tokens,
        total_tokens=_usage.total_tokens,
        model=_usage.model,
        status=_usage.status,
    )
    metrics_store.append(entry)

    if got_diff:
        _save_checkpoint()


@app.command()
def context(
    task: str,
    files: list[str],
    benchmark: bool = typer.Option(
        False, "--benchmark", help="Measure token savings vs. raw source docs"
    ),
) -> None:
    """Load constraints and produce a context pack for a given task.

    Queries the knowledge graph for constraints and decisions relevant to
    the specified files, then formats them as a context pack for agent injection.
    """
    cfg = load_config()

    graph = _load_graph_safe(cfg.output.graph_path)

    constraints = graph.get_constraints_for_entities(files)
    decisions = graph.get_decisions_for_entities(files)

    output = format_context_pack(task, decisions, constraints)
    typer.echo(output)

    if benchmark:
        # Collect all source file paths from config
        source_paths: list[str] = []
        for adr_src in cfg.sources.adr_dirs:
            source_paths.append(adr_src.path)
        for oc_src in cfg.sources.opencode_instructions:
            source_paths.append(oc_src.path)
        for claude_src in cfg.sources.claude_instructions:
            source_paths.append(claude_src.path)

        # Concatenate all *.md files from source directories
        all_source_text = ""
        for dir_path in source_paths:
            p = Path(dir_path)
            if not p.is_dir():
                continue
            for md_file in sorted(p.rglob("*.md")):
                try:
                    all_source_text += md_file.read_text(encoding="utf-8") + "\n"
                except Exception:
                    continue

        baseline_tokens = count_tokens(all_source_text)
        context_pack_tokens = count_tokens(output)
        savings = baseline_tokens - context_pack_tokens
        ratio = savings / baseline_tokens if baseline_tokens > 0 else 0.0

        typer.echo()
        typer.echo("Benchmark")
        typer.echo(f"  Raw source size:   {baseline_tokens:,} tokens")
        typer.echo(f"  Context pack:      {context_pack_tokens:,} tokens")
        typer.echo(f"  Savings:           {savings:,} tokens ({ratio:.0%})")

        # Log benchmark metrics
        metrics_store = UsageStore()
        entry = MetricsEntry(
            timestamp=timestamp(),
            operation="enforce_benchmark",
            source_label=task,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            model="",
            status="success",
            baseline_tokens=baseline_tokens,
            context_pack_tokens=context_pack_tokens,
            savings_ratio=ratio,
        )
        metrics_store.append(entry)


@app.command()
def query(
    entity: str | None = None,
    decision: str | None = None,
    constraint: str | None = None,
    search: str | None = None,
    depth: int = 1,
) -> None:
    """Search decisions, constraints, and entities in the graph."""
    cfg = load_config()

    graph = _load_graph_safe(cfg.output.graph_path)

    if entity:
        decisions = graph.get_evolution(entity)
        constraints = graph.get_constraints_for_entities([entity])

        if depth >= 2:
            seen_entities: set[str] = {entity}
            seen_ids = {c.id for c in constraints}
            for _level in range(depth - 1):
                next_entities: set[str] = set()
                for c in constraints:
                    if c.bound_entities:
                        next_entities.update(
                            be for be in c.bound_entities if be not in seen_entities
                        )
                if not next_entities:
                    break
                seen_entities.update(next_entities)
                extra = graph.get_constraints_for_entities(list(next_entities))
                for c in extra:
                    if c.id not in seen_ids:
                        constraints.append(c)
                        seen_ids.add(c.id)

        typer.echo(f"Evolution for {entity} (depth {depth}):\n")
        for d in decisions:
            typer.echo(f"  [{d.id}] {d.title} ({d.timestamp})")
            typer.echo(f"  {d.rationale}\n")

        if constraints:
            typer.echo("Related constraints:\n")
            for c in constraints:
                typer.echo(f"  [{c.id}] {c.description}")
    elif search:
        all_d = graph.get_all_decisions()
        all_c = graph.get_all_constraints()
        term = search.lower()

        matching_d = [
            d for d in all_d if term in d.title.lower() or term in d.rationale.lower()
        ]
        matching_c = [
            c for c in all_c if term in c.description.lower()
        ]

        typer.echo(f"Decisions matching '{search}':")
        for d in matching_d:
            typer.echo(f"  [{d.id}] {d.title}")
        typer.echo(f"\nConstraints matching '{search}':")
        for c in matching_c:
            typer.echo(f"  [{c.id}] {c.description}")
    else:
        stats = graph.stats()
        typer.echo(f"Graph stats: {stats}")


@app.command()
def log(n: int = 20) -> None:
    """Show recent graph mutations."""
    audit_path = Path(".mementic/audit.jsonl")
    if not audit_path.exists():
        typer.echo("No audit log found. Run init or capture first.")
        raise typer.Exit()

    lines = [line for line in audit_path.read_text().strip().split("\n") if line.strip()]
    if not lines:
        typer.echo("Audit log is empty.")
        raise typer.Exit()
    for line in lines[-n:]:
        entry = json.loads(line)
        typer.echo(f"[{entry['timestamp']}] {entry['action']}: {entry.get('id', '-')}")


@app.command()
def server():
    """Start the MCP server for agent integration (stdio transport)."""
    import sys
    from mementic.server import mcp
    sys.stderr.write("Mementic MCP server starting (stdio transport)...\n")
    mcp.run(transport="stdio")


@app.command()
def status(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show token usage totals"
    ),
) -> None:
    """Show graph statistics."""
    cfg = load_config()

    graph = _load_graph_safe(cfg.output.graph_path)
    stats = graph.stats()
    typer.echo(f"Decisions: {stats.get('decisions', 0)}")
    typer.echo(f"Constraints: {stats.get('constraints', 0)}")
    typer.echo(f"Code Entities: {stats.get('entities', 0)}")
    typer.echo(f"Edges: {stats.get('edges', 0)}")

    if verbose:
        metrics_path = Path(".mementic/metrics.jsonl")
        if not metrics_path.exists():
            typer.echo()
            typer.echo("No metrics data yet.")
        else:
            store = UsageStore()
            summary = store.summary()
            typer.echo()
            typer.echo("Token Usage")
            typer.echo(f"  LLM calls:         {summary['total_calls']}")
            typer.echo(f"  Total input:       {summary['total_input_tokens']:,} tokens")
            typer.echo(f"  Total output:      {summary['total_output_tokens']:,} tokens")
            typer.echo(f"  Total cost:        {summary['total_tokens']:,} tokens")


@app.command()
def graph(
    output: str = typer.Option(
        ".mementic/mementic_graph.html", "--output", "-o",
        help="Output HTML file path",
    ),
    open_browser: bool = typer.Option(
        False, "--open", "-O",
        help="Open the generated file in the default browser",
    ),
    filter_type: str | None = typer.Option(
        None, "--filter", "-f",
        help="Filter nodes by type: decision, constraint, entity",
    ),
) -> None:
    """Generate an interactive HTML visualization of the knowledge graph.

    Produces a self-contained HTML file with a force-directed graph.
    No server or external dependencies required for viewing.
    """
    import webbrowser

    from mementic.visualize import generate_html_graph

    cfg = load_config()
    g = _load_graph_safe(cfg.output.graph_path)

    valid_filters = {"decision", "constraint", "entity"}
    ft = filter_type.lower() if filter_type else None
    if ft and ft not in valid_filters:
        typer.echo(
            f"Invalid filter '{filter_type}'. "
            f"Valid options: {', '.join(sorted(valid_filters))}",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(f"Generating graph visualization ({g.stats()['decisions']} decisions, "
               f"{g.stats()['constraints']} constraints, "
               f"{g.stats()['edges']} edges)...")

    html = generate_html_graph(g, filter_type=ft)

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(html, encoding="utf-8")
    typer.echo(f"Written to {output} ({len(html):,} bytes)")

    if open_browser:
        webbrowser.open(Path(output).resolve().as_uri())


@app.command()
def review() -> None:
    """Review flagged (proposed) decisions and constraints.

    Shows decisions that the conflict detector flagged as potentially
    conflicting with existing ones. Accept or reject each.
    """
    cfg = load_config()

    graph = _load_graph_safe(cfg.output.graph_path)

    pending_d = graph.get_pending_decisions()
    pending_c = graph.get_pending_constraints()

    if not pending_d and not pending_c:
        typer.echo("No pending proposals. Graph is clean!")
        return

    typer.echo(f"Pending decisions: {len(pending_d)}")
    typer.echo(f"Pending constraints: {len(pending_c)}")
    typer.echo()

    for d in pending_d:
        typer.echo(f"[{d.id}] {d.title}")
        typer.echo(f"  Rationale: {d.rationale}")
        if d.rejected_alternatives:
            typer.echo(f"  Rejected: {', '.join(d.rejected_alternatives)}")

        # Show potential conflicts
        conflicts = detect_conflicts(
            graph,
            [d],
            {d.id: _get_entities_for_decision(graph, d.id)},
        )
        if conflicts:
            typer.echo("  Conflicts with:")
            for conflict_d in conflicts:
                typer.echo(f"    - [{conflict_d.id}] {conflict_d.title}")

        choice = typer.confirm("Accept?", default=True)
        if choice:
            d.status = DecisionStatus.ACCEPTED
            graph.add_decision(d)
        else:
            d.status = DecisionStatus.REJECTED
            graph.add_decision(d)

    for c in pending_c:
        typer.echo(f"\n[{c.id}] {c.description}")
        choice = typer.confirm("Accept?", default=True)
        if choice:
            c.status = "active"
            graph.add_constraint(c)
        else:
            c.status = "deprecated"
            graph.add_constraint(c)

    graph.save(cfg.output.graph_path)
    typer.echo("Review complete.")


def _get_entities_for_decision(graph: GraphStore, decision_id: str) -> list[str]:
    """Get affected entity paths for a decision node by following its edges."""
    entities: list[str] = []
    for _nid, ntype, _payload in graph.iter_nodes():
        if ntype != "CodeEntity":
            continue
        for pred in graph.get_predecessors(_nid):
            if pred == decision_id:
                entities.append(_nid)
    return entities


def _status_icon(status: str) -> str:
    """Return a compact status marker for list output."""
    icons = {
        "accepted": "+",
        "proposed": "?",
        "deprecated": "-",
        "rejected": "x",
        "active": "+",
    }
    return icons.get(status, " ")


@app.command(name="list")
def list_cmd(
    entity_type: str = typer.Argument(
        "decisions",
        help="Type to list: decisions, constraints, or entities",
    ),
    status: str | None = typer.Option(
        None, "--status",
        help="Filter by status: proposed, accepted, deprecated, rejected, active",
    ),
    n: int = typer.Option(
        0, "-n",
        help="Show only the last N entries",
    ),
    stale: bool = typer.Option(
        False, "--stale",
        help="Show only entities referencing files that no longer exist",
    ),
) -> None:
    """Browse decisions, constraints, and entities in a scannable table.

    Examples:
        mementic list decisions
        mementic list decisions --status proposed
        mementic list decisions -n 10
        mementic list constraints
        mementic list entities --stale
    """
    cfg = load_config()

    graph = _load_graph_safe(cfg.output.graph_path)

    entity_type = entity_type.lower()
    if entity_type not in ("decisions", "constraints", "entities"):
        typer.echo(
            f"Invalid type '{entity_type}'. Use: decisions, constraints, entities.",
            err=True,
        )
        raise typer.Exit(code=1)

    valid_statuses = {"proposed", "accepted", "deprecated", "rejected", "active", "superseded"}
    if status and status not in valid_statuses:
        typer.echo(
            f"Invalid status '{status}'. Valid: {', '.join(sorted(valid_statuses))}",
            err=True,
        )
        raise typer.Exit(code=1)

    if entity_type == "decisions":
        decisions = graph.get_all_decisions()
        if status:
            decisions = [d for d in decisions if d.status == status]
        if n > 0:
            decisions = decisions[-n:]

        if not decisions:
            typer.echo("No decisions found.")
            raise typer.Exit()

        max_id = max((len(d.id) for d in decisions), default=0)
        max_id = max(max_id, 4)
        pad = max_id + 2  # space after longest ID

        typer.echo(f"Decisions ({len(decisions)}):")
        for d in decisions:
            date = d.timestamp[:10] if d.timestamp else ""
            icon = _status_icon(d.status)
            typer.echo(
                f"  {icon}  {d.id:<{pad}}  {date}"
            )
            typer.echo(f"     {d.title}")

    elif entity_type == "constraints":
        constraints = graph.get_all_constraints()
        if status:
            constraints = [c for c in constraints if c.status == status]
        if n > 0:
            constraints = constraints[-n:]

        if not constraints:
            typer.echo("No constraints found.")
            raise typer.Exit()

        max_id = max((len(c.id) for c in constraints), default=0)
        max_id = max(max_id, 4)
        pad = max_id + 2

        typer.echo(f"Constraints ({len(constraints)}):")
        for c in constraints:
            enforced = f"via {c.enforced_by}" if c.enforced_by else ""
            icon = _status_icon(c.status)
            typer.echo(
                f"  {icon}  {c.id:<{pad}}  {enforced}"
            )
            typer.echo(f"     {c.description}")

    elif entity_type == "entities":
        entities: list[tuple[str, dict[str, Any]]] = []
        for nid, ntype, payload in graph.iter_nodes():
            if ntype == "CodeEntity":
                entities.append((nid, payload))

        if stale:
            entities = [
                (nid, p) for nid, p in entities
                if p.get("type") == "file" and not Path(nid).exists()
            ]
        elif status:
            typer.echo("Note: entities don't have statuses. Ignoring --status filter.")

        if n > 0:
            entities = entities[-n:]

        if not entities:
            typer.echo("No entities found.")
            raise typer.Exit()

        max_id = max((len(eid) for eid, _ in entities), default=0)
        max_id = max(max_id, 4)
        pad = max_id + 2

        typer.echo(f"Entities ({len(entities)}):")
        for eid, p in entities:
            etype = p.get("type", "file")
            repo = p.get("repo", "-")
            typer.echo(f"  {eid:<{pad}}  {etype:<10}  {repo}")


@app.command()
def metrics(
    json_output: bool = typer.Option(
        False, "--json", help="Output as JSON"
    ),
    reset: bool = typer.Option(
        False, "--reset", help="Reset all metrics after displaying"
    ),
) -> None:
    """Display aggregated token usage metrics."""
    store = UsageStore()
    summary = store.summary()

    if json_output:
        typer.echo(json.dumps(summary, indent=2, default=str))
    else:
        typer.echo("Token Usage Since Inception")
        typer.echo("─" * 50)
        typer.echo(f"  LLM calls made:      {summary['total_calls']}")
        typer.echo(f"  Input tokens sent:   {summary['total_input_tokens']:,}")
        typer.echo(f"  Output tokens:        {summary['total_output_tokens']:,}")
        typer.echo(f"  Total tokens:         {summary['total_tokens']:,}")

        typer.echo()
        typer.echo("Breakdown by Activity")
        typer.echo("─" * 50)

        # Human-friendly labels for known operations
        operation_labels: dict[str, str] = {
            "init": "one-time setup cost",
            "capture": "decision extraction",
            "enforce_benchmark": "benchmark runs",
            "mcp_context_pack": "agent context packs",
        }

        by_operation = summary.get("by_operation", {})
        if by_operation:
            for op, data in sorted(by_operation.items()):
                label = operation_labels.get(op, op)
                typer.echo(
                    f"  {op:<22} {data['calls']:>5} calls   "
                    f"{data['total_tokens']:>10,} tokens   ({label})"
                )
        else:
            typer.echo("  (no activity yet)")

        typer.echo()
        typer.echo("Agent Context Savings (benchmarks)")
        typer.echo("─" * 50)

        benchmark_tasks = summary.get("benchmark_tasks", 0)
        if benchmark_tasks > 0:
            avg_baseline = summary["avg_baseline_tokens"]
            avg_context_pack = summary["avg_context_pack_tokens"]
            avg_savings = avg_baseline - avg_context_pack
            avg_ratio = summary["avg_savings_ratio"]
            breakeven = summary.get("tasks_to_breakeven")
            typer.echo(f"  Tasks benchmarked:   {benchmark_tasks}")
            typer.echo(f"  Avg. raw source size: {avg_baseline:,.0f} tokens")
            typer.echo(f"  Avg. context pack:    {avg_context_pack:,.0f} tokens")
            typer.echo(
                f"  Avg. savings:         {avg_savings:,.0f} tokens"
                f" ({avg_ratio:.0%})"
            )
            if breakeven is not None:
                typer.echo(f"  Tasks to breakeven:   ~{breakeven:.0f} tasks")
        else:
            typer.echo(
                "  No benchmarks run yet. "
                "Use `mementic context --benchmark` to measure savings."
            )

    if reset:
        if typer.confirm("Delete all metrics?"):
            store.reset()
            typer.echo("Metrics reset.")


@app.command()
def deprecate(
    id: str = typer.Argument(..., help="Decision ID to deprecate"),
    reason: str = typer.Option(
        "", "--reason", "-r",
        help="Reason for deprecation",
    ),
) -> None:
    """Mark a decision as deprecated.

    Appends a deprecation note to the rationale and writes an audit entry.
    The next capture will create a better decision — don't edit history.
    """
    cfg = load_config()

    graph = _load_graph_safe(cfg.output.graph_path)

    if not graph.has_node(id):
        typer.echo(
            f"Decision '{id}' not found. Run 'mementic list decisions' to see IDs.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Find the node and its payload in one pass
    target_payload: dict | None = None
    for nid, ntype, payload in graph.iter_nodes():
        if nid == id:
            if ntype != "Decision":
                typer.echo(f"'{id}' is a {ntype}, not a Decision.", err=True)
                raise typer.Exit(code=1)
            target_payload = payload
            break

    if target_payload is None:
        typer.echo(f"Decision '{id}' could not be loaded.", err=True)
        raise typer.Exit(code=1)

    target = Decision(**target_payload)

    if target.status == DecisionStatus.DEPRECATED:
        typer.echo(f"Decision '{id}' is already deprecated.")
        raise typer.Exit()

    deprecation_note = f" [DEPRECATED: {reason}]" if reason else " [DEPRECATED]"
    target.rationale += deprecation_note
    target.status = DecisionStatus.DEPRECATED
    graph.add_decision(target)

    graph.save(cfg.output.graph_path)
    typer.echo(f"Deprecated decision '{id}': {target.title}")


@app.command(name="lint")
def lint_cmd(
    fix: bool = typer.Option(
        False, "--fix",
        help="Auto-resolve all fixable issues and save the graph",
    ),
) -> None:
    """Run rules-based graph health checks.

    Checks 5 deterministic rules for graph integrity. No LLM cost.
    `mementic lint` shows issues grouped by actionability.
    `mementic lint --fix` auto-resolves everything that can be fixed safely.
    """
    from mementic.lint import lint, lint_fix

    cfg = load_config()

    graph = _load_graph_safe(cfg.output.graph_path)

    result = lint(graph)

    if not result.findings:
        typer.echo("Graph is clean — no issues found.")
        return

    fixable = [f for f in result.findings if f.fixable]
    needs_review = [f for f in result.findings if not f.fixable]

    total = len(result.findings)
    typer.echo(f"{total} issue(s): {len(fixable)} auto-fixable, "
               f"{len(needs_review)} need review.")
    typer.echo()

    if fix:
        counts = lint_fix(graph, result)
        graph.save(cfg.output.graph_path)

        typer.echo(f"Fixed {counts['total']} issue(s). Graph saved.")
        if counts["removed"]:
            typer.echo(f"  {counts['removed']} entity node(s) removed "
                       f"(stale or orphaned)")
        if counts["deprecated"]:
            typer.echo(f"  {counts['deprecated']} constraint(s) deprecated "
                       f"(cascaded from deprecated decision)")
        if counts["cleared"]:
            typer.echo(f"  {counts['cleared']} broken enforced_by reference(s) cleared")

        remaining = [f for f in result.findings if not f.fixable]
        if remaining:
            typer.echo()
            typer.echo(f"{len(remaining)} remaining (need manual review):")
            _print_findings(remaining)
    else:
        if fixable:
            typer.echo("Auto-fixable (run `mementic lint --fix`):")
            _print_findings(fixable)
        if needs_review:
            if fixable:
                typer.echo()
            typer.echo("Needs review:")
            _print_findings(needs_review)


def _print_findings(findings: list[LintFinding]) -> None:
    """Print a compact list of lint findings."""
    markers = {"error": "ERR", "warning": "WARN", "info": "INFO"}
    for f in findings:
        marker = markers.get(f.severity, "?")
        typer.echo(f"  [{marker}] {f.rule:<24} {f.message}")
