"""Tests for mementic.config (Spec 22: local .mementic/config.json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mementic.config import (
    AgentSessionConfig,
    LLMConfig,
    LocalConfig,
    SessionsConfig,
    load_local_config,
    save_local_config,
    get_provider_config,
)


class TestLLMConfig:
    """Tests for the simplified LLMConfig model (Spec 22)."""

    def test_valid_provider_deepseek(self) -> None:
        """LLMConfig with valid provider passes validation."""
        cfg = LLMConfig(provider="deepseek", model="deepseek-chat")
        assert cfg.provider == "deepseek"
        assert cfg.model == "deepseek-chat"
        assert cfg.base_url == "https://api.deepseek.com"
        assert cfg.key_env_var == "DEEPSEEK_API_KEY"

    def test_valid_provider_openai(self) -> None:
        """LLMConfig with openai provider."""
        cfg = LLMConfig(provider="openai", model="gpt-4o")
        assert cfg.provider == "openai"
        assert cfg.base_url == "https://api.openai.com/v1"
        assert cfg.key_env_var == "OPENAI_API_KEY"

    def test_valid_provider_openrouter(self) -> None:
        """LLMConfig with openrouter provider."""
        cfg = LLMConfig(provider="openrouter", model="deepseek/deepseek-chat")
        assert cfg.provider == "openrouter"
        assert cfg.base_url == "https://openrouter.ai/api/v1"
        assert cfg.key_env_var == "OPENROUTER_API_KEY"

    def test_valid_provider_ollama(self) -> None:
        """LLMConfig with ollama provider has no API key requirement."""
        cfg = LLMConfig(provider="ollama", model="llama3")
        assert cfg.provider == "ollama"
        assert cfg.base_url == "http://localhost:11434/v1"
        assert cfg.key_env_var is None

    def test_unknown_provider_raises(self) -> None:
        """Unknown provider raises ValidationError."""
        with pytest.raises(ValidationError, match="Unknown LLM provider"):
            LLMConfig(provider="nonexistent", model="x")

    def test_extraction_model_property(self) -> None:
        """extraction_model property returns the model name."""
        cfg = LLMConfig(provider="deepseek", model="deepseek-v3")
        assert cfg.extraction_model == "deepseek-v3"

    def test_ensure_api_key_raises_when_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ensure_api_key raises RuntimeError when key missing."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = LLMConfig(provider="openai", model="gpt-4o")
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            cfg.ensure_api_key()

    def test_ensure_api_key_returns_key_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ensure_api_key returns the key when env var is set."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        cfg = LLMConfig(provider="openai", model="gpt-4o")
        assert cfg.ensure_api_key() == "sk-test-123"

    def test_ensure_api_key_ollama_returns_dummy(self) -> None:
        """ensure_api_key for ollama returns a dummy value."""
        cfg = LLMConfig(provider="ollama", model="llama3")
        assert cfg.ensure_api_key() == "ollama"


class TestLocalConfig:
    """Tests for the LocalConfig model (Spec 22)."""

    def test_valid_local_config(self) -> None:
        """LocalConfig with valid LLM config passes validation."""
        cfg = LocalConfig(llm=LLMConfig(provider="deepseek", model="deepseek-chat"))
        assert cfg.llm.provider == "deepseek"
        assert cfg.sessions is None

    def test_local_config_with_sessions(self) -> None:
        """LocalConfig with sessions config."""
        sessions = SessionsConfig(
            opencode=AgentSessionConfig(log_dirs=["/custom/logs"]),
        )
        cfg = LocalConfig(
            llm=LLMConfig(provider="deepseek", model="deepseek-chat"),
            sessions=sessions,
        )
        assert cfg.sessions is not None
        assert cfg.sessions.opencode.log_dirs == ["/custom/logs"]

    def test_local_config_unknown_provider_fails(self) -> None:
        """LocalConfig with unknown provider raises ValidationError."""
        with pytest.raises(ValidationError, match="Unknown LLM provider"):
            LocalConfig(llm=LLMConfig(provider="unknown", model="x"))

    def test_get_llm_config(self) -> None:
        """get_llm_config returns the LLM config."""
        llm = LLMConfig(provider="openai", model="gpt-4o")
        cfg = LocalConfig(llm=llm)
        assert cfg.get_llm_config() is llm


class TestLoadSaveLocalConfig:
    """Tests for load_local_config and save_local_config (Spec 22)."""

    def test_load_returns_none_when_no_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """load_local_config returns None when .mementic/config.json doesn't exist."""
        monkeypatch.chdir(tmp_path)
        assert load_local_config() is None

    def test_save_and_load_roundtrip(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """save_local_config + load_local_config roundtrips."""
        monkeypatch.chdir(tmp_path)
        original = LocalConfig(
            llm=LLMConfig(provider="openai", model="gpt-4o"),
            sessions=SessionsConfig(
                cursor=AgentSessionConfig(log_dirs=["/custom/cursor"]),
            ),
        )
        save_local_config(original)
        loaded = load_local_config()
        assert loaded is not None
        assert loaded.llm.provider == "openai"
        assert loaded.llm.model == "gpt-4o"
        assert loaded.sessions is not None
        assert loaded.sessions.cursor.log_dirs == ["/custom/cursor"]

    def test_load_returns_valid_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """load_local_config returns a LocalConfig when valid file exists."""
        monkeypatch.chdir(tmp_path)
        mementic_dir = tmp_path / ".mementic"
        mementic_dir.mkdir()
        config_file = mementic_dir / "config.json"
        config_file.write_text(json.dumps({
            "llm": {"provider": "deepseek", "model": "deepseek-chat"},
        }))
        cfg = load_local_config()
        assert cfg is not None
        assert cfg.llm.provider == "deepseek"
        assert cfg.llm.model == "deepseek-chat"

    def test_load_corrupt_json_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """load_local_config raises ValueError for corrupt JSON."""
        monkeypatch.chdir(tmp_path)
        mementic_dir = tmp_path / ".mementic"
        mementic_dir.mkdir()
        config_file = mementic_dir / "config.json"
        config_file.write_text("{not valid json")
        with pytest.raises(ValueError, match="Corrupt config file"):
            load_local_config()

    def test_save_creates_directory(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """save_local_config creates .mementic/ directory if missing."""
        monkeypatch.chdir(tmp_path)
        cfg = LocalConfig(llm=LLMConfig(provider="deepseek", model="deepseek-chat"))
        save_local_config(cfg)
        assert (tmp_path / ".mementic" / "config.json").exists()

    def test_save_exclude_none(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """save_local_config excludes None fields from JSON output."""
        monkeypatch.chdir(tmp_path)
        cfg = LocalConfig(llm=LLMConfig(provider="deepseek", model="deepseek-chat"))
        save_local_config(cfg)
        content = (tmp_path / ".mementic" / "config.json").read_text()
        data = json.loads(content)
        assert "sessions" not in data


class TestGetProviderConfig:
    """Tests for get_provider_config helper."""

    def test_returns_config_for_known_provider(self) -> None:
        """get_provider_config returns the config dict for a known provider."""
        cfg = get_provider_config("deepseek")
        assert cfg["base_url"] == "https://api.deepseek.com"
        assert cfg["key_env_var"] == "DEEPSEEK_API_KEY"

    def test_raises_for_unknown_provider(self) -> None:
        """get_provider_config raises ValueError for unknown provider."""
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_provider_config("nonexistent")


class TestAgentSessionConfig:
    """Tests for AgentSessionConfig model."""

    def test_default_log_dirs_empty(self) -> None:
        """Default log_dirs is an empty list."""
        cfg = AgentSessionConfig()
        assert cfg.log_dirs == []

    def test_custom_log_dirs(self) -> None:
        """log_dirs can be set explicitly."""
        cfg = AgentSessionConfig(log_dirs=["/path/to/logs", "/another/path"])
        assert cfg.log_dirs == ["/path/to/logs", "/another/path"]


class TestSessionsConfig:
    """Tests for SessionsConfig model."""

    def test_all_fields_have_defaults(self) -> None:
        """All session agent fields default to empty AgentSessionConfig."""
        cfg = SessionsConfig()
        assert cfg.opencode.log_dirs == []
        assert cfg.claude_code.log_dirs == []
        assert cfg.generic.log_dirs == []
        assert cfg.cursor.log_dirs == []
        assert cfg.windsurf.log_dirs == []

    def test_claude_code_alias(self) -> None:
        """claude-code JSON key maps to claude_code field."""
        data = {"claude-code": {"log_dirs": ["/claude/logs"]}}
        cfg = SessionsConfig.model_validate(data)
        assert cfg.claude_code.log_dirs == ["/claude/logs"]
