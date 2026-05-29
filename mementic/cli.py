from __future__ import annotations

import subprocess
from pathlib import Path

import typer
from dotenv import load_dotenv

load_dotenv()

from mementic.config import load_config  # noqa: E402
from mementic.extraction import extract_from_session  # noqa: E402
from mementic.format import format_context_pack  # noqa: E402
from mementic.graph import GraphStore  # noqa: E402
from mementic.models import DecisionStatus  # noqa: E402
from mementic.pipeline import run_init  # noqa: E402

app = typer.Typer(help="Mementic — semantic memory for engineering teams and AI agents")


@app.command()
def init(config_path: str = ".mementic.json") -> None:
    """Initialize the knowledge graph from configured sources."""
    graph = run_init(config_path)
    stats = graph.stats()
    typer.echo(f"Mementic graph initialized: {stats}")


@app.command()
def capture(
    session: str | None = None,
    notes: str | None = None,
) -> None:
    """Extract architectural decisions from an agent session."""
    if not session:
        typer.echo("Error: --session PATH is required for capture in MVP", err=True)
        raise typer.Exit(code=1)

    session_text = Path(session).read_text()

    result = subprocess.run(
        ["git", "diff", "HEAD~1"], capture_output=True, text=True
    )
    git_diff = result.stdout or ""

    cfg = load_config()
    extraction = extract_from_session(session_text, git_diff, notes or "", cfg.llm)

    typer.echo("\nProposed decisions:\n")
    for d in extraction.decisions:
        typer.echo(f"  [{d.id}] {d.title}")
        typer.echo(f"  Rationale: {d.rationale}")
        if d.rejected_alternatives:
            typer.echo(f"  Rejected: {', '.join(d.rejected_alternatives)}")
        typer.echo()

    typer.echo("Proposed constraints:\n")
    for c in extraction.constraints:
        typer.echo(f"  [{c.id}] {c.description}")
        typer.echo()

    if not extraction.decisions and not extraction.constraints:
        typer.echo("No architectural decisions or constraints found.")
        raise typer.Exit()

    accept = typer.confirm("Accept all and write to graph?", default=True)
    if accept:
        try:
            graph = GraphStore.load(cfg.output.graph_path)
        except (FileNotFoundError, Exception):
            graph = GraphStore()

        for d in extraction.decisions:
            graph.add_decision(d)
        for c in extraction.constraints:
            graph.add_constraint(c)

        Path(cfg.output.graph_path).parent.mkdir(parents=True, exist_ok=True)
        graph.save(cfg.output.graph_path)
        typer.echo(f"Written to {cfg.output.graph_path}")


@app.command()
def enforce(task: str, files: list[str]) -> None:
    """Load constraints and format a context pack for a given task."""
    cfg = load_config()

    try:
        graph = GraphStore.load(cfg.output.graph_path)
    except (FileNotFoundError, Exception):
        typer.echo("No graph found. Run 'mementic init' first.", err=True)
        raise typer.Exit(code=1)

    constraints = graph.get_constraints_for_entities(files)
    decisions = graph.get_decisions_for_entities(files)

    output = format_context_pack(task, decisions, constraints)
    typer.echo(output)


@app.command()
def query(
    entity: str | None = None,
    decision: str | None = None,
    constraint: str | None = None,
    search: str | None = None,
) -> None:
    """Search decisions, constraints, and entities in the graph."""
    cfg = load_config()

    try:
        graph = GraphStore.load(cfg.output.graph_path)
    except (FileNotFoundError, Exception):
        typer.echo("No graph found. Run 'mementic init' first.", err=True)
        raise typer.Exit(code=1)

    if entity:
        decisions = graph.get_evolution(entity)
        typer.echo(f"Evolution for {entity}:\n")
        for d in decisions:
            typer.echo(f"  [{d.id}] {d.title} ({d.timestamp})")
            typer.echo(f"  {d.rationale}\n")
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
def server():
    """Start the MCP server for agent integration (stdio transport)."""
    import sys
    from mementic.server import mcp
    sys.stderr.write("Mementic MCP server starting (stdio transport)...\n")
    mcp.run(transport="stdio")


@app.command()
def status() -> None:
    """Show graph statistics."""
    cfg = load_config()

    try:
        graph = GraphStore.load(cfg.output.graph_path)
        stats = graph.stats()
        typer.echo(f"Decisions: {stats.get('decisions', 0)}")
        typer.echo(f"Constraints: {stats.get('constraints', 0)}")
        typer.echo(f"Code Entities: {stats.get('entities', 0)}")
        typer.echo(f"Edges: {stats.get('edges', 0)}")
    except (FileNotFoundError, Exception):
        typer.echo("No graph found. Run 'mementic init' first.")


@app.command()
def review() -> None:
    """Review pending proposed decisions."""
    cfg = load_config()

    try:
        graph = GraphStore.load(cfg.output.graph_path)
    except (FileNotFoundError, Exception):
        typer.echo("No graph found.", err=True)
        raise typer.Exit(code=1)

    pending_d = graph.get_pending_decisions()
    pending_c = graph.get_pending_constraints()

    if not pending_d and not pending_c:
        typer.echo("No pending proposals.")
        return

    for d in pending_d:
        typer.echo(f"\n[{d.id}] {d.title}")
        typer.echo(f"  Rationale: {d.rationale}")
        if typer.confirm("Accept?", default=True):
            d.status = DecisionStatus.ACCEPTED
            graph.add_decision(d)

    graph.save(cfg.output.graph_path)
    typer.echo("Review complete.")
