"""Tests for mementic.config."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from mementic.config import LLMConfig, MementicConfig, SessionsConfig, load_config
from mementic.graph import GraphStore


class TestLoadConfig:
    """Tests for load_config()."""

    def test_loads_valid_json_file(self) -> None:
        """load_config returns MementicConfig from a valid JSON file."""
        data = {
            "sources": {
                "adr_dirs": [{"path": "docs/adr"}],
                "opencode_instructions": [{"path": ".opencode/instructions"}],
                "claude_instructions": [{"path": ".claude/instructions"}],
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
            assert len(config.sources.claude_instructions) == 1
            assert config.sources.claude_instructions[0].path == ".claude/instructions"
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
        assert config.sources.claude_instructions == []
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
            assert config.sources.claude_instructions == []
        finally:
            Path(tmp.name).unlink()


class TestMultiProvider:
    """Tests for multi-provider LLM configuration (Spec 06a D3)."""

    def test_provider_default_deepseek(self) -> None:
        """Default provider is deepseek with correct base_url and env var."""
        config = MementicConfig()
        assert config.llm.provider == "deepseek"
        assert config.llm.base_url == "https://api.deepseek.com"
        assert config.llm.key_env_var == "DEEPSEEK_API_KEY"

    def test_provider_openai_config(self) -> None:
        """OpenAI provider has correct base_url and env var."""
        config = MementicConfig(
            llm=LLMConfig(provider="openai", model="gpt-4o")
        )
        assert config.llm.provider == "openai"
        assert config.llm.base_url == "https://api.openai.com/v1"
        assert config.llm.key_env_var == "OPENAI_API_KEY"

    def test_provider_openrouter_config(self) -> None:
        """OpenRouter provider has correct base_url and env var."""
        config = MementicConfig(
            llm=LLMConfig(provider="openrouter", model="anthropic/claude-3-sonnet")
        )
        assert config.llm.provider == "openrouter"
        assert config.llm.base_url == "https://openrouter.ai/api/v1"
        assert config.llm.key_env_var == "OPENROUTER_API_KEY"

    def test_provider_ollama_config(self) -> None:
        """Ollama provider has local base_url and no API key required."""
        config = MementicConfig(
            llm=LLMConfig(provider="ollama", model="llama3")
        )
        assert config.llm.provider == "ollama"
        assert config.llm.base_url == "http://localhost:11434/v1"
        assert config.llm.key_env_var is None

    def test_unknown_provider_raises_validation_error(self) -> None:
        """Unknown provider raises ValidationError during model validation."""
        with pytest.raises(ValidationError, match="Unknown LLM provider"):
            MementicConfig(
                llm=LLMConfig(provider="nonexistent", model="x")
            )

    def test_ensure_api_key_raises_when_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ensure_api_key raises RuntimeError with env var name when key missing."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        config = MementicConfig(
            llm=LLMConfig(provider="openai", model="gpt-4o")
        )
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            config.llm.ensure_api_key()

    def test_ensure_api_key_returns_key_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ensure_api_key returns the key when the env var is set."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        config = MementicConfig(
            llm=LLMConfig(provider="openai", model="gpt-4o")
        )
        assert config.llm.ensure_api_key() == "sk-test-123"

    def test_ensure_api_key_ollama_returns_dummy(self) -> None:
        """ensure_api_key for ollama returns a dummy value (no key needed)."""
        config = MementicConfig(
            llm=LLMConfig(provider="ollama", model="llama3")
        )
        assert config.llm.ensure_api_key() == "ollama"

    def test_missing_key_error_message_includes_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Error message names the provider and its required env var."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        config = MementicConfig(
            llm=LLMConfig(provider="openrouter", model="openai/gpt-4o")
        )
        with pytest.raises(RuntimeError) as exc_info:
            config.llm.ensure_api_key()
        assert "openrouter" in str(exc_info.value)
        assert "OPENROUTER_API_KEY" in str(exc_info.value)


# ── C1: Cursor + Windsurf source models ─────────────────────────────


class TestCursorWindsurfSources:
    """Tests for CursorSource and WindsurfSource config models (Spec 09c C1)."""

    def test_sources_model_has_cursor_field(self) -> None:
        """Sources() has cursor_instructions field defaulting to empty list."""
        from mementic.config import Sources
        sources = Sources()
        assert hasattr(sources, "cursor_instructions")
        assert sources.cursor_instructions == []

    def test_sources_model_has_windsurf_field(self) -> None:
        """Sources() has windsurf_instructions field defaulting to empty list."""
        from mementic.config import Sources
        sources = Sources()
        assert hasattr(sources, "windsurf_instructions")
        assert sources.windsurf_instructions == []

    def test_cursor_source_model(self) -> None:
        """CursorSource model stores a path string."""
        from mementic.config import CursorSource
        source = CursorSource(path=".cursor/instructions")
        assert source.path == ".cursor/instructions"

    def test_windsurf_source_model(self) -> None:
        """WindsurfSource model stores a path string."""
        from mementic.config import WindsurfSource
        source = WindsurfSource(path=".windsurf/instructions")
        assert source.path == ".windsurf/instructions"

    def test_cursor_instructions_in_full_config(self) -> None:
        """MementicConfig with cursor_instructions deserializes correctly."""
        data = {
            "sources": {
                "cursor_instructions": [{"path": ".cursor/instructions"}],
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json.dump(data, tmp)

        try:
            config = load_config(tmp.name)
            assert len(config.sources.cursor_instructions) == 1
            assert config.sources.cursor_instructions[0].path == ".cursor/instructions"
        finally:
            Path(tmp.name).unlink()

    def test_windsurf_instructions_in_full_config(self) -> None:
        """MementicConfig with windsurf_instructions deserializes correctly."""
        data = {
            "sources": {
                "windsurf_instructions": [{"path": ".windsurf/instructions"}],
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json.dump(data, tmp)

        try:
            config = load_config(tmp.name)
            assert len(config.sources.windsurf_instructions) == 1
            assert config.sources.windsurf_instructions[0].path == ".windsurf/instructions"
        finally:
            Path(tmp.name).unlink()


# ── C4: Cursor + Windsurf session config ──────────────────────────────


class TestCursorWindsurfSessions:
    """Tests for cursor and windsurf fields on SessionsConfig (Spec 09c C4)."""

    def test_sessions_config_has_cursor_field(self) -> None:
        """SessionsConfig() has a cursor field with AgentSessionConfig defaults."""
        sessions = SessionsConfig()
        assert hasattr(sessions, "cursor")
        assert sessions.cursor.log_dirs == []

    def test_sessions_config_has_windsurf_field(self) -> None:
        """SessionsConfig() has a windsurf field with AgentSessionConfig defaults."""
        sessions = SessionsConfig()
        assert hasattr(sessions, "windsurf")
        assert sessions.windsurf.log_dirs == []

    def test_cursor_sessions_in_full_config(self) -> None:
        """MementicConfig with sessions.cursor deserializes correctly."""
        data = {
            "sessions": {
                "cursor": {"log_dirs": ["/custom/cursor/logs"]},
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json.dump(data, tmp)

        try:
            config = load_config(tmp.name)
            assert config.sessions.cursor.log_dirs == ["/custom/cursor/logs"]
        finally:
            Path(tmp.name).unlink()

    def test_windsurf_sessions_in_full_config(self) -> None:
        """MementicConfig with sessions.windsurf deserializes correctly."""
        data = {
            "sessions": {
                "windsurf": {"log_dirs": ["/custom/windsurf/logs"]},
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json.dump(data, tmp)

        try:
            config = load_config(tmp.name)
            assert config.sessions.windsurf.log_dirs == ["/custom/windsurf/logs"]
        finally:
            Path(tmp.name).unlink()


# ── Spec 09b B4: Config corruption recovery ─────────────────────────


class TestConfigCorruption:
    """Tests for config corruption recovery (Spec 09b B4)."""

    def test_corrupt_config_warns(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Corrupt JSON config prints warning to stderr and returns defaults."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            tmp.write("{not valid json at all")

        try:
            config = load_config(tmp.name)
            # Should return defaults despite corrupt file
            assert isinstance(config, MementicConfig)
            assert config.sources.adr_dirs == []
            assert config.llm.provider == "deepseek"

            # Verify stderr warning was printed
            captured = capsys.readouterr()
            assert "corrupt" in captured.err.lower()
            assert "mementic init" in captured.err.lower()
        finally:
            Path(tmp.name).unlink()

    def test_corrupt_config_does_not_crash(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Corrupt JSON does not raise an exception — returns defaults gracefully."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            tmp.write("{broken json [[[")

        try:
            # Should not raise
            config = load_config(tmp.name)
            assert isinstance(config, MementicConfig)
            assert config.output.graph_path == "./.mementic/graph.json"
        finally:
            Path(tmp.name).unlink()

    def test_missing_source_directory_warns(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """run_init warns about missing source directories but continues."""
        from mementic.pipeline import run_init

        config_path = tmp_path / ".mementic.json"
        config_path.write_text(json.dumps({
            "sources": {
                "adr_dirs": [{"path": str(tmp_path / "nonexistent_adr")}],
                "opencode_instructions": [{"path": str(tmp_path / "nonexistent_oc")}],
                "claude_instructions": [{"path": str(tmp_path / "nonexistent_claude")}],
            },
            "output": {"graph_path": str(tmp_path / ".mementic" / "graph.json")},
            "llm": {"provider": "deepseek", "model": "deepseek-chat"},
        }))

        # Should not raise — warns and continues
        graph, usage = run_init(str(config_path))

        captured = capsys.readouterr()
        stderr_output = captured.err

        assert "nonexistent_adr" in stderr_output
        assert "not found" in stderr_output.lower()
        assert isinstance(graph, GraphStore)

