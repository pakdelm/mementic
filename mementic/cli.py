from __future__ import annotations

import json
import re
import subprocess
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
    err_console,
    kv_table,
    make_progress,
    make_step_tracker,
    panel,
    rule,
    sanitize,
    table,
    text,
)

from mementic.config import (
    LLMConfig,
    LocalConfig,
    load_local_config,
    save_local_config,
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
from mementic.pipeline import _extract_session, run_init
from mementic.conflict import auto_resolve_capture

_CODEBASE_INDICATORS: tuple[str, ...] = (
    "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
    "Makefile", "setup.py", "setup.cfg", "CMakeLists.txt",
    "meson.build", "pom.xml", "build.gradle", "Gemfile",
    "Cargo.lock", "mix.exs", "rebar.config",
)

app = typer.Typer(help="Mementic — semantic memory for engineering teams and AI agents")


def _resolve_graph() -> GraphStore:
    """Open the local knowledge graph at .mementic/graph.db."""
    return GraphStore(db_path=Path(".mementic/graph.db"))


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
            ["git", "diff", reference], capture_output=True, text=True, encoding="utf-8"
        )
        return result.stdout or "", result.returncode
    except FileNotFoundError:
        return "", 1




@app.command()
def init(
    provider: str | None = typer.Option(
        None, "--provider",
        help="LLM provider (deepseek, openai, openrouter, ollama)",
    ),
    model: str | None = typer.Option(
        None, "--model",
        help="Model name for the provider (provider-specific default if omitted)",
    ),
    no_scan: bool = typer.Option(
        False, "--no-scan",
        help="Skip codebase structural scan",
    ),
    deep: bool = typer.Option(
        False, "--deep",
        help="Full LLM-powered extraction (requires API key)",
    ),
    key: bool = typer.Option(
        False, "--key",
        help="Set or rotate the API key in OS keyring",
    ),
) -> None:
    """Initialize the knowledge graph for this repository.

    Creates a .mementic/ directory, sets up the LLM provider, scans the
    codebase, and populates the knowledge graph.

    Provider resolution order:
    1. --provider flag (explicit)
    2. Interactive prompt (when stdin is a tty) — always shown so the user
       can choose, even if env vars are already set
    3. Environment variable detection — used as a fallback when non-interactive
       (CI, Docker SBX). Picks the first detected from: OPENROUTER_API_KEY,
       DEEPSEEK_API_KEY, OPENAI_API_KEY

    API keys are stored in the OS keyring. Use --key to set or rotate.
    """
    import typer as _typer
    from mementic.format import detect_repo_path

    cwd = Path(detect_repo_path())
    mementic_dir = Path(".mementic")

    # --- Resolve LLM config ---
    existing = load_local_config()
    llm_cfg = existing.llm if existing else None

    if llm_cfg is None or provider or model:
        # Determine provider
        resolved_provider = provider
        if not resolved_provider:
            # Preserve existing config's provider when only --model is given
            if llm_cfg is not None:
                resolved_provider = llm_cfg.provider
        if not resolved_provider:
            import os as _os

            # Detect which env vars are set (informational, for the prompt)
            env_providers: dict[str, str] = {}
            if _os.environ.get("OPENROUTER_API_KEY"):
                env_providers["openrouter"] = "OPENROUTER_API_KEY"
            if _os.environ.get("DEEPSEEK_API_KEY"):
                env_providers["deepseek"] = "DEEPSEEK_API_KEY"
            if _os.environ.get("OPENAI_API_KEY"):
                env_providers["openai"] = "OPENAI_API_KEY"

            if sys.stdin.isatty():
                console.print("\n[bold]Select LLM provider:[/]")
                for i, (name, env_var) in enumerate(
                    [("openrouter", "OPENROUTER_API_KEY"),
                     ("deepseek", "DEEPSEEK_API_KEY"),
                     ("openai", "OPENAI_API_KEY"),
                     ("ollama", None)],
                    start=1,
                ):
                    tag = f" [{C_SUCCESS}detected[/]]" if name in env_providers else ""
                    console.print(f"  [{i}] {name}{tag}")
                choice = _typer.prompt("Enter number", default="1")
                provider_map = {"1": "openrouter", "2": "deepseek", "3": "openai", "4": "ollama"}
                resolved_provider = provider_map.get(choice, "openrouter")
            elif env_providers:
                # Non-interactive with env vars: pick the first detected
                resolved_provider = next(iter(env_providers))
            else:
                console.print(
                    text(f"[{C_FAIL}]No provider specified. "
                         f"Use --provider or set an API key environment variable "
                         f"(OPENROUTER_API_KEY, DEEPSEEK_API_KEY, OPENAI_API_KEY).[/]")
                )
                raise _typer.Exit(code=1)

        # Determine model
        resolved_model = model
        if not resolved_model:
            model_defaults: dict[str, str] = {
                "openrouter": "deepseek/deepseek-v4-pro",
                "deepseek": "deepseek-v4-pro",
                "openai": "gpt-4o",
                "ollama": "llama3",
            }
            model_suggestions: dict[str, list[str]] = {
                "openrouter": [
                    "deepseek/deepseek-v4-pro",
                    "deepseek/deepseek-v4-flash",
                    "openai/gpt-4o",
                    "anthropic/claude-sonnet-4",
                ],
                "deepseek": ["deepseek-v4-pro", "deepseek-v4-flash"],
                "openai": ["gpt-4o", "gpt-4o-mini", "o3-mini"],
                "ollama": ["llama3", "codellama", "mistral"],
            }
            default_model = model_defaults.get(resolved_provider, "deepseek-v4-pro")
            if sys.stdin.isatty():
                suggestions = model_suggestions.get(resolved_provider, [default_model])
                console.print(f"\n  Popular {resolved_provider} models:")
                for s in suggestions:
                    marker = " (default)" if s == default_model else ""
                    console.print(f"    {s}{marker}")
                resolved_model = _typer.prompt(
                    "Model", default=default_model
                )
            else:
                resolved_model = default_model

        try:
            llm_cfg = LLMConfig(provider=resolved_provider, model=resolved_model)
        except ValueError as e:
            err_console.print(
                panel(text(str(e)), title="Error", border_style=C_FAIL)
            )
            raise typer.Exit(code=1)
        local_cfg = LocalConfig(
            llm=llm_cfg,
            sessions=existing.sessions if existing else None,
        )
        save_local_config(local_cfg)
        console.print(
            text(f"[{C_SUCCESS}]{STATUS_OK}[/] LLM: {resolved_provider} / {resolved_model}")
        )

    # --- API key setup ---
    if key or (
        llm_cfg.key_env_var is not None
        and llm_cfg.get_api_key() is None
        and sys.stdin.isatty()
    ):
        if llm_cfg.key_env_var is None:
            console.print(f"[{C_DIM}]Provider '{llm_cfg.provider}' does not require an API key.[/]")
        else:
            console.print(f"\n[bold]API key for {llm_cfg.provider}[/]")
            console.print("  The key will be stored in your OS keyring.")
            console.print(f"  Environment variable fallback: {llm_cfg.key_env_var}")
            api_key = _typer.prompt("Enter API key", hide_input=True)
            try:
                llm_cfg.set_api_key(api_key)
                console.print(text(f"[{C_SUCCESS}]{STATUS_OK}[/] API key stored in keyring."))
            except RuntimeError as e:
                console.print(text(f"[{C_WARN}]Could not store in keyring: {e}[/]"))
                console.print(f"  Set {llm_cfg.key_env_var} environment variable instead.")

    # --- Auto-detect repo ---
    effective_repo: str | None = None
    if not no_scan:
        for indicator in _CODEBASE_INDICATORS:
            if (cwd / indicator).is_file():
                effective_repo = str(cwd)
                break

    # --- Warn on re-init ---
    db_path = str(Path(".mementic/graph.db").resolve())
    if Path(db_path).exists():
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            cur = conn.execute(
                "SELECT COUNT(*) FROM decisions UNION ALL "
                "SELECT COUNT(*) FROM constraints"
            )
            rows = cur.fetchall()
            conn.close()
            d_count = rows[0][0] if len(rows) > 0 else 0
            c_count = rows[1][0] if len(rows) > 1 else 0
        except Exception:
            d_count = c_count = 0

        if d_count > 0 or c_count > 0:
            console.print(
                panel(
                    text(
                        f"Graph already contains {d_count} decision(s), "
                        f"{c_count} constraint(s).\n"
                        f"[{C_DIM}]Re-initializing will re-scan sources. "
                        f"Existing entries will not be overwritten.[/]"
                    ),
                    title="Re-initialize?",
                    border_style=C_WARN,
                )
            )
            if sys.stdin.isatty():
                if not typer.confirm(
                    "Continue?",
                    default=False,
                ):
                    typer.echo("Init cancelled.")
                    raise typer.Exit(code=0)
            # Non-interactive mode: proceed silently (CI/automation)

    if not deep:
        console.print(
            panel(
                text(
                    f"Running deterministic init (fast, no LLM).\n"
                    f"[{C_DIM}]Use --deep for full LLM extraction "
                    f"(requires API key).[/]"
                ),
                title="Note",
                border_style=C_WARN,
            )
        )

    # --- Run init pipeline ---
    mementic_dir.mkdir(exist_ok=True)

    try:
        with make_progress() as progress:
            task_id = progress.add_task(
                "Initializing knowledge graph...",
                total=None,
            )
            advance = make_step_tracker(progress, task_id, preamble="")
            graph = run_init(
                repo_path=effective_repo,
                no_scan=no_scan,
                db_path=db_path,
                llm_cfg=llm_cfg,
                advance=advance,
                deep=deep,
            )
    except RuntimeError as e:
        err_console.print(
            panel(text(str(e)), title="Error", border_style=C_FAIL)
        )
        raise typer.Exit(code=1)

    # --- Post-bootstrap summary ---
    stats = graph.stats()
    entity_count = stats.get("entities", 0)

    # Graph summary
    g_table = kv_table()
    g_table.add_column("Label", style="bold", width=20)
    g_table.add_column("Value", style=C_DIM)
    g_table.add_row("Decisions", str(stats.get("decisions", 0)))
    g_table.add_row("Constraints", str(stats.get("constraints", 0)))
    g_table.add_row("Entities", str(entity_count))
    g_table.add_row("Edges", str(stats.get("edges", 0)))

    # Health table
    from mementic.health import check_health

    items = check_health(graph, repo_path=cwd)

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
            text(f"[{C_HEADER} bold]Graph[/]"),
            g_table,
            rule(),
            text(f"[{C_HEADER} bold]Status ({ok_count}/{len(items)})[/]"),
            health_table,
            rule(),
            text(f"[{C_DIM} italic]Next: mementic capture[/]"),
            title="Mementic initialized",
        )
    )

    # --- Inject enforcement template ---
    from mementic.inject import inject_enforcement, detect_agents, has_enforcement

    agents_md_path = cwd / "AGENTS.md"
    if not has_enforcement(agents_md_path):
        detected = detect_agents(cwd)
        if detected:
            console.print()
            if sys.stdin.isatty():
                inject_ok = typer.confirm(
                    "No enforcement instructions found. Add to AGENTS.md?",
                    default=True,
                )
                if inject_ok:
                    result = inject_enforcement(agents_md_path)
                    console.print(text(f"  [{C_SUCCESS}]{result}[/]"))
                else:
                    console.print(
                        text(
                            f"  [{C_WARN}]Warning: Without enforcement, "
                            f"mementic operates as CLI-only.[/]\n"
                            f"  [{C_DIM}]Agents won't automatically query "
                            f"the graph. Run 'mementic init' to retry.[/]"
                        )
                    )
            else:
                console.print(
                    text(
                        f"  [{C_DIM}]No enforcement instructions found. "
                        f"Run 'mementic init' to add them.[/]"
                    )
                )
        else:
            # Always inject enforcement even without detected agents
            inject_enforcement(agents_md_path)

    # Quality notice for deterministic mode
    existing_cfg = load_local_config()
    if existing_cfg and existing_cfg.llm.get_api_key() is None:
        console.print(
            panel(
                text(
                    f"Running without LLM API key — deterministic extraction only.\n"
                    f"[{C_DIM}]Graph quality is reduced. Set a provider key "
                    f"for richer extraction.[/]"
                ),
                title="Note",
                border_style=C_WARN,
            )
        )


