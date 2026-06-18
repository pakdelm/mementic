"""Config loading and validation for Mementic project configuration."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


__all__ = [
    "AdrDirSource",
    "AgentSessionConfig",
    "ClaudeSource",
    "CursorSource",
    "delete_project",
    "GlobalConfig",
    "LLMConfig",
    "MementicConfig",
    "OpenCodeSource",
    "OutputConfig",
    "PROVIDER_CONFIGS",
    "ProjectEntry",
    "RepoEntry",
    "SessionsConfig",
    "Sources",
    "WindsurfSource",
    "get_global_config_path",
    "has_global_config",
    "get_provider_config",
    "load_config",
    "load_global_config",
    "resolve_project",
    "save_global_config",
]

PROVIDER_CONFIGS: dict[str, dict[str, str | None]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "key_env_var": "DEEPSEEK_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "key_env_var": "OPENAI_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_env_var": "OPENROUTER_API_KEY",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "key_env_var": None,  # local — no API key required
    },
}

DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL = "deepseek-chat"

GLOBAL_CONFIG_DISPLAY_PATH: str = "~/.mementic/config.json"


def get_provider_config(provider: str) -> dict[str, str | None]:
    """Return provider config dict, raising KeyError if unknown.

    Args:
        provider: Provider name (deepseek, openai, openrouter, ollama).

    Returns:
        Dict with base_url and key_env_var keys.

    Raises:
        KeyError: If the provider is not in PROVIDER_CONFIGS.
    """
    if provider not in PROVIDER_CONFIGS:
        known = ", ".join(PROVIDER_CONFIGS)
        msg = (
            f"Unknown LLM provider '{provider}'. "
            f"Supported providers: {known}."
        )
        raise ValueError(msg)
    return PROVIDER_CONFIGS[provider]


class AdrDirSource(BaseModel):
    """A directory containing ADR (Architecture Decision Record) files."""

    path: str


class OpenCodeSource(BaseModel):
    """A path to OpenCode instruction files."""

    path: str


class ClaudeSource(BaseModel):
    """A path to Claude Code instruction files."""

    path: str


class CursorSource(BaseModel):
    """A path to Cursor IDE instruction files."""

    path: str


class WindsurfSource(BaseModel):
    """A path to Windsurf IDE instruction files."""

    path: str


class Sources(BaseModel):
    """Configuration for knowledge sources."""

    adr_dirs: list[AdrDirSource] = Field(default_factory=list)
    opencode_instructions: list[OpenCodeSource] = Field(default_factory=list)
    claude_instructions: list[ClaudeSource] = Field(default_factory=list)
    cursor_instructions: list[CursorSource] = Field(default_factory=list)
    windsurf_instructions: list[WindsurfSource] = Field(default_factory=list)


class AgentSessionConfig(BaseModel):
    """Per-agent session discovery configuration."""

    log_dirs: list[str] = Field(default_factory=list)


class SessionsConfig(BaseModel):
    """Session discovery configuration for all supported agents.

    Each key maps to an agent name (e.g. 'opencode', 'claude-code', 'generic',
    'cursor', 'windsurf'). The JSON file may use hyphenated names
    (e.g. ``claude-code``) which are aliased to the snake_case field names.
    """

    opencode: AgentSessionConfig = Field(default_factory=AgentSessionConfig)
    claude_code: AgentSessionConfig = Field(
        default_factory=AgentSessionConfig,
        validation_alias="claude-code",
    )
    generic: AgentSessionConfig = Field(default_factory=AgentSessionConfig)
    cursor: AgentSessionConfig = Field(default_factory=AgentSessionConfig)
    windsurf: AgentSessionConfig = Field(default_factory=AgentSessionConfig)


class OutputConfig(BaseModel):
    """Configuration for output artifacts."""

    graph_path: str = "./.mementic/graph.json"


class LLMConfig(BaseModel):
    """LLM provider configuration.

    Supported providers: deepseek, openai, openrouter, ollama.
    """

    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    extraction_model: str = DEFAULT_MODEL
    extraction_mode: Literal["auto", "deterministic", "llm"] = "auto"

    @model_validator(mode="after")
    def _validate_provider(self: LLMConfig) -> LLMConfig:
        """Validate that the provider is known."""
        get_provider_config(self.provider)
        return self

    @property
    def base_url(self) -> str:
        """Return the base URL for the configured provider."""
        return str(get_provider_config(self.provider)["base_url"])

    @property
    def key_env_var(self) -> str | None:
        """Return the environment variable name for the API key, or None."""
        return get_provider_config(self.provider)["key_env_var"]

    def get_api_key(self) -> str | None:
        """Read the API key from the configured environment variable.

        Returns:
            The API key string, or None if the env var is not set.
        """
        env_var = self.key_env_var
        if env_var is None:
            return None
        return os.environ.get(env_var)

    def ensure_api_key(self) -> str:
        """Return the API key or raise a clear error message.

        Returns:
            The API key string.

        Raises:
            RuntimeError: If the configured provider requires an API key
                but the required environment variable is not set.
        """
        env_var = self.key_env_var
        if env_var is None:
            return "ollama"  # dummy — not used for auth but needed for OpenAI client init

        key = os.environ.get(env_var)
        if not key:
            msg = (
                f"Mementic requires an API key for provider '{self.provider}'.\n"
                f"  Set the environment variable: {env_var}\n"
                f"  Example: export {env_var}=\"your-key-here\"\n\n"
                f"  Or switch to a different provider in {GLOBAL_CONFIG_DISPLAY_PATH}:\n"
                f"  {{ \"llm\": {{ \"provider\": \"ollama\" }} }} (local, no key needed)\n"
                f"  {{ \"llm\": {{ \"provider\": \"openai\", \"model\": \"gpt-4o\" }} }}"
            )
            raise RuntimeError(msg)
        return key


class MementicConfig(BaseModel):
    """Root configuration for the Mementic project."""

    sources: Sources = Field(default_factory=Sources)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


def load_config(repo_path: str | None = None, project: str | None = None) -> MementicConfig:
    """Assemble MementicConfig from global config + repo entry.

    Sources come from the RepoEntry matching repo_path in the global
    config. LLM and sessions come from the global config.

    Args:
        repo_path: Path to the repository root. When None or not matching
            a registered repo, returns defaults with empty sources.
        project: Optional project name to resolve within.

    Returns:
        A composed MementicConfig.
    """
    try:
        global_cfg = load_global_config()
    except Exception:
        return MementicConfig()

    if repo_path is None:
        return MementicConfig(
            llm=global_cfg.llm,
            sessions=global_cfg.sessions,
        )

    try:
        resolved_repo = Path(repo_path).resolve()
    except (OSError, ValueError):
        return MementicConfig(
            llm=global_cfg.llm,
            sessions=global_cfg.sessions,
        )

    # Choose which projects to search
    if project is not None:
        proj_entries = global_cfg.projects.get(project)
        if proj_entries is None:
            return MementicConfig(
                llm=global_cfg.llm,
                sessions=global_cfg.sessions,
            )
        projects_to_search = [(project, proj_entries)]
    else:
        projects_to_search = list(global_cfg.projects.items())

    for _proj_name, proj_entry in projects_to_search:
        for repo_entry in proj_entry.repos.values():
            try:
                if Path(repo_entry.path).resolve() == resolved_repo:
                    return MementicConfig(
                        sources=repo_entry.sources,
                        llm=global_cfg.llm,
                        sessions=global_cfg.sessions,
                    )
            except (OSError, ValueError):
                continue

    return MementicConfig(
        llm=global_cfg.llm,
        sessions=global_cfg.sessions,
    )


class RepoEntry(BaseModel):
    """A registered repo within a project.

    Attributes:
        path: Absolute path to the repo on disk.
        label: Optional short display name for the repo.
    """

    path: str
    label: str = ""
    sources: Sources = Field(default_factory=Sources)


class ProjectEntry(BaseModel):
    """A named project with registered repos and a dedicated database.

    Attributes:
        description: Optional human-readable description.
        db_path: Path to the project's SQLite database
            (absolute or ~-prefixed).
        repos: Mapping of repo absolute paths to RepoEntry metadata.
    """

    description: str = ""
    db_path: str
    repos: dict[str, RepoEntry] = Field(default_factory=dict)


class GlobalConfig(BaseModel):
    """Global project configuration stored at ``~/.mementic/config.json``.

    This file defines named projects, each with its own database and
    registered repos.  It also holds shared configuration (LLM provider,
    session discovery) that was previously duplicated across per-repo
    config files.

    Attributes:
        version: Schema version of the config file.
        default_project: Name of the default project to use when no
            ``--project`` flag is given.
        projects: Mapping of project names to ProjectEntry objects.
            Empty when no config file exists yet (first run).
        llm: Shared LLM provider configuration. When set, this is the
            fallback for per-repo configs that lack their own ``llm`` section.
        sessions: Shared session discovery configuration. When set, this
            is the fallback for per-repo configs that lack their own
            ``sessions`` section.
    """

    version: int = 1
    default_project: str = "main"
    projects: dict[str, ProjectEntry] = Field(default_factory=dict)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)




def get_global_config_path() -> Path:
    """Return the canonical path to the global config file.

    Respects the ``MEMENTIC_GLOBAL_CONFIG`` environment variable.
    When set, that path is used instead of the default.

    Returns:
        ``Path.home() / ".mementic" / "config.json"``, unless overridden.
    """
    if override := os.environ.get("MEMENTIC_GLOBAL_CONFIG"):
        return Path(override)
    return Path.home() / ".mementic" / "config.json"


def has_global_config() -> bool:
    """Return True if ``~/.mementic/config.json`` exists on disk.

    Returns:
        ``True`` when the global project config file is present.
    """
    return get_global_config_path().is_file()


def load_global_config() -> GlobalConfig:
    """Load the global project config from ``~/.mementic/config.json``.

    If the file does not exist, a default ``GlobalConfig`` with no
    projects is returned.  Corrupt JSON produces a warning on stderr
    and also falls back to the default.

    Returns:
        A validated ``GlobalConfig`` instance.

    Raises:
        ValueError: If the config file exists but contains an empty
            ``projects`` dict — this indicates a manual editing mistake
            and must not be silently overwritten.
        pydantic.ValidationError: If the file contains valid JSON but
            invalid field types or values.
    """
    config_path = get_global_config_path()
    if not config_path.is_file():
        return _default_global_config()

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        sys.stderr.write(
            f"Config file {config_path} is corrupt. "
            "Run 'mementic init' to regenerate.\n"
        )
        return _default_global_config()

    config = GlobalConfig.model_validate(data)

    if not config.projects:
        raise ValueError(
            f"Config file {config_path} has an empty 'projects' dict. "
            "If you edited the file manually, restore a valid project entry "
            "or delete the file and run 'mementic init' to regenerate."
        )

    return config


def save_global_config(config: GlobalConfig) -> None:
    """Write a ``GlobalConfig`` to ``~/.mementic/config.json``.

    Creates the parent directory if it is missing.

    Args:
        config: The validated global config to persist.
    """
    config_path = get_global_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        config.model_dump_json(indent=2),
        encoding="utf-8",
    )


def resolve_project(project: str | None = None) -> tuple[str, GlobalConfig]:
    """Resolve which project to target.

    Resolution order:
    1. Explicit ``project`` argument → use it (must exist).
    2. No projects configured → error.
    3. CWD is inside a registered repo → use that project (if unambiguous).
    4. Single project → use it (regardless of ``default_project``).
    5. ``default_project`` in config → use it.
    6. Multiple projects, no match → error, require ``--project``.

    Args:
        project: Optional project name.  When given, the project must
            exist in the global config.

    Returns:
        A ``(project_name, global_config)`` tuple.

    Raises:
        ValueError: If the requested project does not exist, or if
            resolution fails (no projects, ambiguous CWD, etc.).
    """
    config = load_global_config()

    # Step 1: explicit --project
    if project is not None:
        if project not in config.projects:
            available = ", ".join(sorted(config.projects))
            raise ValueError(
                f"Project '{project}' not found in config. "
                f"Available: {available}"
            )
        return project, config

    if len(config.projects) == 0:
        raise ValueError(
            "No projects configured. Run 'mementic init' to create one."
        )

    # Step 2: CWD-based inference
    try:
        cwd = Path.cwd().resolve()
    except (OSError, ValueError):
        cwd = None

    if cwd is not None:
        matching_projects: list[str] = []
        for proj_name, proj_entry in config.projects.items():
            for repo_entry in proj_entry.repos.values():
                try:
                    repo_path = Path(repo_entry.path).resolve()
                    if cwd == repo_path or cwd.is_relative_to(repo_path):
                        matching_projects.append(proj_name)
                        break  # one match per project is enough
                except (OSError, ValueError):
                    continue

        if len(matching_projects) == 1:
            return matching_projects[0], config

        if len(matching_projects) > 1:
            project_list = ", ".join(sorted(matching_projects))
            raise ValueError(
                f"Current directory is registered in multiple projects "
                f"({project_list}). Use --project to specify."
            )

    # Step 3: default_project fallback
    if len(config.projects) == 1:
        (only_project,) = config.projects
        return only_project, config

    default = config.default_project
    if default in config.projects:
        return default, config

    available = ", ".join(sorted(config.projects))
    raise ValueError(
        f"Multiple projects configured. Use --project to specify. "
        f"Available: {available}"
    )


def delete_project(
    name: str,
    *,
    delete_db: bool = False,
) -> tuple[bool, str]:
    """Delete a project from the global config.

    Args:
        name: Project name to delete.
        delete_db: If True, also delete the SQLite database file.

    Returns:
        A ``(db_deleted, message)`` tuple. ``db_deleted`` is True if
        the database file was actually removed (only possible when
        ``delete_db`` is True).

    Raises:
        ValueError: If the project does not exist or is the last project.
    """
    config = load_global_config()

    if name not in config.projects:
        available = ", ".join(sorted(config.projects))
        raise ValueError(
            f"Project '{name}' not found. Available: {available}"
        )

    if len(config.projects) <= 1:
        raise ValueError(
            "Cannot delete the last remaining project. "
            "Create another project first."
        )

    proj = config.projects[name]
    db_path = Path(proj.db_path).expanduser()
    db_deleted = False

    if delete_db and db_path.is_file():
        db_path.unlink()
        db_deleted = True

    # Remove project from config
    del config.projects[name]

    # Update default_project if it pointed to the deleted project
    if config.default_project == name:
        config.default_project = next(iter(config.projects))

    save_global_config(config)

    msg = f"Deleted project '{name}'."
    if delete_db:
        if db_deleted:
            msg += f" Database file removed: {db_path}"
        else:
            msg += f" Database file not found (nothing to delete): {db_path}"
    else:
        msg += f" Database file kept: {db_path}"

    return db_deleted, msg


def _default_global_config() -> GlobalConfig:
    """Return an empty ``GlobalConfig`` with no projects.

    This is used when no config file exists yet (first run before
    ``mementic init``).  An empty projects dict means no project
    database is available — CLI commands fall back to legacy
    ``graph.json``.
    """
    return GlobalConfig()
