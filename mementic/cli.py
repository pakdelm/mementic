from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, TYPE_CHECKING

import typer
from pydantic import ValidationError

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
    count_tokens,
    detect_repo_path,
    format_memory_context,
    timestamp,
)
from mementic.graph import GraphStore
from mementic.models import CodeEntity, DecisionStatus, EntityType, MetricsEntry
from mementic.pipeline import _extract_session, run_init
from mementic.conflict import auto_resolve_capture
from mementic.metrics import UsageStore
from mementic.sources.adr import scan_adr_dir
from mementic.sources.opencode import scan_opencode_instructions
from mementic.inject import (
    _AGENT_MARKERS,
    detect_agents,
    get_mcp_config_file,
    get_rules_file,
    has_enforcement,
    inject_enforcement,
)

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
            ["git", "diff", reference], capture_output=True, text=True
        )
        return result.stdout or "", result.returncode
    except FileNotFoundError:
        return "", 1


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
            typer.echo(
                "No pre-configured sources. All markdown files will be "
                "scanned during init."
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
        typer.echo(
            f"Registered repo '{repo_name}' in project '{proj_name}'."
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
            typer.echo(
                "Error: --key requires an interactive terminal.\n"
                "  Run 'mementic init' without --key for guided setup, or\n"
                "  set the environment variable for your provider:\n"
                "    export DEEPSEEK_API_KEY=\"your-key-here\"\n"
                "    export OPENAI_API_KEY=\"your-key-here\"\n"
                "    export OPENROUTER_API_KEY=\"your-key-here\"",
                err=True,
            )
            raise typer.Exit(code=1)

        typer.echo()
        typer.echo("Select an LLM provider.")
        typer.echo(f"  {', '.join(PROVIDER_CONFIGS.keys())}")
        typer.echo(
            f"  Press Enter to keep current ({global_cfg.llm.provider})."
        )
        provider = typer.prompt("Provider", default=global_cfg.llm.provider).strip()

        if provider not in PROVIDER_CONFIGS:
            typer.echo(
                f"Error: Unknown provider '{provider}'.\n"
                f"  Known: {', '.join(PROVIDER_CONFIGS.keys())}",
                err=True,
            )
            raise typer.Exit(code=1)

        provider_cfg = PROVIDER_CONFIGS[provider]
        env_var = provider_cfg["key_env_var"]

        if env_var is None:
            global_cfg.llm.provider = provider
            typer.echo("Ollama runs locally — no API key needed.")
            typer.echo("Make sure ollama is running on localhost:11434")
            save_global_config(global_cfg)
            typer.echo(f"Saved to {GLOBAL_CONFIG_DISPLAY_PATH}")
        else:
            key_val = typer.prompt(
                f"API key for {provider} ({env_var})",
                hide_input=True,
                show_default=False,
            )
            if not key_val.strip():
                typer.echo("No key entered — no changes made.")
            else:
                global_cfg.llm.provider = provider
                try:
                    global_cfg.llm.set_api_key(key_val.strip())
                except RuntimeError as e:
                    typer.echo(str(e), err=True)
                    raise typer.Exit(code=1)

                typer.echo("Key stored in OS keyring.")
                typer.echo(
                    f"Fallback (CI/containers): export {env_var}=\"your-key-here\""
                )
                save_global_config(global_cfg)
                typer.echo(f"Saved to {GLOBAL_CONFIG_DISPLAY_PATH}")

        return
    elif not api_key and sys.stdin.isatty():
        # No key found — offer to set one up
        typer.echo()
        typer.echo("Mementic can extract richer architectural context using an LLM.")
        typer.echo(
            "Deterministic mode (no key) works but produces lower-quality results."
        )
        typer.echo()
        setup_key = typer.confirm("Set up an LLM provider now?", default=True)

        if setup_key:
            typer.echo()
            typer.echo("Select an LLM provider.")
            typer.echo(f"  {', '.join(PROVIDER_CONFIGS.keys())}")
            provider = typer.prompt(
                "Provider", default="", show_default=False
            )

            if provider not in PROVIDER_CONFIGS:
                typer.echo(f"Unknown provider '{provider}'. Skipping LLM setup.")
            else:
                provider_cfg = PROVIDER_CONFIGS[provider]
                env_var = provider_cfg["key_env_var"]

                if env_var is None:
                    # ollama — no key needed
                    typer.echo("Ollama runs locally — no API key needed.")
                    typer.echo("Make sure ollama is running on localhost:11434")
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
                        typer.echo("Key stored in OS keyring.")
                        typer.echo(
                            f"Fallback for CI/headless: export {env_var}=..."
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

    # Run init — pipeline reads global config (no config_path)
    try:
        graph, all_usage = run_init(
            repo_path=effective_repo,
            no_scan=no_scan,
            db_path=project_db_path,
            project=project,
        )
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)

    # Load config for source counts in post-bootstrap output
    cfg = load_config(repo_path=str(cwd), project=project)
    stats = graph.stats()
    typer.echo()
    typer.echo("Mementic initialized")
    typer.echo()

    # Source counts
    adr_count = len(cfg.sources.adr_dirs)
    instr_count = (
        len(cfg.sources.opencode_instructions)
        + len(cfg.sources.claude_instructions)
        + len(cfg.sources.cursor_instructions)
        + len(cfg.sources.windsurf_instructions)
    )
    entity_count = stats.get("entities", 0)
    typer.echo(
        f"  Sources      ADR: {adr_count}    "
        f"Instructions: {instr_count}    "
        f"Codebase: {entity_count} entities"
    )
    typer.echo(
        f"  Graph        {stats.get('decisions', 0)} decisions, "
        f"{stats.get('constraints', 0)} constraints, "
        f"{entity_count} entities, "
        f"{stats.get('edges', 0)} edges"
    )
    typer.echo()

    # Health checklist
    from mementic.health import check_health
    items = check_health(graph, repo_path=cwd)
    typer.echo("  Status")
    for item in items:
        marker = "[OK]" if item.status == "ok" else "[FAIL]"
        if item.hint:
            typer.echo(f"    {marker:<6} {item.label:<10} {item.hint}")
        else:
            typer.echo(f"    {marker:<6} {item.label}")
    typer.echo()
    typer.echo("  Next: mementic capture --agent opencode")

    # Enforcement injection check
    agents_md_path = cwd / "AGENTS.md"
    if not has_enforcement(agents_md_path):
        detected = detect_agents(cwd)
        if detected:
            typer.echo()
            if sys.stdin.isatty():
                inject_ok = typer.confirm(
                    "No enforcement instructions found. Add to AGENTS.md?",
                    default=True,
                )
                if inject_ok:
                    result = inject_enforcement(agents_md_path)
                    typer.echo(f"  {result}")
                else:
                    typer.echo(
                        "  Warning: Without enforcement, mementic operates as CLI-only."
                    )
                    typer.echo(
                        "  Agents won't automatically query the graph."
                        " Run 'mementic inject' later."
                    )
            else:
                typer.echo(
                    "  No enforcement instructions found."
                    " Run 'mementic inject' to add them."
                )

    # Quality notice for deterministic mode
    post_cfg = load_global_config()
    if not post_cfg.llm.get_api_key():
        typer.echo()
        typer.echo("  Note  Running without LLM API key — deterministic extraction only.")
        typer.echo(f"        Graph quality is reduced. Set a provider key in {GLOBAL_CONFIG_DISPLAY_PATH}")
        typer.echo("        for richer extraction, or continue as-is (inline proposals work regardless).")

    # Log extraction costs to metrics (project database only)
    if all_usage and graph._conn is not None:
        store = UsageStore(conn=graph._conn)
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
        extraction, _usage = _extract_session(
            session_text, git_diff, notes or "", cfg,
            max_messages=tail, custom_keywords=keyword or None,
        )
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)

    if not extraction.decisions and not extraction.constraints:
        typer.echo("No architectural decisions or constraints found.")
        raise typer.Exit()

    from mementic.format import DECISION_ID_MAX_LENGTH, CONSTRAINT_ID_MAX_LENGTH, slugify

    # Replace LLM-generated IDs (dec-001, etc.) with deterministic slugs
    for d in extraction.decisions:
        d.id = slugify(d.title, max_length=DECISION_ID_MAX_LENGTH)
    for c in extraction.constraints:
        c.id = slugify(c.description, max_length=CONSTRAINT_ID_MAX_LENGTH)

    counts = auto_resolve_capture(
        graph, extraction.decisions, extraction.constraints
    )

    # Add only NEW and UPDATED decisions (skipped are not added)
    for d in extraction.decisions:
        if not graph.has_node(d.id) or d.status == DecisionStatus.ACCEPTED:
            if graph.has_node(d.id):
                # Rule 3: existing Decision is deprecated; overwrite with accepted
                # add_decision handles cross-type collision safety (raises ValueError)
                graph.add_decision(d)
            else:
                # Rule 1: new node
                graph.add_decision(d)
        # Rule 2 (skipped): don't add

        for entity_path in extraction.affected_entities:
            graph.add_code_entity(
                CodeEntity(id=entity_path, type=EntityType.FILE)
            )
            graph.link(d.id, entity_path, "affects")

    for c in extraction.constraints:
        if not graph.has_node(c.id):
            graph.add_constraint(c)
        elif c.status == "active":
            # Updated constraint: existing was deprecated; add_constraint handles update
            graph.add_constraint(c)
        # Skipped: don't add

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
    typer.echo()
    typer.echo("=" * 50)
    typer.echo("Capture complete")
    typer.echo("=" * 50)
    typer.echo(f"  New:           {counts['new_decisions']} decisions, "
               f"{counts['new_constraints']} constraints")
    if counts['updated_decisions'] > 0:
        typer.echo(f"  Updated:       {counts['updated_decisions']} decisions "
                    f"(existing deprecated)")
    if counts['skipped_decisions'] > 0 or counts['skipped_constraints'] > 0:
        total_skipped = counts['skipped_decisions'] + counts['skipped_constraints']
        typer.echo(f"  Duplicates:    {total_skipped} skipped")
    if is_project and project_display:
        typer.echo(f"  Written to project '{project_display}' database")
    else:
        typer.echo(f"  Written to {graph_path}")
    if _usage.model == "deterministic":
        typer.echo("  Mode:        deterministic (no API key — reduced quality)")

    # Log capture metrics (project database only)
    if graph._conn is not None:
        metrics_store = UsageStore(conn=graph._conn)
    else:
        metrics_store = UsageStore(path=".mementic/metrics.jsonl")  # legacy fallback
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
        try:
            head_result = subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True
            )
            if head_result.returncode == 0:
                graph.set_capture_checkpoint(
                    repo_path, head_result.stdout.strip()
                )
        except FileNotFoundError:
            pass  # git not available — skip