@app.command()
def capture(
    session: str | None = None,
    notes: str | None = None,
    agent: str | None = None,
    tail: int = typer.Option(
        50, "--tail",
        help="Max messages from session tail (0 = skip session, use diff+notes only)",
    ),
    keyword: list[str] = typer.Option(
        [], "--keyword", "-k",
        help="Custom architecture keywords to scan for (repeatable). "
             "Merged with built-in keywords for session windowing.",
    ),
) -> None:
    """Extract architectural decisions from an agent session.

    Decisions are auto-resolved against the existing graph using
    three deterministic rules (new/duplicate/update). No interactive prompts.
    """
    from mementic.session import get_registry

    registry = get_registry()
    local_cfg = load_local_config()
    llm_cfg: LLMConfig = local_cfg.llm if local_cfg else LLMConfig(provider="ollama", model="llama3")
    detected_agent: str | None = None

    if not session and not agent:
        # Auto-detect: find most recent session across all providers
        log_dir_overrides: dict[str, list[str]] = {}
        if local_cfg and local_cfg.sessions:
            for name in ("opencode", "claude-code", "generic", "cursor", "windsurf"):
                agent_cfg = getattr(local_cfg.sessions, name.replace("-", "_"))
                if agent_cfg.log_dirs:
                    log_dir_overrides[name] = agent_cfg.log_dirs
        provider, detected = registry.detect(log_dirs=log_dir_overrides or None)
        if not detected:
            typer.echo("No session found. Provide --session PATH.", err=True)
            raise typer.Exit(code=1)
        session = detected
        detected_agent = provider.agent_name
        console.print(
            text(f"[{C_ID}]{detected_agent}[/] [{C_DIM}]session: {session}[/]")
        )
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
        console.print(
            text(f"[{C_ID}]{detected_agent}[/] [{C_DIM}]session: {session}[/]")
        )
    elif session:
        # Explicit session path, detect which provider can read it
        provider, _ = registry.detect(session_path=session)
        detected_agent = provider.agent_name
        console.print(
            text(f"[{C_ID}]{detected_agent}[/] [{C_DIM}]session: {session}[/]")
        )
    else:
        typer.echo("No session found. Provide --session PATH.", err=True)
        raise typer.Exit(code=1)

    session_text = Path(session).read_text(encoding="utf-8", errors="replace")

    # Open graph for checkpoint query
    graph = _resolve_graph()
    repo_path = str(Path.cwd())

    # Compute git diff — incremental via SQLite checkpoint
    checkpoint = graph.get_capture_checkpoint(repo_path)
    got_diff = False
    if checkpoint:
        # Check if there are new commits since last capture
        try:
            head_result = subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                encoding="utf-8",
            )
        except FileNotFoundError:
            head_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="")
        if head_result.returncode == 0 and checkpoint.strip() == head_result.stdout.strip():
            console.print(
                text(f"[{C_DIM}]No new commits since last capture.[/]")
            )
            git_diff = ""
        else:
            git_diff, rc = _run_git_diff(checkpoint)
            if rc == 1:
                console.print(
                    text(f"[{C_WARN}]git not found. Proceeding without diff.[/]")
                )
                git_diff = ""
            elif rc == 128:
                typer.echo("Not a git repository. Skipping diff.", err=True)
                git_diff = ""
            elif rc == 0:
                got_diff = True
                if not git_diff.strip():
                    console.print(
                        text(f"[{C_DIM}]No changes since last capture.[/]")
                    )
            else:
                typer.echo(
                    "Warning: git diff failed. Proceeding without diff.",
                    err=True,
                )
                git_diff = ""
    else:
        git_diff, rc = _run_git_diff("HEAD~1")
        if rc == 1:
            console.print(
                text(f"[{C_WARN}]git not found. Proceeding without diff.[/]")
            )
            git_diff = ""
        elif rc == 128:
            typer.echo("Not a git repository. Skipping diff.", err=True)
            git_diff = ""
        elif rc == 0:
            got_diff = True
            if not git_diff.strip():
                console.print(
                    text(f"[{C_DIM}]No changes since last capture.[/]")
                )
        else:
            typer.echo(
                "Warning: git diff failed. Proceeding without diff.",
                err=True,
            )
            git_diff = ""

    try:
        with console.status(
            f"[{C_HEADER}]Extracting architectural decisions...[/]"
        ):
            extraction = _extract_session(
                session_text, git_diff, notes or "", llm_cfg,
                max_messages=tail, custom_keywords=keyword or None,
            )
    except RuntimeError as e:
        err_console.print(
            panel(text(str(e)), title="Error", border_style=C_FAIL)
        )
        raise typer.Exit(code=1)

    if not extraction.decisions and not extraction.constraints:
        console.print(
            text(f"[{C_DIM}]No architectural decisions or constraints found.[/]")
        )
        raise typer.Exit()

    # Replace LLM-generated IDs (dec-001, etc.) with deterministic slugs
    for d in extraction.decisions:
        d.id = slugify(d.title, max_length=DECISION_ID_MAX_LENGTH)
    for c in extraction.constraints:
        c.id = slugify(c.description, max_length=CONSTRAINT_ID_MAX_LENGTH)

    counts = auto_resolve_capture(
        graph, extraction.decisions, extraction.constraints
    )

    # Add only NEW and UPDATED decisions (skipped are not added).
    # Track disposition per decision for output listing.
    decision_dispositions: dict[str, str] = {}
    for d in extraction.decisions:
        if not graph.has_node(d.id) or d.status == DecisionStatus.ACCEPTED:
            if graph.has_node(d.id):
                # Rule 3: existing Decision is deprecated; overwrite with accepted
                decision_dispositions[d.id] = "updated"
                graph.add_decision(d)
            else:
                # Rule 1: new node
                decision_dispositions[d.id] = "new"
                graph.add_decision(d)
        else:
            # Rule 2 (skipped): don't add
            decision_dispositions[d.id] = "skipped"

        for entity_path in extraction.affected_entities:
            graph.add_code_entity(
                CodeEntity(id=entity_path, type=EntityType.FILE)
            )
            graph.link(d.id, entity_path, "affects")

    constraint_dispositions: dict[str, str] = {}
    for c in extraction.constraints:
        existing = graph.has_node(c.id)
        if not existing:
            constraint_dispositions[c.id] = "new"
            graph.add_constraint(c)
        elif c.status == "active":
            # Updated constraint: existing was deprecated; add_constraint handles update
            constraint_dispositions[c.id] = "updated"
            graph.add_constraint(c)
        else:
            # Skipped: don't add
            constraint_dispositions[c.id] = "skipped"

        for entity_path in c.bound_entities:
            graph.link(c.id, entity_path, "binds_to")
    for edge in extraction.edges_to_create:
        graph.link(edge.source, edge.target, edge.relation)

    # F6 — Summary output
    result_table = kv_table()
    result_table.add_column("", style="bold", width=15)
    result_table.add_column("", width=60)
    result_table.add_row(
        "New",
        text(
            f"[{C_SUCCESS}]{counts['new_decisions']} decisions, "
            f"{counts['new_constraints']} constraints[/]"
        ),
    )
    if counts["updated_decisions"] > 0:
        result_table.add_row(
            "Updated",
            text(
                f"[{C_SUCCESS}]{counts['updated_decisions']} decisions[/] "
                f"[{C_DIM}](existing deprecated)[/]"
            ),
        )
    if counts["skipped_decisions"] > 0 or counts["skipped_constraints"] > 0:
        total_skipped = (
            counts["skipped_decisions"] + counts["skipped_constraints"]
        )
        result_table.add_row(
            "Skipped",
            text(f"[{C_WARN}]{total_skipped} duplicates[/]"),
        )
    result_table.add_row(
        "Written", text(f"[{C_DIM}].mementic/graph.db[/]")
    )
    # Build the panel content: summary table + optional lists of what was extracted
    panel_content: list = [result_table]

    # Decisions list
    if extraction.decisions:
        panel_content.append(rule())
        d_title_text = text(
            f"Decisions ({len(extraction.decisions)})", style=f"bold {C_HEADER}"
        )
        d_table = table(show_header=False, show_edge=False, padding=(0, 1))
        d_table.add_column("", width=4)
        d_table.add_column("Title", overflow="fold")
        d_table.add_column("ID", style=C_DIM, width=40)
        for d in extraction.decisions:
            disp = decision_dispositions.get(d.id, "new")
            if disp == "new":
                icon = f"[{C_SUCCESS}]{STATUS_OK}[/]"
                suffix = ""
            elif disp == "updated":
                icon = f"[{C_WARN}]{STATUS_DEPRECATED}[/]"
                suffix = f" [{C_DIM}](updated)[/]"
            else:
                icon = f"[{C_DIM}]~[/]"
                suffix = f" [{C_DIM}](duplicate)[/]"
            d_table.add_row(icon, f"[bold]{d.title}[/]{suffix}", d.id)
        panel_content.extend([d_title_text, d_table])

    # Constraints list
    if extraction.constraints:
        panel_content.append(rule())
        c_title_text = text(
            f"Constraints ({len(extraction.constraints)})",
            style=f"bold {C_HEADER}",
        )
        c_table = table(show_header=False, show_edge=False, padding=(0, 1))
        c_table.add_column("", width=4)
        c_table.add_column("Description", overflow="fold")
        c_table.add_column("ID", style=C_DIM, width=40)
        for c in extraction.constraints:
            disp = constraint_dispositions.get(c.id, "new")
            if disp == "new":
                icon = f"[{C_SUCCESS}]{STATUS_OK}[/]"
                suffix = ""
            elif disp == "updated":
                icon = f"[{C_WARN}]{STATUS_DEPRECATED}[/]"
                suffix = f" [{C_DIM}](updated)[/]"
            else:
                icon = f"[{C_DIM}]~[/]"
                suffix = f" [{C_DIM}](duplicate)[/]"
            c_table.add_row(icon, f"[bold]{c.description}[/]{suffix}", c.id)
        panel_content.extend([c_title_text, c_table])

    console.print(panel(*panel_content, title="Capture complete"))

    if got_diff:
        try:
            head_result = subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                encoding="utf-8",
            )
            if head_result.returncode == 0:
                graph.set_capture_checkpoint(
                    repo_path, head_result.stdout.strip()
                )
        except FileNotFoundError:
            pass  # git not available — skip


query_app = typer.Typer(help="Search and retrieve from the knowledge graph")
app.add_typer(query_app, name="query")


@query_app.command()
def keywords(
    keywords: list[str] = typer.Argument(..., help="Keywords for matching (e.g. auth login session pydantic)"),
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
        typer.echo("No audit log found. Run init or capture first.")
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
    except typer.Exit:
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
    rationale: str = typer.Option(..., "--rationale", "-r", help="Justification for the decision"),
    entities: list[str] | None = typer.Option(None, "--entity", "-e", help="Affected file paths (repeatable)"),
    rejected: list[str] | None = typer.Option(None, "--rejected", help="Rejected alternatives (repeatable)"),
) -> None:
    """Record a new architectural decision."""
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
    graph.add_decision(d)
    for ep in (entities or []):
        clean = re.sub(r"::[A-Za-z_]\w*$", "", ep)
        clean = re.sub(r":[A-Za-z_]\w*$", "", clean)
        e = CodeEntity(id=clean, type=EntityType.FILE, repo=d.repo)
        graph.add_code_entity(e)
        graph.link(d.id, clean, "affects")
    console.print(
        panel(
            text(
                f"Title:  [{C_HEADER}]{title}[/]\n"
                f"ID:     [{C_ID}]{d.id}[/]\n"
                f"Status: [{C_SUCCESS}]accepted[/]"
            ),
            title="Decision added",
        )
    )


