from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, TYPE_CHECKING

import typer
from dotenv import load_dotenv
from pydantic import ValidationError

load_dotenv()

from mementic.config import (  # noqa: E402
    load_config,
    load_global_config,
    resolve_project,
    save_global_config,
)
from mementic.extraction import extract_from_session  # noqa: E402
from mementic.format import format_context_pack, timestamp  # noqa: E402
from mementic.graph import GraphStore  # noqa: E402
from mementic.models import CodeEntity, Decision, DecisionStatus, EntityType, MetricsEntry  # noqa: E402
from mementic.pipeline import run_init  # noqa: E402
from mementic.conflict import detect_conflicts  # noqa: E402
from mementic.metrics import UsageStore  # noqa: E402

if TYPE_CHECKING:
    from mementic.config import MementicConfig  # noqa: F401
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


def _resolve_graph(project: str | None = None) -> tuple[GraphStore, "MementicConfig"]:
    """Resolve graph for a project or fall back to legacy graph.json.

    When ``--project`` is not given, uses the legacy graph.json path.
    When ``--project`` is given, resolves the named project and opens its
    SQLite database.

    Args:
        project: Optional project name from ``--project`` flag.

    Returns:
        A ``(GraphStore, MementicConfig)`` tuple.

    Raises:
        typer.Exit: When an explicit ``--project`` is not found.
    """
    cfg = load_config()
    if project is None:
        # No project requested — use legacy graph.json
        return _load_graph_safe(cfg.output.graph_path), cfg

    try:
        proj_name, global_cfg = resolve_project(project)
        proj_entry = global_cfg.projects[proj_name]
        db_path = Path(proj_entry.db_path).expanduser()
        return GraphStore(db_path=db_path), cfg
    except ValueError as e:
        typer.echo(str(e), err=True)
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
        pass  # git not found — not important at this point


def _run_git_diff(reference: str) -> tuple[str, int]:
    """Run git diff against a reference. Returns (diff_text, returncode).

    Return codes:
        0   — success
        1   — git not found on PATH
        2   — not a git repository (rc 128 from git)
        3   — no new commits (reference is HEAD)
    """
    try:
        result = subprocess.run(
            ["git", "diff", reference], capture_output=True, text=True
        )
        return result.stdout or "", result.returncode
    except FileNotFoundError:
        return "", 1


