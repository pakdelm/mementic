"""Session provider protocol, registry, and backward-compat re-exports.

Provides the SessionProvider protocol for agent session discovery. Three built-in
providers are auto-registered: OpenCode, Claude Code, and Generic. The registry
auto-detects which provider to use based on explicit flags, session path, or the
most recently modified session across all providers.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Protocol


# ── Utility ──────────────────────────────────────────────────────────


def expand_path(path: str) -> str:
    """Expand ~ and %ENV_VAR% in a path string.

    Args:
        path: A path string that may contain ~ or %VAR% patterns.

    Returns:
        The expanded path string with home directory and env vars resolved.
    """
    if path.startswith("~/"):
        path = str(Path.home() / path[2:])
    elif path == "~":
        path = str(Path.home())
    if "%" in path:
        while match := re.search(r"%(\w+)%", path):
            var_name = match.group(1)
            var_value = os.environ.get(var_name, "")
            path = path[: match.start()] + var_value + path[match.end() :]
    return path


# ── Protocol ─────────────────────────────────────────────────────────


class SessionProvider(Protocol):
    """Protocol for agent session discovery and reading.

    Implementations provide agent-specific logic for finding the most recent
    session log, reading its content, and optionally discovering associated
    diffs.
    """

    @property
    def agent_name(self) -> str:
        """Unique identifier for this agent (e.g. 'opencode', 'claude-code')."""
        ...

    def find_latest_session(self, log_dirs: list[str] | None = None) -> str | None:
        """Find the most recently modified session file.

        Args:
            log_dirs: Optional list of directories to search. If None, the
                provider uses its built-in platform-specific defaults.

        Returns:
            The file path of the most recent session, or None if no sessions found.
        """
        ...

    def read_session_log(self, path: str) -> str:
        """Read a session log file and return its content.

        Args:
            path: Path to the session log file.

        Returns:
            The file contents as a UTF-8 string.
        """
        ...

    def find_session_diffs(self, session_path: str) -> str | None:
        """Find diffs associated with a session, if the agent stores them.

        Args:
            session_path: Path to the session log file.

        Returns:
            Diff text, or None if this provider/agent doesn't store diffs.
        """
        ...


# ── Registry ─────────────────────────────────────────────────────────


class SessionProviderRegistry:
    """Global registry of session providers.

    Providers are registered at import time. The detect() method resolves
    the appropriate provider based on explicit flags, session path, or
    automatic discovery of the most recent session across all providers.
    """

    _instance: SessionProviderRegistry | None = None
    _providers: dict[str, SessionProvider]

    def __init__(self) -> None:
        self._providers = {}

    @classmethod
    def get_instance(cls) -> SessionProviderRegistry:
        """Return the singleton registry instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, provider: SessionProvider) -> None:
        """Register a session provider.

        Args:
            provider: A SessionProvider implementation.
        """
        self._providers[provider.agent_name] = provider

    def get(self, agent_name: str) -> SessionProvider | None:
        """Get a provider by agent name.

        Args:
            agent_name: The agent name (e.g. 'opencode').

        Returns:
            The provider, or None if not registered.
        """
        return self._providers.get(agent_name)

    def detect(
        self,
        agent: str | None = None,
        session_path: str | None = None,
        log_dirs: dict[str, list[str]] | None = None,
    ) -> tuple[SessionProvider, str | None]:
        """Detect the appropriate provider and session path.

        Resolution order:
        1. If ``agent`` specified: use that provider, discover session path.
        2. If ``session_path`` specified (no agent): try each provider to find
           one that can read it (by attempting read_session_log).
        3. Otherwise: auto-detect most recent session across all providers.

        Args:
            agent: Explicit agent name (e.g. 'opencode', 'claude-code', 'generic').
            session_path: Explicit path to a session log file.
            log_dirs: Optional per-agent log directory overrides from config,
                mapping agent name -> list of directory paths.

        Returns:
            A (provider, session_path) tuple. session_path is None if no
            session could be discovered.

        Raises:
            ValueError: If an explicit agent is given but not registered.
        """
        log_dirs = log_dirs or {}

        # Case 1: explicit agent
        if agent:
            provider = self._providers.get(agent)
            if provider is None:
                known = ", ".join(self._providers)
                raise ValueError(
                    f"Unknown agent '{agent}'. Known agents: {known}."
                )
            if session_path:
                return provider, session_path
            found = provider.find_latest_session(log_dirs.get(agent))
            return provider, found

        # Case 2: explicit session path, detect provider
        if session_path:
            for provider in self._providers.values():
                try:
                    provider.read_session_log(session_path)
                    return provider, session_path
                except (OSError, UnicodeDecodeError):
                    continue
            # If no provider can read it, use generic as fallback
            return self._providers["generic"], session_path

        # Case 3: auto-detect most recent session across all providers
        latest_file: str | None = None
        latest_mtime: float = 0.0
        latest_provider: SessionProvider | None = None

        for agent_name, provider in self._providers.items():
            found = provider.find_latest_session(log_dirs.get(agent_name))
            if found:
                try:
                    mtime = Path(found).stat().st_mtime
                    if mtime > latest_mtime:
                        latest_mtime = mtime
                        latest_file = found
                        latest_provider = provider
                except OSError:
                    continue

        if latest_provider and latest_file:
            return latest_provider, latest_file

        # Fallback: return generic provider with no session
        return self._providers.get("generic", list(self._providers.values())[0]), None


# ── Singleton access ─────────────────────────────────────────────────


def get_registry() -> SessionProviderRegistry:
    """Return the global session provider registry singleton."""
    return SessionProviderRegistry.get_instance()


# ── Backward-compat top-level functions ──────────────────────────────


def find_latest_session() -> str | None:
    """Find the most recently modified session file across all providers.

    Returns:
        The file path of the most recent session, or None if no sessions found.
    """
    registry = get_registry()
    _, session_path = registry.detect()
    return session_path


def read_session_log(path: str) -> str:
    """Read a session log file and return its content.

    Args:
        path: Path to the session log file.

    Returns:
        The file contents as a UTF-8 string.
    """
    return Path(path).read_text(encoding="utf-8", errors="replace")


# ── Auto-register built-in providers ─────────────────────────────────


def _register_builtin_providers() -> None:
    """Register the three built-in session providers with the global registry."""
    from mementic.session.opencode import OpenCodeSessionProvider
    from mementic.session.claude_code import ClaudeCodeSessionProvider
    from mementic.session.generic import GenericSessionProvider

    registry = get_registry()
    registry.register(OpenCodeSessionProvider())
    registry.register(ClaudeCodeSessionProvider())
    registry.register(GenericSessionProvider())


_register_builtin_providers()

# Re-export DEFAULT_SESSION_PATHS for backward-compat with patches on
# mementic.session.DEFAULT_SESSION_PATHS (must be after provider init to
# avoid circular import).
from mementic.session.opencode import DEFAULT_SESSION_PATHS  # noqa: E402, F401