@add_app.command()
def constraint(
    description: str = typer.Argument(..., help="Constraint description"),
    entities: list[str] = typer.Option(..., "--entity", "-e", help="Bound entity paths (repeatable, required)"),
    decision_id: str | None = typer.Option(None, "--decision-id", "-d", help="Enforcing decision ID"),
    rule_id: str | None = typer.Option(None, "--id", help="Explicit rule ID (auto-generated if omitted)"),
) -> None:
    """Record a new constraint."""
    graph = _resolve_graph()
    now = timestamp()
    cid = slugify(rule_id, max_length=CONSTRAINT_ID_MAX_LENGTH) if rule_id else slugify(description, max_length=CONSTRAINT_ID_MAX_LENGTH)
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
    graph.add_constraint(c)
    for ep in entities:
        clean = re.sub(r"::[A-Za-z_]\w*$", "", ep)
        clean = re.sub(r":[A-Za-z_]\w*$", "", clean)
        e = CodeEntity(id=clean, type=EntityType.FILE, repo=c.repo)
        graph.add_code_entity(e)
        graph.link(c.id, clean, "binds_to")
    bound_list = ", ".join(entities[:5])
    if len(entities) > 5:
        bound_list += f" +{len(entities) - 5} more"
    console.print(
        panel(
            text(
                f"ID:       [{C_ID}]{c.id}[/]\n"
                f"Bound to: [{C_PATH}]{bound_list}[/]\n"
                f"Desc:     [{C_DIM}]{description}[/]"
            ),
            title="Constraint added",
        )
    )


if __name__ == "__main__":
    app()
