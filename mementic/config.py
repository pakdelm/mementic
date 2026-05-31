"""Config loading and validation for Mementic project configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


__all__ = [
    "AdrDirSource",
    "LLMConfig",
    "MementicConfig",
    "OpenCodeSource",
    "OutputConfig",
    "PROVIDER_CONFIGS",
    "Sources",
    "get_provider_config",
    "load_config",
]

# ── Provider metadata ──────────────────────────────────────────────

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


# ── Source models ──────────────────────────────────────────────────


class AdrDirSource(BaseModel):
    """A directory containing ADR (Architecture Decision Record) files."""

    path: str


class OpenCodeSource(BaseModel):
    """A path to OpenCode instruction files."""

    path: str


class Sources(BaseModel):
    """Configuration for knowledge sources."""

    adr_dirs: list[AdrDirSource] = Field(default_factory=list)
    opencode_instructions: list[OpenCodeSource] = Field(default_factory=list)


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
                f"  Or switch to a different provider in .mementic.json:\n"
                f"  {{ \"llm\": {{ \"provider\": \"ollama\" }} }} (local, no key needed)\n"
                f"  {{ \"llm\": {{ \"provider\": \"openai\", \"model\": \"gpt-4o\" }} }}"
            )
            raise RuntimeError(msg)
        return key


class MementicConfig(BaseModel):
    """Root configuration for the Mementic project."""

    sources: Sources = Field(default_factory=Sources)
    output: OutputConfig = Field(default_factory=OutputConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


def load_config(path: str = ".mementic.json") -> MementicConfig:
    """Load and validate config from a JSON file.

    If no explicit path is given, checks the MEMENTIC_CONFIG environment
    variable first, then falls back to .mementic.json.

    Args:
        path: Path to the JSON configuration file. If the default
            (".mementic.json") and MEMENTIC_CONFIG is set, that variable
            is used instead.

    Returns:
        A validated MementicConfig instance. Returns defaults for missing fields
        or when the file does not exist.

    Raises:
        ValidationError: If the JSON file contains invalid field types, values,
            or an unknown LLM provider.
    """
    # Respect MEMENTIC_CONFIG env var when no explicit path given
    if path == ".mementic.json":
        path = os.environ.get("MEMENTIC_CONFIG", path)

    config_file = Path(path)
    if not config_file.is_file():
        return MementicConfig()

    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return MementicConfig()

    return MementicConfig.model_validate(data)