@app.command()
def context(
    keywords: list[str] = typer.Option(
        ..., "--keyword",
        help="Keywords for matching. Repeatable. Agent generates 15-25 single-word keywords.",
    ),
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
    ),
) -> None:
    """Load constraints and produce mementic memory ranked by keyword overlap.

    Keyword overlap with constraint/decision descriptions determines
    relevance ranking.  All items pass through; zero-overlap items
    sink to the bottom and are omitted if token budget is exceeded.
    """
    graph, _cfg = _resolve_graph(project)
    graph.clean_dead_references()

    constraints = graph.get_all_active_constraints()
    decisions = graph.get_all_active_decisions()

    output = format_memory_context(
        decisions, constraints,
        max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
        max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
        keywords=keywords,
    )
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
    graph.clean_dead_references()

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
        for row in rows:
            entity_type = row["entity_type"]
            action = row["action"]
            entity_id = row["entity_id"]
            timestamp = row["timestamp"]
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
                summary = f"{details.get('source', '')} → {details.get('target', '')} ({details['relation']})"
            elif isinstance(details, str):
                summary = details
            else:
                summary = "-"
            typer.echo(
                f"[{timestamp}] {entity_type}/{action} {entity_id} — {summary}"
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


@app.command()
def server():
    """Start the MCP server for agent integration (stdio transport)."""
    import sys
    from mementic.server import mcp
    sys.stderr.write("Mementic MCP server starting (stdio transport)...\n")
    mcp.run(transport="stdio")


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
    typer.echo("Mementic status")
    for item in items:
        marker = "[OK]" if item.status == "ok" else "[FAIL]"
        line = f"  {marker:<6} {item.label}"
        if item.hint:
            line += f"  {item.hint}"
        typer.echo(line)


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


def _status_icon(status: str) -> str:
    """Return a compact status marker for list output."""
    icons = {
        "accepted": "+",
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
        help="Filter by status: accepted, deprecated, rejected, active",
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
        mementic list decisions --status accepted
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

    valid_statuses = {"accepted", "deprecated", "rejected", "active"}
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
    cfg: MementicConfig | None = None
    try:
        graph, cfg = _resolve_graph(project)
        store = UsageStore(conn=graph._conn) if graph._conn else UsageStore()
    except typer.Exit:
        if project is not None:
            raise  # explicit --project not found: propagate error
        store = UsageStore()  # no project configured — empty store

    summary = store.summary()

    if json_output:
        typer.echo(json.dumps(summary, indent=2, default=str))
    else:
        # Task 2.2 — Lazy source material counting
        source_file_count = 0
        source_tokens = 0

        if cfg is not None:
            # Count ADR files
            for adr_source in cfg.sources.adr_dirs:
                if Path(adr_source.path).is_dir():
                    adrs = scan_adr_dir(adr_source.path)
                    for adr in adrs:
                        try:
                            text = Path(adr.path).read_text(encoding="utf-8")
                            source_tokens += count_tokens(text)
                            source_file_count += 1
                        except Exception:  # noqa: S110
                            pass

            # Count instruction files (all agent types — same reader)
            for source_list in (
                cfg.sources.opencode_instructions,
                cfg.sources.claude_instructions,
                cfg.sources.cursor_instructions,
                cfg.sources.windsurf_instructions,
            ):
                for source in source_list:
                    if Path(source.path).is_dir():
                        instructions = scan_opencode_instructions(source.path)
                        for instr in instructions:
                            source_tokens += count_tokens(instr.content)
                            source_file_count += 1

        source_available = source_tokens > 0

        init_calls: int = summary["extraction_init_calls"]
        init_tokens: int = summary["extraction_init_tokens"]
        capture_calls: int = summary["extraction_capture_calls"]
        capture_tokens: int = summary["extraction_capture_tokens"]
        cp_queries: int = summary["memory_context_queries"]
        cp_total: int = summary["memory_context_total_tokens"]
        cp_avg: float = summary["memory_context_avg_tokens"]

        extraction_total = init_tokens + capture_tokens
        capture_avg = capture_tokens / capture_calls if capture_calls > 0 else 0.0

        compression_ratio = (
            (init_tokens / source_tokens * 100) if source_tokens > 0 else None
        )
        graph_compression = (
            (extraction_total / source_tokens * 100) if source_tokens > 0 else None
        )
        pack_vs_source = (
            (cp_avg / source_tokens * 100)
            if source_tokens > 0 and cp_avg > 0
            else None
        )
        extraction_amortized = (
            (extraction_total / cp_queries) if cp_queries > 0 else None
        )

        def _n(v: int) -> str:
            return f"{v:,}"

        def _t(v: int | float) -> str:
            return f"{v:,.0f}"

        def _p(v: float | None) -> str:
            if v is None:
                return "--"
            return f"{v:.1f}%"

        def _section(header: str) -> None:
            typer.echo(f"{header:<30}{'Count':>8}     {'Tokens':>12}")

        def _row(label: str, count: str, tokens: str) -> None:
            typer.echo(f"  {label:<28}{count:>8}     {tokens:>12}")

        def _ref_row(label: str, value: str) -> None:
            typer.echo(f"  {label:<28}  {value}")

        sep = "=" * 56
        typer.echo(sep)
        typer.echo("                  MEMENTIC TOKEN USAGE")
        typer.echo(sep)
        typer.echo()

        # Section 1: Setup (one-time)
        _section("Setup (one-time)")
        sc = _n(source_file_count) if source_available else "--"
        st = _n(source_tokens) if source_available else "--"
        _row("Source material", sc, st)
        _row("Init extraction", _n(init_calls), _n(init_tokens))
        _row("Compression ratio", "", _p(compression_ratio))
        typer.echo()

        # Section 2: Capture (recurring)
        _section("Capture (recurring)")
        _row("Sessions", _n(capture_calls), _n(capture_tokens))
        ca = _t(capture_avg) if capture_calls > 0 else "--"
        _row("Avg per session", "", ca)
        typer.echo()

        # Section 3: Context Packs
        _section("Context Packs")
        _row("Queries", _n(cp_queries), _n(cp_total))
        qa = _t(cp_avg) if cp_queries > 0 else "--"
        _row("Avg per pack", "", qa)
        typer.echo()

        # Section 4: Reference
        typer.echo("Reference")
        gc = _p(graph_compression)
        gc_detail = f"({_n(extraction_total)} / {_n(source_tokens)})" if source_available else ""
        _ref_row("Graph compression", f"{gc}  {gc_detail}".rstrip())
        pv = _p(pack_vs_source)
        pv_detail = (
            f"({_t(cp_avg)} / {_n(source_tokens)})"
            if source_available and cp_queries > 0
            else ""
        )
        _ref_row("Pack vs source", f"{pv}  {pv_detail}".rstrip())
        ea = _t(extraction_amortized) if extraction_amortized is not None else "--"
        ea_detail = (
            f"({_n(extraction_total)} / {cp_queries})" if cp_queries > 0 else ""
        )
        _ref_row("Extraction amortized", f"{ea}  tokens/query {ea_detail}".rstrip())

    if reset:
        if typer.confirm("Delete all metrics?"):
            store.reset()
            typer.echo("Metrics reset.")


@app.command()
def deprecate(
    id: str = typer.Argument(..., help="Decision or constraint ID to deprecate"),
    reason: str = typer.Option(
        "", "--reason", "-r",
        help="Reason for deprecation",
    ),
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
    ),
) -> None:
    """Mark a decision or constraint as deprecated.

    Appends a deprecation note and writes an audit entry.
    The entity remains in the graph for history but is excluded
    from mementic memory.
    """
    graph, cfg = _resolve_graph(project)

    result = graph.deprecate_entity(id, reason)
    graph.save(cfg.output.graph_path)

    if result["status"] == "deprecated":
        typer.echo(result["message"])
    elif result["status"] == "already_deprecated":
        typer.echo(result["message"])
        raise typer.Exit()
    elif result["status"] == "wrong_type":
        typer.echo(result["message"], err=True)
        raise typer.Exit(code=1)
    else:
        typer.echo(result["message"], err=True)
        raise typer.Exit(code=1)


@app.command()
def session_cleanup(
    feature_description: str = typer.Argument(
        ..., help="Description of the feature/task just completed",
    ),
    deprecated_ids: list[str] | None = typer.Option(
        None, "--deprecate", "-d",
        help="Decision or constraint IDs to deprecate (repeatable)",
    ),
    reason: str = typer.Option(
        "", "--reason", "-r",
        help="Reason for deprecation (applied to all deprecated IDs)",
    ),
    project: str | None = typer.Option(
        None, "--project",
        help="Target project name",
    ),
) -> None:
    """Clean up mementic context after recording decisions/constraints.

    Deprecates superseded entities, cleans dead references, and reports
    graph state. Use after recording inline proposals during a session.
    """
    graph, cfg = _resolve_graph(project)
    deprecated: list[str] = []
    already: list[str] = []
    not_found: list[str] = []

    for entity_id in (deprecated_ids or []):
        result = graph.deprecate_entity(entity_id, reason)
        if result["status"] == "deprecated":
            deprecated.append(entity_id)
        elif result["status"] == "already_deprecated":
            already.append(entity_id)
        else:
            not_found.append(entity_id)

    # Write deprecations before cleanup
    graph.save(cfg.output.graph_path)

    # Clean dead references
    graph.clean_dead_references()
    graph.save(cfg.output.graph_path)

    # Count active
    active_constraints = len(graph.get_all_active_constraints())
    active_decisions = len(graph.get_all_active_decisions())

    parts: list[str] = [
        f"session-cleanup: '{feature_description}'"
    ]
    if deprecated:
        parts.append(f"deprecated {', '.join(deprecated)}")
    if already:
        parts.append(f"(already deprecated: {', '.join(already)})")
    if not_found:
        parts.append(f"(not found: {', '.join(not_found)})")
    parts.append(
        f"{active_decisions + active_constraints} active "
        f"({active_decisions} decisions, {active_constraints} constraints)"
    )

    typer.echo(" — ".join(parts))


@app.command()
def inject(
    agent: str | None = typer.Option(
        None, "--agent",
        help="Target agent (opencode, claude-code, cursor, windsurf). Auto-detects if omitted.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Show what would be written without modifying files.",
    ),
) -> None:
    """Inject mementic enforcement instructions into AGENTS.md.

    Auto-detects agents in the repo and writes the enforcement block.
    For non-OpenCode agents, offers to write pointer files and register
    the MCP server.
    """
    cwd = Path(detect_repo_path())
    agents_md = cwd / "AGENTS.md"

    if agent:
        agents = [agent]
        valid_agents = set(_AGENT_MARKERS.values())
        if agent not in valid_agents:
            typer.echo(
                f"Unknown agent '{agent}'. Valid: {', '.join(sorted(valid_agents))}",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        agents = detect_agents(cwd)
        if not agents:
            typer.echo("No agents detected. Looking for .opencode/, .claude/, .cursor/, .windsurf/ directories.")
            typer.echo("Run 'mementic inject --agent opencode' to inject manually.")
            raise typer.Exit(code=1)

    # Inject enforcement block
    result = inject_enforcement(agents_md, dry_run=dry_run)
    typer.echo(result)

    if "already exists" in result and not dry_run:
        typer.echo("To re-inject, remove the existing block from AGENTS.md first.")
        raise typer.Exit()

    # For non-OpenCode agents, offer pointer files
    for ag in agents:
        rules_file = get_rules_file(ag)
        if rules_file and not dry_run:
            rules_path = cwd / rules_file
            if not rules_path.exists():
                if sys.stdin.isatty():
                    create_pointer = typer.confirm(
                        f"Create {rules_file} with pointer to AGENTS.md?",
                        default=True,
                    )
                    if create_pointer:
                        rules_path.write_text(
                            "See AGENTS.md for mementic enforcement instructions.\n",
                            encoding="utf-8",
                        )
                        typer.echo(f"  Created {rules_file}")

    # Offer to register MCP server
    for ag in agents:
        mcp_config = get_mcp_config_file(ag)
        if mcp_config and not dry_run:
            config_path = cwd / mcp_config
            if not config_path.exists():
                if sys.stdin.isatty():
                    create_config = typer.confirm(
                        f"Create {mcp_config} with mementic MCP server?",
                        default=True,
                    )
                    if create_config:
                        config_path.parent.mkdir(parents=True, exist_ok=True)
                        mcp_entry = {
                            "mcpServers": {
                                "mementic": {
                                    "command": "uv",
                                    "args": ["run", "mementic", "server"],
                                }
                            }
                        }
                        config_path.write_text(
                            json.dumps(mcp_entry, indent=2) + "\n",
                            encoding="utf-8",
                        )
                        typer.echo(f"  Created {mcp_config}")

    if dry_run:
        typer.echo("\n(dry run — no files modified)")


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

    typer.echo(msg)


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

    typer.echo(f"Project:   {target}{'  (default)' if is_default else ''}")
    typer.echo(f"DB path:   {proj.db_path}")
    if proj.description:
        typer.echo(f"Description: {proj.description}")
    typer.echo(f"Repos ({len(proj.repos)}):")

    if not proj.repos:
        typer.echo("  (none)")
    else:
        for repo_path, repo_entry in proj.repos.items():
            label = f"  [{repo_entry.label}]" if repo_entry.label else ""
            typer.echo(f"  {repo_path}{label}")


if __name__ == "__main__":
    app()
