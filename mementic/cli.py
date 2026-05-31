from __future__ import annotations

import json
import subprocess
from pathlib import Path

import typer
from dotenv import load_dotenv

load_dotenv()

from mementic.config import load_config  # noqa: E402
from mementic.extraction import extract_from_session  # noqa: E402
from mementic.format import format_context_pack  # noqa: E402
from mementic.graph import GraphStore  # noqa: E402
from mementic.models import CodeEntity, DecisionStatus, EntityType  # noqa: E402
from mementic.pipeline import run_init  # noqa: E402

app = typer.Typer(help="Mementic — semantic memory for engineering teams and AI agents")

CHECKPOINT_FILE = ".mementic/checkpoint"


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
        from mementic.session import find_latest_session

        session = find_latest_session()
        if not session:
            typer.echo("No session found. Provide --session PATH.", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"Using session: {session}")

    session_text = Path(session).read_text()

    checkpoint = _load_checkpoint()
    if checkpoint:
        git_diff, rc = _run_git_diff(checkpoint)
        if rc != 0:
            typer.echo(f"Warning: git diff against checkpoint failed (rc={rc}).", err=True)
    else:
        git_diff, rc = _run_git_diff("HEAD~1")
        if rc != 0:
            typer.echo(f"Warning: git diff against HEAD~1 failed (rc={rc}).", err=True)
        else:
            typer.echo("No checkpoint found — using HEAD~1 as baseline.")

    cfg = load_config()
    extraction = extract_from_session(session_text, git_diff, notes or "", cfg.llm)

    if not extraction.decisions and not extraction.constraints:
        typer.echo("No architectural decisions or constraints found.")
        raise typer.Exit()

    accepted_d = []
    accepted_c = []

    for d in extraction.decisions:
        typer.echo(f"\nDecision: [{d.id}] {d.title}")
        typer.echo(f"  Rationale: {d.rationale}")
        if d.rejected_alternatives:
            typer.echo(f"  Rejected: {', '.join(d.rejected_alternatives)}")
        if typer.confirm("  Accept?", default=True):
            accepted_d.append(d)

    for c in extraction.constraints:
        typer.echo(f"\nConstraint: [{c.id}] {c.description}")
        if typer.confirm("  Accept?", default=True):
            accepted_c.append(c)

    if not accepted_d and not accepted_c:
        typer.echo("Nothing accepted. Graph unchanged.")
        raise typer.Exit()

    if Path(cfg.output.graph_path).exists():
        try:
            graph = GraphStore.load(cfg.output.graph_path)
        except Exception:
            typer.echo("Graph file corrupted. Starting with empty graph.", err=True)
            graph = GraphStore()
    else:
        graph = GraphStore()

    for d in accepted_d:
        graph.add_decision(d)
        for entity_path in extraction.affected_entities:
            graph.add_code_entity(
                CodeEntity(id=entity_path, type=EntityType.FILE)
            )
            graph.link(d.id, entity_path, "affects")
    for c in accepted_c:
        graph.add_constraint(c)
        for entity_path in c.bound_entities:
            graph.link(c.id, entity_path, "binds_to")
    for edge in extraction.edges_to_create:
        graph.link(edge.source, edge.target, edge.relation)

    Path(cfg.output.graph_path).parent.mkdir(parents=True, exist_ok=True)
    graph.save(cfg.output.graph_path)
    typer.echo(f"Written to {cfg.output.graph_path}")

    _save_checkpoint()


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
    depth: int = 1,
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
        choice = typer.confirm("Accept?", default=True)
        if choice:
            d.status = DecisionStatus.ACCEPTED
            graph.add_decision(d)
        else:
            d.status = DecisionStatus.SUPERSEDED
            graph.add_decision(d)

    for c in pending_c:
        typer.echo(f"\n[{c.id}] {c.description}")
        choice = typer.confirm("Accept?", default=True)
        if choice:
            c.status = "active"
            graph.add_constraint(c)
        else:
            c.status = "superseded"
            graph.add_constraint(c)

    graph.save(cfg.output.graph_path)
    typer.echo("Review complete.")
