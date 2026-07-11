from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, TYPE_CHECKING

import typer
from pydantic import ValidationError
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
    table,
    text,
)

from mementic.config import (
    PROVIDER_CONFIGS,
    GLOBAL_CONFIG_DISPLAY_PATH,
    GlobalConfig,
    ProjectEntry,
    RepoEntry,
    Sources,
    load_config,
    load_global_config,
    resolve_project,
    save_global_config,
)
from mementic.format import (
    CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
    CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
    DECISION_ID_MAX_LENGTH,
    CONSTRAINT_ID_MAX_LENGTH,
    detect_repo_path,
    format_evolution,
    format_memory_context,
    resolve_repo_name,
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
if TYPE_CHECKING:
    from mementic.config import MementicConfig  # noqa: F401

_CODEBASE_INDICATORS: tuple[str, ...] = (
    "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
    "Makefile", "setup.py", "setup.cfg", "CMakeLists.txt",
    "meson.build", "pom.xml", "build.gradle", "Gemfile",
    "Cargo.lock", "mix.exs", "rebar.config",
)

# Agent name → SessionsConfig field name (hyphenated agent names use
# underscore field names — Claude Code is "claude-code" but the config
# field is claude_code via validation_alias).
_SESSION_PROVIDER_IMPORTS: dict[str, str] = {
    "opencode": "mementic.session.opencode",
    "claude-code": "mementic.session.claude_code",
    "cursor": "mementic.session.cursor",
    "windsurf": "mementic.session.windsurf",
}
_SESSION_FIELD_NAMES: dict[str, str] = {
    "opencode": "opencode",
    "claude-code": "claude_code",
    "cursor": "cursor",
    "windsurf": "windsurf",
}


def _populate_session_defaults(global_cfg: Any) -> None:
    """Populate session log_dirs from each provider's platform defaults.

    Only writes when the config currently has empty log_dirs for a given
    agent — preserves user overrides on subsequent ``mementic init`` runs.
    """
    import importlib
    system = sys.platform  # "win32" / "linux" / "darwin" — matches DEFAULT_*_PATHS keys
    updated = False

    for agent_name, module_name in _SESSION_PROVIDER_IMPORTS.items():
        field = _SESSION_FIELD_NAMES.get(agent_name, agent_name.replace("-", "_"))
        agent_cfg = getattr(global_cfg.sessions, field, None)
        if agent_cfg is None or agent_cfg.log_dirs:
            continue  # already has user-configured paths

        try:
            mod = importlib.import_module(module_name)
        except ImportError:
            continue

        # Each provider module exports DEFAULT_*_PATHS: dict[platform, list[str]]
        for attr_name in dir(mod):
            if attr_name.startswith("DEFAULT_") and attr_name.endswith("_PATHS"):
                default_paths = getattr(mod, attr_name, None)
                if isinstance(default_paths, dict):
                    paths = default_paths.get(system, default_paths.get("linux", []))
                    if paths:
                        agent_cfg.log_dirs = list(paths)
                        updated = True
                    break

    if updated:
        from mementic.config import save_global_config
        save_global_config(global_cfg)

app = typer.Typer(help="Mementic — semantic memory for engineering teams and AI agents")


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

    Tries to resolve a project from the global config. When no projects are
    configured (first run, or no ``~/.mementic/config.json`` on disk), falls
    back to the legacy ``graph.json`` path.

    Args:
        project: Optional project name from ``--project`` flag.

    Returns:
        A ``(GraphStore, MementicConfig)`` tuple.

    Raises:
        typer.Exit: When an explicit ``--project`` is not found.
    """
    cfg = load_config(repo_path=detect_repo_path())

    try:
        proj_name, global_cfg = resolve_project(project)
        proj_entry = global_cfg.projects[proj_name]
        db_path = Path(proj_entry.db_path).expanduser()
        return GraphStore(db_path=db_path), cfg
    except ValueError as e:
        if project is not None:
            # Explicit --project that doesn't exist
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1)
        # No projects configured — fall back to legacy graph.json

    return _load_graph_safe(cfg.output.graph_path), cfg


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


def _save_graph_cli(graph: GraphStore, cfg: "MementicConfig") -> None:
    """Save graph to disk only when using legacy JSON mode (not SQLite)."""
    if graph.is_persistent:
        return
    graph.save(cfg.output.graph_path)


@app.command()
def init(
    repo: str | None = typer.Option(
        None, "--repo",
        help="Path to repository root for codebase structural scan",
    ),
    no_scan: bool = typer.Option(
        False, "--no-scan",
        help="Skip codebase structural scan (use configured sources only)",
    ),
    deep: bool = typer.Option(
        False, "--deep",
        help="Full LLM-powered extraction (requires API key). "
             "Without --deep, init runs deterministically in seconds.",
    ),
    interactive: bool = typer.Option(
        False, "--interactive", help="Walk through setup interactively"
    ),
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name for project-aware init",
    ),
    key: bool = typer.Option(
        False, "--key",
        help="Set or rotate the API key. Opens an interactive prompt to "
             "select a provider and enter the key (stored in OS keyring).",
    ),
) -> None:
    """Initialize the knowledge graph from configured sources.

    Auto-discovers ``./docs/adr/``, ``./docs/instructions/``,
    ``.cursor/instructions/`` and ``.windsurf/instructions/`` directories
    and registers the repo in the global config at ``~/.mementic/config.json``.

    With --repo, scans the codebase structure for architectural patterns
    before processing configured sources. Use --no-scan to skip.

    API keys are stored in the OS keyring (Windows Credential Locker,
    macOS Keychain, Linux Secret Service). Use --key to select a provider
    and set or rotate the key interactively.
    """
    cwd = Path(detect_repo_path())
    cwd_resolved = str(cwd.resolve())

    try:
        global_cfg = load_global_config()
    except ValueError as e:
        # load_global_config rejects empty projects dicts to catch manual
        # editing mistakes.  In init() we recover by creating a fresh project.
        typer.echo(f"Warning: {e}", err=True)
        global_cfg = GlobalConfig()

    already_registered = False
    for _proj_name, proj_entry in global_cfg.projects.items():
        for repo_entry in proj_entry.repos.values():
            try:
                if Path(repo_entry.path).resolve() == Path(cwd_resolved):
                    already_registered = True
                    break
            except (OSError, ValueError):
                continue
        if already_registered:
            break

    if not already_registered:
        # Register with empty sources — pipeline Phase 0 handles markdown scanning.
        discovered_sources = Sources()

        if not any(
            (
                discovered_sources.adr_dirs,
                discovered_sources.opencode_instructions,
            )
        ):
            console.print(
                text(
                    f"[{C_WARN}]No pre-configured sources. All markdown "
                    f"files will be scanned during init.[/]"
                )
            )

        # Detect codebase-only repos (relax: register with empty sources)
        has_codebase = any((cwd / ind).is_file() for ind in _CODEBASE_INDICATORS)

        if not any(
            (
                discovered_sources.adr_dirs,
                discovered_sources.opencode_instructions,
                discovered_sources.claude_instructions,
                discovered_sources.cursor_instructions,
                discovered_sources.windsurf_instructions,
            )
        ) and not has_codebase:
            typer.echo(
                "No sources discovered. Graph will start empty — use "
                "inline proposals during sessions to populate it."
            )

        if not global_cfg.projects:
            global_cfg.projects["main"] = ProjectEntry(
                db_path="~/.mementic/projects/main.mementic.db",
            )
            global_cfg.default_project = "main"
        proj_name = global_cfg.default_project
        proj_entry = global_cfg.projects[proj_name]

        repo_name = cwd.name
        proj_entry.repos[cwd_resolved] = RepoEntry(
            path=cwd_resolved,
            label=repo_name,
            sources=discovered_sources,
        )
        save_global_config(global_cfg)
        console.print(
            text(
                f"[{C_SUCCESS}]{STATUS_OK}[/] Registered repo "
                f"'{repo_name}' in project '{proj_name}'."
            )
        )

    # Auto-detect repo if not explicitly given
    effective_repo = repo
    if effective_repo is None and not no_scan:
        for indicator in _CODEBASE_INDICATORS:
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
    else:
        # Use default project's db_path so init writes to the project DB
        try:
            project_name, project_global_cfg = resolve_project(None)
            proj_entry = project_global_cfg.projects[project_name]
            project_db_path = str(Path(proj_entry.db_path).expanduser())
        except ValueError:
            project_db_path = None

    # API key setup via OS keyring
    global_cfg = load_global_config()
    api_key = global_cfg.llm.get_api_key()

    if key:
        # --key flag: select provider and set/rotate key interactively
        if not sys.stdin.isatty():
            err_console.print(
                panel(
                    text(
                        "Error: --key requires an interactive terminal.\n"
                        "  Run 'mementic init' without --key for guided setup, or\n"
                        "  set the environment variable for your provider:\n"
                        "    export DEEPSEEK_API_KEY=\"your-key-here\"\n"
                        "    export OPENAI_API_KEY=\"your-key-here\"\n"
                        "    export OPENROUTER_API_KEY=\"your-key-here\""
                    ),
                    title="Error",
                    border_style=C_FAIL,
                )
            )
            raise typer.Exit(code=1)

        console.print(
            text(
                f"\n[{C_HEADER} bold]Select an LLM provider[/]\n"
                f"  {', '.join(PROVIDER_CONFIGS.keys())}\n"
                f"  [{C_DIM}]Press Enter to keep current "
                f"({global_cfg.llm.provider})[/]"
            )
        )
        provider = typer.prompt("Provider", default=global_cfg.llm.provider).strip()

        if provider not in PROVIDER_CONFIGS:
            err_console.print(
                text(
                    f"[{C_FAIL}]Error: Unknown provider '{provider}'.\n"
                    f"  Known: {', '.join(PROVIDER_CONFIGS.keys())}[/]"
                )
            )
            raise typer.Exit(code=1)

        provider_cfg = PROVIDER_CONFIGS[provider]
        env_var = provider_cfg["key_env_var"]

        if env_var is None:
            global_cfg.llm.provider = provider
            console.print(
                text(
                    f"[{C_SUCCESS}]{STATUS_OK}[/] Ollama runs locally "
                    f"— no API key needed.\n"
                    f"  [{C_DIM}]Make sure ollama is running on "
                    f"localhost:11434[/]"
                )
            )
            save_global_config(global_cfg)
            typer.echo(f"Saved to {GLOBAL_CONFIG_DISPLAY_PATH}")
        else:
            key_val = typer.prompt(
                f"API key for {provider} ({env_var})",
                hide_input=True,
                show_default=False,
            )
            if not key_val.strip():
                console.print(
                    text(f"[{C_DIM}]No key entered — no changes made.[/]")
                )
            else:
                global_cfg.llm.provider = provider
                try:
                    global_cfg.llm.set_api_key(key_val.strip())
                except RuntimeError as e:
                    typer.echo(str(e), err=True)
                    raise typer.Exit(code=1)

                console.print(
                    text(
                        f"[{C_SUCCESS}]{STATUS_OK}[/] Key stored in OS keyring.\n"
                        f"  [{C_DIM}]Fallback: export {env_var}=...[/]"
                    )
                )
                save_global_config(global_cfg)
                typer.echo(f"Saved to {GLOBAL_CONFIG_DISPLAY_PATH}")

        return
    elif not api_key and sys.stdin.isatty():
        # No key found — offer to set one up
        console.print(
            text(
                f"\n[{C_HEADER}]Mementic can extract richer architectural "
                f"context using an LLM.[/]\n"
                f"[{C_DIM}]Deterministic mode (no key) works but produces "
                f"lower-quality results.[/]\n"
            )
        )
        setup_key = typer.confirm("Set up an LLM provider now?", default=True)

        if setup_key:
            console.print(
                text(
                    f"\n[{C_HEADER} bold]Select an LLM provider[/]\n"
                    f"  {', '.join(PROVIDER_CONFIGS.keys())}"
                )
            )
            provider = typer.prompt(
                "Provider", default="", show_default=False
            )

            if provider not in PROVIDER_CONFIGS:
                typer.echo(
                    f"Unknown provider '{provider}'. Skipping LLM setup."
                )
            else:
                provider_cfg = PROVIDER_CONFIGS[provider]
                env_var = provider_cfg["key_env_var"]

                if env_var is None:
                    # ollama — no key needed
                    console.print(
                        text(
                            f"[{C_SUCCESS}]{STATUS_OK}[/] Ollama runs locally "
                            f"— no API key needed.\n"
                            f"  [{C_DIM}]Make sure ollama is running on "
                            f"localhost:11434[/]"
                        )
                    )
                else:
                    key_val = typer.prompt(
                        f"API key for {provider} ({env_var})",
                        hide_input=True,
                        show_default=False,
                    )
                    if key_val.strip():
                        # Store in OS keyring (primary)
                        global_cfg.llm.provider = provider
                        try:
                            global_cfg.llm.set_api_key(key_val.strip())
                        except RuntimeError as e:
                            typer.echo(str(e), err=True)
                            raise typer.Exit(code=1)
                        console.print(
                            text(
                                f"[{C_SUCCESS}]{STATUS_OK}[/] Key stored in "
                                f"OS keyring.\n"
                                f"  [{C_DIM}]Fallback: export {env_var}=...[/]"
                            )
                        )

                default_model = ""  # user must choose
                model = typer.prompt(
                    f"Model for {provider}", default=default_model
                )
                global_cfg.llm.provider = provider
                global_cfg.llm.model = model
                global_cfg.llm.extraction_model = model
                save_global_config(global_cfg)
                typer.echo(f"Saved to {GLOBAL_CONFIG_DISPLAY_PATH}")

    # Populate session log_dirs from provider defaults
    # Ensures the config shows default paths for all supported agents so
    # users can see and override them.  Only writes when config currently
    # has empty log_dirs (preserves user overrides on subsequent runs).
    _populate_session_defaults(global_cfg)

    # Compute tick total for progress bar.
    # Pipeline calls advance() per sub-step — count actual work items,
    # not abstract phases, so the bar tracks real progress.
    phase_cfg = load_config(repo_path=str(cwd), project=project)
    tick_total = 0

    if effective_repo and not no_scan:
        if deep:
            tick_total += 2  # _run_codebase_scan: I/O + LLM (2 ticks)
        try:
            md_count = sum(
                1 for _ in Path(effective_repo).rglob("*.md")
                if ".mementic" not in _.parts and "node_modules" not in _.parts
            )
            md_count = max(md_count, 1)
            if deep:
                # LLM mode: pipeline advances per batch (~10 files per batch)
                tick_total += max(1, (md_count + 9) // 10)
            else:
                # Deterministic: pipeline advances per file
                tick_total += md_count
        except (OSError, ValueError):
            tick_total += 10 if deep else 5
    tick_total += len(phase_cfg.sources.adr_dirs)
    tick_total += len(phase_cfg.sources.opencode_instructions)
    tick_total += len(phase_cfg.sources.claude_instructions)
    tick_total += len(phase_cfg.sources.cursor_instructions)
    tick_total += len(phase_cfg.sources.windsurf_instructions)
    has_src = bool(
        phase_cfg.sources.adr_dirs
        or phase_cfg.sources.opencode_instructions
        or phase_cfg.sources.claude_instructions
        or phase_cfg.sources.cursor_instructions
        or phase_cfg.sources.windsurf_instructions
    )
    if has_src or (effective_repo and not no_scan):
        tick_total += 1  # backfill edges
    if project_db_path and effective_repo:
        tick_total += 1  # track repo in project_sources
    tick_total = max(tick_total, 1)  # avoid indeterminate bar

    # Warn on re-init when the graph already contains data.
    if project_db_path:
        db_path = str(Path(project_db_path).expanduser())
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

    # Run init — pipeline reads global config (no config_path)
    try:
        with make_progress() as progress:
            task_id = progress.add_task(
                "Initializing knowledge graph...",
                total=tick_total,
            )
            advance = make_step_tracker(progress, task_id, preamble="")

            graph = run_init(
                repo_path=effective_repo,
                no_scan=no_scan,
                db_path=project_db_path,
                project=project,
                advance=advance,
                deep=deep,
            )
    except RuntimeError as e:
        err_console.print(
            panel(text(str(e)), title="Error", border_style=C_FAIL)
        )
        raise typer.Exit(code=1)

    # Load config for source counts in post-bootstrap output
    cfg = phase_cfg
    stats = graph.stats()

    # Post-bootstrap summary
    from mementic.health import check_health
    items = check_health(graph, repo_path=cwd)

    adr_count = len(cfg.sources.adr_dirs)
    instr_count = (
        len(cfg.sources.opencode_instructions)
        + len(cfg.sources.claude_instructions)
        + len(cfg.sources.cursor_instructions)
        + len(cfg.sources.windsurf_instructions)
    )
    entity_count = stats.get("entities", 0)

    # Post-bootstrap summary
    from mementic.health import check_health

    items = check_health(graph, repo_path=cwd)

    adr_count = len(cfg.sources.adr_dirs)
    instr_count = (
        len(cfg.sources.opencode_instructions)
        + len(cfg.sources.claude_instructions)
        + len(cfg.sources.cursor_instructions)
        + len(cfg.sources.windsurf_instructions)
    )
    entity_count = stats.get("entities", 0)

    # Sources summary
    src_table = kv_table()
    src_table.add_column("Label", style="bold", width=20)
    src_table.add_column("Value", style=C_DIM)
    src_table.add_row("ADR", str(adr_count))
    src_table.add_row("Instructions", str(instr_count))
    src_table.add_row("Codebase entities", str(entity_count))

    # Graph summary
    g_table = kv_table()
    g_table.add_column("Label", style="bold", width=20)
    g_table.add_column("Value", style=C_DIM)
    g_table.add_row("Decisions", str(stats.get("decisions", 0)))
    g_table.add_row("Constraints", str(stats.get("constraints", 0)))
    g_table.add_row("Entities", str(entity_count))
    g_table.add_row("Edges", str(stats.get("edges", 0)))

    # Health table
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
            text(f"[{C_HEADER} bold]Sources[/]"),
            src_table,
            rule(),
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

    # Enforcement injection check
    from mementic.inject import detect_agents, has_enforcement, inject_enforcement

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

    # Quality notice for deterministic mode
    post_cfg = load_global_config()
    if not post_cfg.llm.get_api_key():
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
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
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
    cfg = load_config(repo_path=detect_repo_path())
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

    # Load or create graph — needed for checkpoint query
    is_project = False
    project_display: str | None = None
    repo_path = str(Path.cwd())

    try:
        proj_name, global_cfg = resolve_project(project)
        proj_entry = global_cfg.projects[proj_name]
        db_path = Path(proj_entry.db_path).expanduser()
        graph = GraphStore(db_path=db_path)
        is_project = True
        project_display = proj_name
        # Use registered repo path if available, else CWD
        for name, repo_entry in proj_entry.repos.items():
            if Path(repo_entry.path).resolve() == Path(repo_path).resolve():
                repo_path = repo_entry.path
                break
    except ValueError:
        if project is not None:
            typer.echo(
                f"Project '{project}' not found. Use 'mementic project list' "
                f"to see configured projects.",
                err=True,
            )
            raise typer.Exit(code=1)
        # No projects configured — fall back to legacy graph.json
        graph_path = cfg.output.graph_path
        if Path(graph_path).exists():
            try:
                graph = _load_graph_safe(graph_path)
            except typer.Exit:
                typer.echo("Starting with empty graph.", err=True)
                graph = GraphStore()
        else:
            graph = GraphStore()

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
                session_text, git_diff, notes or "", cfg,
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

    if is_project:
        # SQLite mode: data already persisted via _write_back on each mutation
        pass
    else:
        Path(graph_path).parent.mkdir(parents=True, exist_ok=True)
        graph.save(graph_path)

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
    if is_project and project_display:
        result_table.add_row(
            "Written",
            text(f"[{C_DIM}]project '{project_display}' database[/]"),
        )
    else:
        result_table.add_row(
            "Written", text(f"[{C_DIM}]{graph_path}[/]")
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
    project: str | None = typer.Option(None, "--project", help="Target project name"),
) -> None:
    """Return ranked context pack of constraints and decisions matched by keywords."""
    graph, _cfg = _resolve_graph(project)
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
            Markdown(output),
            title=f"Context pack: {kw_list}",
        )
    )


@query_app.command()
def file(
    path: str = typer.Argument(..., help="File path to trace evolution for"),
    prefix: bool = typer.Option(False, "--prefix", help="Use prefix matching for entity paths"),
    max_tokens: int = typer.Option(1500, "--max-tokens", help="Maximum token budget for output"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    project: str | None = typer.Option(None, "--project", help="Target project name"),
) -> None:
    """Show constraints and decisions affecting a specific file."""
    graph, _cfg = _resolve_graph(project)
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
        panel(Markdown(evolution_text), title=f"File history: {path}")
    )


@query_app.command()
def search(
    term: str = typer.Argument(..., help="Search term for titles, rationales, and descriptions"),
    project: str | None = typer.Option(None, "--project", help="Target project name"),
) -> None:
    """Full-text search across decisions and constraints."""
    graph, _cfg = _resolve_graph(project)
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
    project: str | None = typer.Option(None, "--project", help="Target project name"),
) -> None:
    """Show graph statistics."""
    graph, _cfg = _resolve_graph(project)
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
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
    ),
) -> None:
    """Show recent graph mutations from the audit log."""
    graph, _cfg = _resolve_graph(project)

    if graph._conn is not None:
        rows = graph._conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        if not rows:
            typer.echo("Audit log is empty. Run init or capture first.")
            raise typer.Exit()
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

    # Fallback: read legacy JSONL audit log
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
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
    ),
) -> None:
    """Show health checklist for mementic."""
    from mementic.health import check_health

    try:
        graph, _cfg = _resolve_graph(project)
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
    project: str | None = typer.Option(
        None, "--project", help="Target project name",
    ),
) -> None:
    """List architectural decisions in a scannable table."""
    graph, _cfg = _resolve_graph(project)
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
    project: str | None = typer.Option(
        None, "--project", help="Target project name",
    ),
) -> None:
    """List constraints in a scannable table."""
    graph, _cfg = _resolve_graph(project)
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
    project: str | None = typer.Option(
        None, "--project", help="Target project name",
    ),
) -> None:
    """List code entities in a scannable table."""
    graph, _cfg = _resolve_graph(project)

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
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
    ),
) -> None:
    """Mark one or more decisions or constraints as deprecated.

    Appends a deprecation note and writes audit entries.
    Deprecated entities remain in the graph for history but are
    excluded from mementic memory.
    """
    graph, cfg = _resolve_graph(project)

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

    _save_graph_cli(graph, cfg)

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
    project: str | None = typer.Option(None, "--project", help="Target project name"),
) -> None:
    """Record a new architectural decision."""
    graph, cfg = _resolve_graph(project)
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
        repo=resolve_repo_name(detect_repo_path()),
    )
    graph.add_decision(d)
    for ep in (entities or []):
        clean = re.sub(r"::[A-Za-z_]\w*$", "", ep)
        clean = re.sub(r":[A-Za-z_]\w*$", "", clean)
        e = CodeEntity(id=clean, type=EntityType.FILE, repo=d.repo)
        graph.add_code_entity(e)
        graph.link(d.id, clean, "affects")
    _save_graph_cli(graph, cfg)
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
    project: str | None = typer.Option(None, "--project", help="Target project name"),
) -> None:
    """Record a new constraint."""
    graph, cfg = _resolve_graph(project)
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
        repo=resolve_repo_name(detect_repo_path()),
    )
    graph.add_constraint(c)
    for ep in entities:
        clean = re.sub(r"::[A-Za-z_]\w*$", "", ep)
        clean = re.sub(r":[A-Za-z_]\w*$", "", clean)
        e = CodeEntity(id=clean, type=EntityType.FILE, repo=c.repo)
        graph.add_code_entity(e)
        graph.link(c.id, clean, "binds_to")
    _save_graph_cli(graph, cfg)
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
    console.print(
        panel(
            text(
                f"Project [{C_ID}]{name}[/] created.\n"
                f"[{C_DIM}]Database will be created on first mementic init.[/]"
            ),
            title="Project created",
        )
    )


@project_app.command(name="list")
def list_projects() -> None:
    """List all configured projects."""
    config = load_global_config()

    if not config.projects:
        typer.echo("No projects configured.")
        raise typer.Exit()

    default = config.default_project
    proj_table = table()
    proj_table.add_column("Default", width=2)
    proj_table.add_column("Name", style="bold")
    proj_table.add_column("Repos")
    proj_table.add_column("Description", style=C_DIM)
    for proj_name, proj_entry in config.projects.items():
        marker = f"[{C_SUCCESS}]*[/]" if proj_name == default else " "
        proj_table.add_row(
            marker,
            proj_name,
            str(len(proj_entry.repos)),
            proj_entry.description or "",
        )
    console.print(panel(proj_table, title="Projects"))


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
    console.print(
        panel(
            text(f"Default project: [{C_ID}]{name}[/]"),
            title="Default set",
        )
    )


@project_app.command()
def delete(
    name: str = typer.Argument(..., help="Project name to delete"),
    force: bool = typer.Option(
        False, "--force", "-f",
        help="Skip confirmation prompt",
    ),
    keep_db: bool = typer.Option(
        False, "--keep-db",
        help="Keep the SQLite database file (delete config entry only)",
    ),
) -> None:
    """Delete a project and optionally its database file."""
    from mementic.config import delete_project as _delete_project

    # Load config for display purposes only; delete_project() handles
    # its own validation (existence check, last-project guard).
    config = load_global_config()

    if name in config.projects:
        proj = config.projects[name]
        db_path = Path(proj.db_path).expanduser()

        typer.echo(f"Project: {name}")
        typer.echo(f"Repos:   {len(proj.repos)}")
        typer.echo(f"DB path: {db_path}")

        if not keep_db and db_path.is_file() and not force:
            confirm = typer.confirm(
                f"Delete database file {db_path}?"
            )
            if not confirm:
                typer.echo(
                    "Keeping database file. Deleting config entry only."
                )
                keep_db = True

    try:
        _, msg = _delete_project(name, delete_db=not keep_db)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    console.print(
        panel(text(f"[{C_SUCCESS}]{msg}[/]"), title="Project deleted")
    )


@project_app.command()
def info(
    name: str | None = typer.Argument(
        None, help="Project name (defaults to current default)"
    ),
) -> None:
    """Show details for a project."""
    config = load_global_config()

    target = name if name is not None else config.default_project

    if target not in config.projects:
        available = ", ".join(sorted(config.projects))
        typer.echo(
            f"Project '{target}' not found. Available: {available}",
            err=True,
        )
        raise typer.Exit(code=1)

    proj = config.projects[target]
    is_default = target == config.default_project

    info_table = kv_table()
    info_table.add_column("", style="bold", width=15)
    info_table.add_column("", style=C_DIM)
    info_table.add_row("DB path", str(proj.db_path))
    if proj.description:
        info_table.add_row("Description", proj.description)
    info_table.add_row("Repos", str(len(proj.repos)))

    console.print(
        panel(
            info_table,
            title=f"Project: {target}{' (default)' if is_default else ''}",
        )
    )

    # Also show individual repos
    if proj.repos:
        repo_lines = []
        for repo_path, repo_entry in proj.repos.items():
            label = f" [{repo_entry.label}]" if repo_entry.label else ""
            repo_lines.append(f"[{C_PATH}]{repo_path}{label}[/]")
        console.print(
            panel(
                *[text(line) for line in repo_lines],
                title="Registered repos",
            )
        )


if __name__ == "__main__":
    app()
