"""Config loading and validation for Mementic project configuration."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


__all__ = [
    "AdrDirSource",
    "LLMConfig",
    "MementicConfig",
    "OpenCodeSource",
    "OutputConfig",
    "Sources",
    "load_config",
]


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
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    extraction_model: str = "deepseek-chat"


class MementicConfig(BaseModel):
    """Root configuration for the Mementic project."""

    sources: Sources = Field(default_factory=Sources)
    output: OutputConfig = Field(default_factory=OutputConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


def load_config(path: str = ".mementic.json") -> MementicConfig:
    """Load and validate config from a JSON file.

    Args:
        path: Path to the JSON configuration file.

    Returns:
        A validated MementicConfig instance. Returns defaults for missing fields
        or when the file does not exist.

    Raises:
        ValidationError: If the JSON file contains invalid field types or values.
    """
    config_file = Path(path)
    if not config_file.is_file():
        return MementicConfig()

    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return MementicConfig()

    return MementicConfig.model_validate(data)