@app.command()
def init(
    config_path: str = ".mementic.json",
    repo: str | None = typer.Option(
        None, "--repo",
        help="Path to repository root for codebase structural scan",
    ),
    no_scan: bool = typer.Option(
        False, "--no-scan",
        help="Skip codebase structural scan (use configured sources only)",
    ),
    interactive: bool = typer.Option(
        False, "--interactive", help="Walk through setup interactively"
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Skip auto-bootstrap; fail if .mementic.json is missing"
    ),
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name for project-aware init",
    ),
) -> None:
    """Initialize the knowledge graph from configured sources.

    If .mementic.json doesn't exist, auto-discovers ./docs/adr/ and
    ./docs/instructions/ directories and creates a default config.

    With --repo, scans the codebase structure for architectural patterns
    before processing configured sources. Use --no-scan to skip.
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
            "cursor_instructions": [],
            "windsurf_instructions": [],
        }

        if Path("./docs/adr").is_dir():
            discovered["adr_dirs"].append({"path": "./docs/adr"})
            typer.echo("  Found: ./docs/adr/")
        if Path("./docs/instructions").is_dir():
            discovered["opencode_instructions"].append({"path": "./docs/instructions"})
            typer.echo("  Found: ./docs/instructions/")
        if Path(".cursor/instructions").is_dir():
            discovered["cursor_instructions"].append({"path": ".cursor/instructions"})
            typer.echo("  Found: .cursor/instructions/")
        if Path(".windsurf/instructions").is_dir():
            discovered["windsurf_instructions"].append({"path": ".windsurf/instructions"})
            typer.echo("  Found: .windsurf/instructions/")

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

    # Auto-detect repo if not explicitly given
    effective_repo = repo
    if effective_repo is None and not no_scan:
        cwd = Path.cwd()
        indicators = (
            "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
            "Makefile", "setup.py", "setup.cfg",
        )
        for indicator in indicators:
            if (cwd / indicator).is_file():
                effective_repo = str(cwd)
                break

    # Resolve project info before init (may error on invalid --project)
    project_db_path: str | None = None
    project_name: str | None = None
    project_global_cfg: Any = None
    if project is not None:
        try:
            project_name, project_global_cfg = resolve_project(project)
            proj_entry = project_global_cfg.projects[project_name]
            project_db_path = str(Path(proj_entry.db_path).expanduser())
        except ValueError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1)

    try:
        graph, all_usage = run_init(config_path, repo_path=effective_repo, no_scan=no_scan)
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    stats = graph.stats()
    typer.echo(f"Mementic graph initialized: {stats}")

    # ── Project mode: copy graph to project SQLite database ──
    if project_db_path is not None and project_name is not None:
        from mementic.config import RepoEntry
        typer.echo(f"Saving to project '{project_name}' database...")
        project_graph = GraphStore(db_path=project_db_path)
        for d in graph.get_all_decisions():
            project_graph.add_decision(d)
        for c in graph.get_all_constraints():
            project_graph.add_constraint(c)
        for nid, ntype, payload in graph.iter_nodes():
            if ntype == "CodeEntity":
                project_graph.add_code_entity(CodeEntity(**payload))
        for src, tgt, rel in graph.iter_edges():
            project_graph.link(src, tgt, rel)
        project_graph.close()

        # Register repo if --repo was given or auto-detected
        if effective_repo:
            repo_path = str(Path(effective_repo).resolve())
            repo_name = Path(repo_path).name
            proj_entry.repos[repo_name] = RepoEntry(
                path=repo_path, label=repo_name
            )
            save_global_config(project_global_cfg)
            typer.echo(
                f"Registered repo '{repo_name}' in project '{project_name}'."
            )
        typer.echo(f"Project '{project_name}' initialized.")

    # Log extraction costs to metrics
    if all_usage:
        metrics_path: str = ".mementic/metrics.jsonl"
        if project_db_path is not None and project_name is not None:
            metrics_path = str(
                Path("~/.mementic/projects").expanduser()
                / f"{project_name}_metrics.jsonl"
            )
        store = UsageStore(path=metrics_path)
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
    tail: int = typer.Option(
        100, "--tail",
        help="Max messages from session tail (0 = skip session, use diff+notes only)",
    ),
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
    ),
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
        for name in ("opencode", "claude-code", "generic", "cursor", "windsurf"):
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
        # Check if there are new commits since last capture
        try:
            head_result = subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True
            )
        except FileNotFoundError:
            head_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="")
        if head_result.returncode == 0 and checkpoint.strip() == head_result.stdout.strip():
            typer.echo("No new commits since last capture.")
            git_diff = ""
        else:
            git_diff, rc = _run_git_diff(checkpoint)
            if rc == 1:
                typer.echo("git not found. Proceeding without diff.", err=True)
                git_diff = ""
            elif rc == 128:
                typer.echo("Not a git repository. Skipping diff.", err=True)
                git_diff = ""
            elif rc == 0:
                got_diff = True
                if not git_diff.strip():
                    typer.echo("No changes since last capture.")
            else:
                typer.echo(
                    "Warning: git diff failed. Proceeding without diff.",
                    err=True,
                )
                git_diff = ""
    else:
        git_diff, rc = _run_git_diff("HEAD~1")
        if rc == 1:
            typer.echo("git not found. Proceeding without diff.", err=True)
            git_diff = ""
        elif rc == 128:
            typer.echo("Not a git repository. Skipping diff.", err=True)
            git_diff = ""
        elif rc == 0:
            got_diff = True
            if not git_diff.strip():
                typer.echo("No changes since last capture.")
        else:
            typer.echo(
                "Warning: git diff failed. Proceeding without diff.",
                err=True,
            )
            git_diff = ""

    try:
        extraction, _usage = extract_from_session(
            session_text, git_diff, notes or "", cfg.llm, max_messages=tail
        )
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)

    if not extraction.decisions and not extraction.constraints:
        typer.echo("No architectural decisions or constraints found.")
        raise typer.Exit()

    # ── Load or create graph (project-aware) ──
    is_project = False
    project_display: str | None = None
    if project is not None:
        try:
            proj_name, global_cfg = resolve_project(project)
            proj_entry = global_cfg.projects[proj_name]
            db_path = Path(proj_entry.db_path).expanduser()
            graph = GraphStore(db_path=db_path)
            is_project = True
            project_display = proj_name
        except ValueError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1)
    else:
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

    if is_project:
        # SQLite mode: data already persisted via _write_back on each mutation
        pass
    else:
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
    if is_project and project_display:
        typer.echo(f"  Written to project '{project_display}' database")
    else:
        typer.echo(f"  Written to {graph_path}")

    # Log capture metrics
    metrics_path: str = ".mementic/metrics.jsonl"
    if is_project and project_display:
        metrics_path = str(
            Path("~/.mementic/projects").expanduser()
            / f"{project_display}_metrics.jsonl"
        )
    metrics_store = UsageStore(path=metrics_path)
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
    prefix: bool = typer.Option(
        False, "--prefix", help="Enable prefix matching for namespace queries (e.g. 'mementic/')"
    ),
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
    ),
) -> None:
    """Load constraints and produce a context pack for a given task.

    Queries the knowledge graph for constraints and decisions relevant to
    the specified files, then formats them as a context pack for agent injection.
    """
    graph, _cfg = _resolve_graph(project)

    constraints = graph.get_constraints_for_entities(files, prefix_match=prefix)
    decisions = graph.get_decisions_for_entities(files)

    output = format_context_pack(task, decisions, constraints)
    typer.echo(output)


@app.command()
def query(
    entity: str | None = None,
    decision: str | None = None,
    constraint: str | None = None,
    search: str | None = None,
    depth: int = 1,
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
    ),
) -> None:
    """Search decisions, constraints, and entities in the graph."""
    graph, _cfg = _resolve_graph(project)

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
            typer.echo(f"  [{d.id}] {d.title} ({d.created_at})")
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
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
    ),
) -> None:
    """Show graph statistics."""
    graph, _cfg = _resolve_graph(project)
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
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
    ),
) -> None:
    """Generate an interactive HTML visualization of the knowledge graph.

    Produces a self-contained HTML file with a force-directed graph.
    No server or external dependencies required for viewing.
    """
    import webbrowser

    from mementic.visualize import generate_html_graph

    g, _cfg = _resolve_graph(project)

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
def review(
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
    ),
) -> None:
    """Review flagged (proposed) decisions and constraints.

    Shows decisions that the conflict detector flagged as potentially
    conflicting with existing ones. Accept or reject each.
    """
    graph, _cfg = _resolve_graph(project)

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

    # save() is no-op when db-backed; writes JSON otherwise
    graph.save(_cfg.output.graph_path)
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
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
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
    graph, _cfg = _resolve_graph(project)

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
            date = d.created_at[:10] if d.created_at else ""
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
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name (uses project-scoped metrics when set)",
    ),
) -> None:
    """Display aggregated token usage metrics."""
    metrics_path = ".mementic/metrics.jsonl"
    if project is not None:
        try:
            proj_name, _global_cfg = resolve_project(project)
            metrics_path = str(
                Path("~/.mementic/projects").expanduser()
                / f"{proj_name}_metrics.jsonl"
            )
        except ValueError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1)

    store = UsageStore(path=metrics_path)
    summary = store.summary()

    if json_output:
        typer.echo(json.dumps(summary, indent=2, default=str))
    else:
        # -- Section 1: Extraction Cost --
        typer.echo("Token Economics")
        typer.echo("-" * 50)
        typer.echo("  Extraction cost (lifetime)")
        init_calls = summary["extraction_init_calls"]
        init_tokens = summary["extraction_init_tokens"]
        capture_calls = summary["extraction_capture_calls"]
        capture_tokens = summary["extraction_capture_tokens"]
        typer.echo(
            f"    init:       {init_calls:>4} calls   {init_tokens:>10,} tokens"
            f"   (one-time graph build)"
        )
        typer.echo(
            f"    capture:    {capture_calls:>4} calls   {capture_tokens:>10,} tokens"
            f"   (per-session extraction)"
        )
        typer.echo(
            f"    subtotal:              {summary['extraction_total_tokens']:>10,} tokens"
        )

        # ── Section 2: Context Pack Usage ──
        typer.echo()
        typer.echo("  Context pack usage (lifetime)")
        cp_queries = summary["context_pack_queries"]
        cp_total = summary["context_pack_total_tokens"]
        cp_avg = summary["context_pack_avg_tokens"]
        typer.echo(
            f"    queries:     {cp_queries:>4} calls   {cp_total:>10,} tokens"
        )
        typer.echo(
            f"    avg pack:    {cp_avg:>10,.0f} tokens/query"
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
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
    ),
) -> None:
    """Mark a decision as deprecated.

    Appends a deprecation note to the rationale and writes an audit entry.
    The next capture will create a better decision — don't edit history.
    """
    graph, cfg = _resolve_graph(project)

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

    # save() is no-op when db-backed; writes JSON otherwise
    graph.save(cfg.output.graph_path)
    typer.echo(f"Deprecated decision '{id}': {target.title}")


@app.command(name="lint")
def lint_cmd(
    fix: bool = typer.Option(
        False, "--fix",
        help="Auto-resolve all fixable issues and save the graph",
    ),
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
    ),
) -> None:
    """Run rules-based graph health checks.

    Checks 5 deterministic rules for graph integrity. No LLM cost.
    `mementic lint` shows issues grouped by actionability.
    `mementic lint --fix` auto-resolves everything that can be fixed safely.
    """
    from mementic.lint import lint, lint_fix

    graph, cfg = _resolve_graph(project)

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
        # save() is no-op when db-backed; writes JSON otherwise
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


# ── Project management commands ──────────────────────────────────────

project_app = typer.Typer(help="Manage Mementic projects")
app.add_typer(project_app, name="project")


@project_app.command()
def create(
    name: str = typer.Argument(..., help="Project name"),
    description: str = typer.Option(
        "", "--description", "-d",
        help="Project description",
    ),
) -> None:
    """Create a new Mementic project."""
    from mementic.config import ProjectEntry

    config = load_global_config()

    if name in config.projects:
        typer.echo(
            f"Project '{name}' already exists.", err=True
        )
        raise typer.Exit(code=1)

    config.projects[name] = ProjectEntry(
        description=description,
        db_path=f"~/.mementic/projects/{name}.mementic.db",
    )
    save_global_config(config)
    typer.echo(
        f"Created project '{name}'. "
        f"Database will be created on first mementic init."
    )


@project_app.command(name="list")
def list_projects() -> None:
    """List all configured projects."""
    config = load_global_config()

    if not config.projects:
        typer.echo("No projects configured.")
        raise typer.Exit()

    default = config.default_project
    for proj_name, proj_entry in config.projects.items():
        marker = "*" if proj_name == default else " "
        repo_count = len(proj_entry.repos)
        desc = f"    {proj_entry.description}" if proj_entry.description else ""
        typer.echo(f"{marker} {proj_name:<20} ({repo_count} repos){desc}")


@project_app.command()
def set_default(
    name: str = typer.Argument(..., help="Project name to set as default"),
) -> None:
    """Set the default project for cross-repo operations."""
    config = load_global_config()

    if name not in config.projects:
        available = ", ".join(sorted(config.projects))
        typer.echo(
            f"Project '{name}' not found. Available: {available}",
            err=True,
        )
        raise typer.Exit(code=1)

    config.default_project = name
    save_global_config(config)
    typer.echo(f"Default project set to '{name}'.")


if __name__ == "__main__":
    app()
