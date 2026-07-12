"""Local config loading and validation for Mementic.

Stored at ``.mementic/config.json`` in the repository root.  Contains only
LLM provider/model and optional session log overrides.  API keys are never
stored here — they live in the OS keyring (or environment variable for
CI / Docker Sandboxes).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import keyring
from pydantic import BaseModel, Field, model_validator


__all__ = [
    "AgentSessionConfig",
    "LLMConfig",
    "LocalConfig",
    "PROVIDER_CONFIGS",
    "SessionsConfig",
    "get_provider_config",
    "load_local_config",
    "save_local_config",
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


def get_provider_config(provider: str) -> dict[str, str | None]:
    """Return provider config dict, raising KeyError if unknown.

    Args:
        provider: Provider name (deepseek, openai, openrouter, ollama).

    Returns:
        Dict with base_url and key_env_var keys.

    Raises:
        ValueError: If the provider is not in PROVIDER_CONFIGS.
    """
    if provider not in PROVIDER_CONFIGS:
        known = ", ".join(PROVIDER_CONFIGS)
        msg = (
            f"Unknown LLM provider '{provider}'. "
            f"Supported providers: {known}."
        )
        raise ValueError(msg)
    return PROVIDER_CONFIGS[provider]


class LLMConfig(BaseModel):
    """LLM provider configuration.

    Supported providers: deepseek, openai, openrouter, ollama.

    API keys are stored in the OS keyring (primary) with environment
    variables as a fallback for CI/containers.  ``mementic init --key``
    stores the key in the keyring.
    """

    _KEYRING_SERVICE: str = "mementic"

    provider: str
    model: str

    @model_validator(mode="after")
    def _validate_provider(self: LLMConfig) -> LLMConfig:
        """Validate that the provider is known."""
        get_provider_config(self.provider)
        return self

    @property
    def extraction_model(self) -> str:
        """Return the model to use for extraction (same as model)."""
        return self.model

    @property
    def base_url(self) -> str:
        """Return the base URL for the configured provider."""
        return str(get_provider_config(self.provider)["base_url"])

    @property
    def key_env_var(self) -> str | None:
        """Return the environment variable name for the API key, or None."""
        return get_provider_config(self.provider)["key_env_var"]

    def get_api_key(self) -> str | None:
        """Read the API key — keyring first, then environment variable.

        Keyring is the primary path (stored by ``mementic init --key``).
        Environment variables are a fallback for CI, containers, and
        headless environments.

        Returns:
            The API key string, or None if neither source provides one.
        """
        env_var = self.key_env_var
        if env_var is None:
            return None

        # 1. OS keyring (primary — interactive users)
        try:
            key = keyring.get_password(self._KEYRING_SERVICE, self.provider)
            if key:
                return key
        except Exception:
            pass  # keyring backend unavailable — fall through to env

        # 2. Environment variable (fallback — CI, containers, headless)
        return os.environ.get(env_var)

    def set_api_key(self, key: str) -> None:
        """Store the API key in the OS keyring.

        Args:
            key: The API key string to store.

        Raises:
            RuntimeError: If the provider does not require a key (e.g. ollama),
                or if the keyring backend is unavailable.
        """
        env_var = self.key_env_var
        if env_var is None:
            raise RuntimeError(
                f"Provider '{self.provider}' does not require an API key."
            )
        try:
            keyring.set_password(self._KEYRING_SERVICE, self.provider, key)
        except Exception:
            raise RuntimeError(
                f"Failed to store API key in OS keyring for provider "
                f"'{self.provider}'. The keyring backend may be unavailable.\n"
                f"\n"
                f"  Set the environment variable instead:\n"
                f"    export {env_var}=\"your-key-here\"\n"
            )

    def ensure_api_key(self) -> str:
        """Return the API key or raise a clear error message.

        Tries keyring first, then environment variable.  The error
        message tells the user exactly which env var to set or to run
        ``mementic init --key`` to store in the keyring.

        Returns:
            The API key string.

        Raises:
            RuntimeError: If the configured provider requires an API key
                and no key is available from keyring or environment.
        """
        env_var = self.key_env_var
        if env_var is None:
            return "ollama"  # dummy — not used for auth but needed for OpenAI client init

        key = self.get_api_key()
        if key:
            return key

        msg = (
            f"Mementic requires an API key for provider '{self.provider}'.\n"
            f"\n"
            f"  To store in OS keyring (recommended):\n"
            f"    mementic init --key\n"
            f"\n"
            f"  Or set the environment variable:\n"
            f"    export {env_var}=\"your-key-here\"\n"
            f"\n"
            f"  Or switch to a local provider in .mementic/config.json:\n"
            f"    {{ \"llm\": {{ \"provider\": \"ollama\" }} }}\n"
        )
        raise RuntimeError(msg)


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


class LocalConfig(BaseModel):
    """Local per-repo configuration stored at ``.mementic/config.json``.

    Contains only LLM provider/model and optional session log overrides.
    API keys are never stored in this file.
    """

    llm: LLMConfig
    sessions: SessionsConfig | None = None

    def get_llm_config(self) -> LLMConfig:
        """Return the LLM configuration."""
        return self.llm


def load_local_config() -> LocalConfig | None:
    """Load the local config from ``.mementic/config.json``.

    Returns:
        A ``LocalConfig`` instance, or ``None`` if the file does not exist.

    Raises:
        ValueError: If the file exists but contains invalid JSON or schema.
    """
    config_path = Path(".mementic/config.json")
    if not config_path.is_file():
        return None

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        sys.stderr.write(
            f"Config file {config_path} is corrupt. "
            "Run 'mementic init' to regenerate.\n"
        )
        raise ValueError(f"Corrupt config file: {config_path}")

    return LocalConfig.model_validate(data)


def save_local_config(config: LocalConfig) -> None:
    """Write a ``LocalConfig`` to ``.mementic/config.json``.

    Creates the ``.mementic/`` directory if it is missing.

    Args:
        config: The validated local config to persist.
    """
    config_path = Path(".mementic/config.json")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json", exclude_none=True)
    config_path.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )
