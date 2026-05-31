"""Tests for mementic.config."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from mementic.config import MementicConfig, load_config


class TestLoadConfig:
    """Tests for load_config()."""

    def test_loads_valid_json_file(self) -> None:
        """load_config returns MementicConfig from a valid JSON file."""
        data = {
            "sources": {
                "adr_dirs": [{"path": "docs/adr"}],
                "opencode_instructions": [{"path": ".opencode/instructions"}],
            },
            "output": {"graph_path": "./custom/graph.json"},
            "llm": {"provider": "openai", "model": "gpt-4"},
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json.dump(data, tmp)

        try:
            config = load_config(tmp.name)
            assert isinstance(config, MementicConfig)
            assert len(config.sources.adr_dirs) == 1
            assert config.sources.adr_dirs[0].path == "docs/adr"
            assert len(config.sources.opencode_instructions) == 1
            assert config.sources.opencode_instructions[0].path == ".opencode/instructions"
            assert config.output.graph_path == "./custom/graph.json"
            assert config.llm.provider == "openai"
            assert config.llm.model == "gpt-4"
        finally:
            Path(tmp.name).unlink()

    def test_returns_defaults_when_no_file_exists(self) -> None:
        """load_config returns default MementicConfig when file is missing."""
        config = load_config("/nonexistent/path/.mementic.json")
        assert isinstance(config, MementicConfig)
        assert config.sources.adr_dirs == []
        assert config.sources.opencode_instructions == []
        assert config.output.graph_path == "./.mementic/graph.json"
        assert config.llm.provider == "deepseek"
        assert config.llm.model == "deepseek-chat"
        assert config.llm.extraction_model == "deepseek-chat"

    def test_invalid_fields_raise_validation_error(self) -> None:
        """load_config raises ValidationError for invalid field types."""
        data = {"sources": {"adr_dirs": "not_a_list"}}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json.dump(data, tmp)

        try:
            with pytest.raises(ValidationError):
                load_config(tmp.name)
        finally:
            Path(tmp.name).unlink()

    def test_custom_graph_path(self) -> None:
        """load_config correctly reads a custom graph_path."""
        data = {"output": {"graph_path": "/tmp/mementic/graph.json"}}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json.dump(data, tmp)

        try:
            config = load_config(tmp.name)
            assert config.output.graph_path == "/tmp/mementic/graph.json"
        finally:
            Path(tmp.name).unlink()

    def test_partial_config_uses_defaults_for_missing_fields(self) -> None:
        """load_config fills in defaults for fields omitted from JSON."""
        data: dict = {}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json.dump(data, tmp)

        try:
            config = load_config(tmp.name)
            assert isinstance(config, MementicConfig)
            assert config.sources.adr_dirs == []
            assert config.output.graph_path == "./.mementic/graph.json"
            assert config.llm.provider == "deepseek"
        finally:
            Path(tmp.name).unlink()

    def test_invalid_json_returns_defaults(self) -> None:
        """load_config returns default config when JSON is malformed."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            tmp.write("{not valid json")

        try:
            config = load_config(tmp.name)
            assert isinstance(config, MementicConfig)
            assert config.sources.adr_dirs == []
            assert config.sources.opencode_instructions == []
        finally:
            Path(tmp.name).unlink()

