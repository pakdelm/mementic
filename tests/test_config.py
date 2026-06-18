"""Tests for mementic.config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mementic.config import (
    AdrDirSource,
    AgentSessionConfig,
    ClaudeSource,
    CursorSource,
    GlobalConfig,
    LLMConfig,
    MementicConfig,
    OpenCodeSource,
    OutputConfig,
    ProjectEntry,
    RepoEntry,
    SessionsConfig,
    Sources,
    WindsurfSource,
    delete_project,
    load_config,
    load_global_config,
    resolve_project,
    save_global_config,
)
from mementic.graph import GraphStore


def _set_repo_sources(
    repo_path: str, **sources: list
) -> None:
    """Mutate the registered repo's Sources in the global config and persist.

    Args:
        repo_path: Absolute path of the repo to find.
        **sources: Keyword args forwarded to ``Sources(...)`` (e.g.
            ``adr_dirs=[AdrDirSource(path="docs/adr")]``).
    """
    global_cfg = load_global_config()
    for proj in global_cfg.projects.values():
        for repo in proj.repos.values():
            if repo.path == repo_path:
                repo.sources = Sources(**sources)
    save_global_config(global_cfg)


def _set_global_llm(llm: LLMConfig) -> None:
    """Set the shared LLM config on the global config and persist."""
    global_cfg = load_global_config()
    global_cfg.llm = llm
    save_global_config(global_cfg)


def _set_global_sessions(sessions: SessionsConfig) -> None:
    """Set the shared sessions config on the global config and persist."""
    global_cfg = load_global_config()
    global_cfg.sessions = sessions
    save_global_config(global_cfg)


class TestLoadConfig:
    """Tests for load_config()."""

    def test_loads_valid_json_file(self, tmp_global_config) -> None:
        """load_config composes MementicConfig from global config + repo entry."""
        config_path, repo_path = tmp_global_config

        _set_repo_sources(
            repo_path,
            adr_dirs=[AdrDirSource(path="docs/adr")],
            opencode_instructions=[OpenCodeSource(path=".opencode/instructions")],
            claude_instructions=[ClaudeSource(path=".claude/instructions")],
        )
        _set_global_llm(LLMConfig(provider="openai", model="gpt-4"))

        config = load_config(repo_path=repo_path)
        assert isinstance(config, MementicConfig)
        assert len(config.sources.adr_dirs) == 1
        assert config.sources.adr_dirs[0].path == "docs/adr"
        assert len(config.sources.opencode_instructions) == 1
        assert config.sources.opencode_instructions[0].path == ".opencode/instructions"
        assert len(config.sources.claude_instructions) == 1
        assert config.sources.claude_instructions[0].path == ".claude/instructions"
        # graph_path is no longer per-repo; comes from the model default.
        assert config.output.graph_path == "./.mementic/graph.json"
        assert config.llm.provider == "openai"
        assert config.llm.model == "gpt-4"

    def test_returns_defaults_when_no_file_exists(self) -> None:
        """load_config returns default MementicConfig when no global config exists."""
        # The autouse _isolate_global_config fixture points MEMENTIC_GLOBAL_CONFIG
        # at a nonexistent path, so load_global_config returns defaults.
        config = load_config()
        assert isinstance(config, MementicConfig)
        assert config.sources.adr_dirs == []
        assert config.sources.opencode_instructions == []
        assert config.sources.claude_instructions == []
        assert config.output.graph_path == "./.mementic/graph.json"
        assert config.llm.provider == "deepseek"
        assert config.llm.model == "deepseek-chat"
        assert config.llm.extraction_model == "deepseek-chat"

    def test_invalid_fields_raise_validation_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_global_config raises ValidationError for invalid field types."""
        config_file = tmp_path / "config.json"
        # Valid JSON but invalid field type: adr_dirs must be a list.
        config_file.write_text(
            json.dumps(
                {
                    "projects": {
                        "main": {
                            "db_path": "x",
                            "repos": {
                                "/r": {
                                    "path": "/r",
                                    "sources": {"adr_dirs": "not_a_list"},
                                }
                            },
                        }
                    }
                }
            )
        )
        monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(config_file))

        with pytest.raises(ValidationError):
            load_global_config()

    def test_custom_graph_path(self) -> None:
        """MementicConfig accepts a custom graph_path via the model directly."""
        config = MementicConfig(output=OutputConfig(graph_path="/tmp/mementic/graph.json"))
        assert config.output.graph_path == "/tmp/mementic/graph.json"

    def test_partial_config_uses_defaults_for_missing_fields(
        self, tmp_global_config
    ) -> None:
        """load_config fills in defaults when the repo entry has empty sources."""
        config_path, repo_path = tmp_global_config

        config = load_config(repo_path=repo_path)
        assert isinstance(config, MementicConfig)
        assert config.sources.adr_dirs == []
        assert config.output.graph_path == "./.mementic/graph.json"
        assert config.llm.provider == "deepseek"

    def test_invalid_json_returns_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_global_config returns default config when JSON is malformed."""
        config_file = tmp_path / "config.json"
        config_file.write_text("{not valid json")
        monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(config_file))

        config = load_global_config()
        assert isinstance(config, GlobalConfig)
        assert config.projects == {}
        assert config.llm.provider == "deepseek"


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
        sources = Sources()
        assert hasattr(sources, "cursor_instructions")
        assert sources.cursor_instructions == []

    def test_sources_model_has_windsurf_field(self) -> None:
        """Sources() has windsurf_instructions field defaulting to empty list."""
        sources = Sources()
        assert hasattr(sources, "windsurf_instructions")
        assert sources.windsurf_instructions == []

    def test_cursor_source_model(self) -> None:
        """CursorSource model stores a path string."""
        source = CursorSource(path=".cursor/instructions")
        assert source.path == ".cursor/instructions"

    def test_windsurf_source_model(self) -> None:
        """WindsurfSource model stores a path string."""
        source = WindsurfSource(path=".windsurf/instructions")
        assert source.path == ".windsurf/instructions"

    def test_cursor_instructions_in_full_config(self, tmp_global_config) -> None:
        """Cursor sources set on the repo entry are surfaced by load_config."""
        config_path, repo_path = tmp_global_config

        _set_repo_sources(
            repo_path,
            cursor_instructions=[CursorSource(path=".cursor/instructions")],
        )

        config = load_config(repo_path=repo_path)
        assert len(config.sources.cursor_instructions) == 1
        assert config.sources.cursor_instructions[0].path == ".cursor/instructions"

    def test_windsurf_instructions_in_full_config(self, tmp_global_config) -> None:
        """Windsurf sources set on the repo entry are surfaced by load_config."""
        config_path, repo_path = tmp_global_config

        _set_repo_sources(
            repo_path,
            windsurf_instructions=[WindsurfSource(path=".windsurf/instructions")],
        )

        config = load_config(repo_path=repo_path)
        assert len(config.sources.windsurf_instructions) == 1
        assert config.sources.windsurf_instructions[0].path == ".windsurf/instructions"


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

    def test_cursor_sessions_in_full_config(self, tmp_global_config) -> None:
        """Sessions set on the global config are surfaced by load_config."""
        config_path, repo_path = tmp_global_config

        _set_global_sessions(
            SessionsConfig(cursor=AgentSessionConfig(log_dirs=["/custom/cursor/logs"]))
        )

        config = load_config(repo_path=repo_path)
        assert config.sessions.cursor.log_dirs == ["/custom/cursor/logs"]

    def test_windsurf_sessions_in_full_config(self, tmp_global_config) -> None:
        """Sessions set on the global config are surfaced by load_config."""
        config_path, repo_path = tmp_global_config

        _set_global_sessions(
            SessionsConfig(windsurf=AgentSessionConfig(log_dirs=["/custom/windsurf/logs"]))
        )

        config = load_config(repo_path=repo_path)
        assert config.sessions.windsurf.log_dirs == ["/custom/windsurf/logs"]


# ── Spec 09b B4: Config corruption recovery ─────────────────────────


class TestConfigCorruption:
    """Tests for config corruption recovery (Spec 09b B4)."""

    def test_corrupt_config_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Corrupt global config prints warning to stderr and returns defaults."""
        config_file = tmp_path / "config.json"
        config_file.write_text("{not valid json at all")
        monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(config_file))

        config = load_global_config()
        # Should return defaults despite corrupt file
        assert isinstance(config, GlobalConfig)
        assert config.projects == {}
        assert config.llm.provider == "deepseek"

        # Verify stderr warning was printed
        captured = capsys.readouterr()
        assert "corrupt" in captured.err.lower()
        assert "mementic init" in captured.err.lower()

    def test_corrupt_config_does_not_crash(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Corrupt JSON does not raise — returns defaults gracefully."""
        config_file = tmp_path / "config.json"
        config_file.write_text("{broken json [[[")
        monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(config_file))

        # Should not raise
        config = load_global_config()
        assert isinstance(config, GlobalConfig)
        assert config.projects == {}
        assert config.llm.provider == "deepseek"

    def test_missing_source_directory_warns(
        self,
        tmp_global_config,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """run_init warns about missing source directories but continues."""
        from mementic.pipeline import run_init

        config_path, repo_path = tmp_global_config
        nonexistent_adr = str(Path(repo_path) / "nonexistent_adr")
        nonexistent_oc = str(Path(repo_path) / "nonexistent_oc")
        nonexistent_claude = str(Path(repo_path) / "nonexistent_claude")

        _set_repo_sources(
            repo_path,
            adr_dirs=[AdrDirSource(path=nonexistent_adr)],
            opencode_instructions=[OpenCodeSource(path=nonexistent_oc)],
            claude_instructions=[ClaudeSource(path=nonexistent_claude)],
        )

        # Should not raise — warns and continues. no_scan skips the codebase
        # LLM scan so the test stays deterministic without an API key.
        graph, usage = run_init(repo_path=repo_path, no_scan=True)

        captured = capsys.readouterr()
        stderr_output = captured.err

        assert "nonexistent_adr" in stderr_output
        assert "not found" in stderr_output.lower()
        assert isinstance(graph, GraphStore)


# ── Spec 12b: Global multi-project config ─────────────────────────


class TestGlobalConfig:
    """Tests for GlobalConfig, ProjectEntry, RepoEntry, and config I/O."""

    # ── load / save ─────────────────────────────────────────────────

    def test_load_global_config_returns_default_when_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When no config file, returns empty config with no projects."""
        config_file = tmp_path / "nonexistent" / "config.json"
        monkeypatch.setattr(
            "mementic.config.get_global_config_path", lambda: config_file
        )

        config = load_global_config()

        assert config.version == 1
        assert config.default_project == "main"
        assert config.projects == {}  # empty — no config on disk yet

    def test_save_and_reload_roundtrip(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Saves a GlobalConfig with projects, reloads, verifies fields match."""
        config_file = tmp_path / ".mementic" / "config.json"
        config_file.parent.mkdir(parents=True)
        monkeypatch.setattr(
            "mementic.config.get_global_config_path", lambda: config_file
        )

        original = GlobalConfig(
            version=1,
            default_project="main",
            projects={
                "main": ProjectEntry(
                    description="Primary project",
                    db_path="~/.mementic/projects/main.mementic.db",
                    repos={
                        "/abs/path/to/repo": RepoEntry(
                            path="/abs/path/to/repo", label="my-repo"
                        ),
                    },
                ),
                "secondary": ProjectEntry(
                    db_path="~/.mementic/projects/secondary.mementic.db",
                ),
            },
        )
        save_global_config(original)

        loaded = load_global_config()
        assert loaded.version == original.version
        assert loaded.default_project == original.default_project
        assert set(loaded.projects.keys()) == {"main", "secondary"}
        assert loaded.projects["main"].description == "Primary project"
        assert loaded.projects["main"].db_path == "~/.mementic/projects/main.mementic.db"
        assert "/abs/path/to/repo" in loaded.projects["main"].repos
        assert loaded.projects["main"].repos["/abs/path/to/repo"].label == "my-repo"
        assert loaded.projects["secondary"].description == ""
        assert loaded.projects["secondary"].repos == {}

    # ── resolve_project ─────────────────────────────────────────────

    def test_resolve_project_explicit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """resolve_project('myproj') returns ('myproj', config)."""
        config_file = tmp_path / ".mementic" / "config.json"
        config_file.parent.mkdir(parents=True)
        monkeypatch.setattr(
            "mementic.config.get_global_config_path", lambda: config_file
        )

        cfg = GlobalConfig(
            projects={
                "myproj": ProjectEntry(db_path="~/projects/myproj.db"),
            },
        )
        save_global_config(cfg)

        name, loaded = resolve_project("myproj")
        assert name == "myproj"
        assert "myproj" in loaded.projects

    def test_resolve_project_not_found_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """resolve_project('missing') raises ValueError."""
        config_file = tmp_path / ".mementic" / "config.json"
        config_file.parent.mkdir(parents=True)
        monkeypatch.setattr(
            "mementic.config.get_global_config_path", lambda: config_file
        )

        cfg = GlobalConfig(
            projects={
                "myproj": ProjectEntry(db_path="~/projects/myproj.db"),
            },
        )
        save_global_config(cfg)

        with pytest.raises(ValueError, match="Project 'missing' not found"):
            resolve_project("missing")

    def test_resolve_project_single_implicit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Config with exactly 1 project, resolve_project(None) returns it."""
        config_file = tmp_path / ".mementic" / "config.json"
        config_file.parent.mkdir(parents=True)
        monkeypatch.setattr(
            "mementic.config.get_global_config_path", lambda: config_file
        )

        cfg = GlobalConfig(
            projects={
                "onlyproj": ProjectEntry(db_path="~/projects/only.db"),
            },
        )
        save_global_config(cfg)

        name, loaded = resolve_project(None)
        assert name == "onlyproj"

    def test_resolve_project_multiple_no_flag_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Config with 2+ projects, resolve_project(None) raises ValueError."""
        config_file = tmp_path / ".mementic" / "config.json"
        config_file.parent.mkdir(parents=True)
        monkeypatch.setattr(
            "mementic.config.get_global_config_path", lambda: config_file
        )

        cfg = GlobalConfig(
            projects={
                "proj-a": ProjectEntry(db_path="~/projects/a.db"),
                "proj-b": ProjectEntry(db_path="~/projects/b.db"),
            },
        )
        save_global_config(cfg)

        with pytest.raises(ValueError, match="Multiple projects configured"):
            resolve_project(None)

    # ── model defaults ──────────────────────────────────────────────

    def test_project_entry_defaults(self) -> None:
        """ProjectEntry(db_path='/tmp/test.db') has empty repos, empty description."""
        entry = ProjectEntry(db_path="/tmp/test.db")
        assert entry.description == ""
        assert entry.repos == {}

    def test_repo_entry_defaults(self) -> None:
        """RepoEntry(path='/tmp/repo') has label=''."""
        entry = RepoEntry(path="/tmp/repo")
        assert entry.label == ""


# ── Spec 17 Phase 1: CWD-based project resolution ─────────────────


class TestResolveProjectCWDInference:
    """Tests for CWD-based project resolution (Spec 17)."""

    def test_cwd_inside_registered_repo_resolves_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When CWD is inside a registered repo, resolve to that project."""
        repo = tmp_path / "auth-service"
        repo.mkdir()
        config = GlobalConfig(
            projects={
                "platform": ProjectEntry(
                    db_path=str(tmp_path / "platform.db"),
                    repos={
                        str(repo): RepoEntry(path=str(repo), label="auth")
                    },
                ),
            }
        )
        config_path = tmp_path / "config.json"
        config_path.write_text(config.model_dump_json(), encoding="utf-8")
        monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(config_path))
        monkeypatch.chdir(repo)

        name, cfg = resolve_project()
        assert name == "platform"

    def test_cwd_subdirectory_of_registered_repo_resolves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CWD inside a subdirectory of a registered repo still resolves."""
        repo = tmp_path / "auth-service"
        subdir = repo / "src" / "handlers"
        subdir.mkdir(parents=True)
        config = GlobalConfig(
            projects={
                "platform": ProjectEntry(
                    db_path=str(tmp_path / "platform.db"),
                    repos={
                        str(repo): RepoEntry(path=str(repo))
                    },
                ),
            }
        )
        config_path = tmp_path / "config.json"
        config_path.write_text(config.model_dump_json(), encoding="utf-8")
        monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(config_path))
        monkeypatch.chdir(subdir)

        name, _ = resolve_project()
        assert name == "platform"

    def test_cwd_in_multiple_projects_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CWD repo registered in multiple projects raises ValueError."""
        repo = tmp_path / "shared-lib"
        repo.mkdir()
        config = GlobalConfig(
            projects={
                "main": ProjectEntry(
                    db_path=str(tmp_path / "main.db"),
                    repos={str(repo): RepoEntry(path=str(repo))},
                ),
                "platform": ProjectEntry(
                    db_path=str(tmp_path / "platform.db"),
                    repos={str(repo): RepoEntry(path=str(repo))},
                ),
            }
        )
        config_path = tmp_path / "config.json"
        config_path.write_text(config.model_dump_json(), encoding="utf-8")
        monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(config_path))
        monkeypatch.chdir(repo)

        with pytest.raises(ValueError, match="multiple projects"):
            resolve_project()

    def test_cwd_outside_repos_falls_back_to_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CWD not in any repo falls back to default_project."""
        repo = tmp_path / "auth-service"
        repo.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        config = GlobalConfig(
            default_project="platform",
            projects={
                "main": ProjectEntry(
                    db_path=str(tmp_path / "main.db"),
                    repos={},
                ),
                "platform": ProjectEntry(
                    db_path=str(tmp_path / "platform.db"),
                    repos={
                        str(repo): RepoEntry(path=str(repo))
                    },
                ),
            }
        )
        config_path = tmp_path / "config.json"
        config_path.write_text(config.model_dump_json(), encoding="utf-8")
        monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(config_path))
        monkeypatch.chdir(home)

        name, _ = resolve_project()
        assert name == "platform"

    def test_single_project_still_works_without_cwd_match(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Single project resolves even when CWD is not in any repo."""
        repo = tmp_path / "my-app"
        repo.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        config = GlobalConfig(
            projects={
                "main": ProjectEntry(
                    db_path=str(tmp_path / "main.db"),
                    repos={
                        str(repo): RepoEntry(path=str(repo))
                    },
                ),
            }
        )
        config_path = tmp_path / "config.json"
        config_path.write_text(config.model_dump_json(), encoding="utf-8")
        monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(config_path))
        monkeypatch.chdir(home)

        name, _ = resolve_project()
        assert name == "main"


# ── Spec 17 Phase 1: Project deletion ─────────────────────────────


class TestDeleteProject:
    """Tests for delete_project (Spec 17)."""

    def test_delete_project_removes_from_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deleting a project removes it from config."""
        config = GlobalConfig(
            projects={
                "main": ProjectEntry(db_path=str(tmp_path / "main.db")),
                "platform": ProjectEntry(db_path=str(tmp_path / "platform.db")),
            }
        )
        config_path = tmp_path / "config.json"
        config_path.write_text(config.model_dump_json(), encoding="utf-8")
        monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(config_path))

        delete_project("platform", delete_db=False)

        loaded = load_global_config()
        assert "platform" not in loaded.projects
        assert "main" in loaded.projects

    def test_delete_project_updates_default_if_needed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If deleted project was the default, default_project is updated."""
        config = GlobalConfig(
            default_project="platform",
            projects={
                "main": ProjectEntry(db_path=str(tmp_path / "main.db")),
                "platform": ProjectEntry(db_path=str(tmp_path / "platform.db")),
            }
        )
        config_path = tmp_path / "config.json"
        config_path.write_text(config.model_dump_json(), encoding="utf-8")
        monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(config_path))

        delete_project("platform", delete_db=False)

        loaded = load_global_config()
        assert loaded.default_project == "main"

    def test_delete_project_last_project_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cannot delete the last remaining project."""
        config = GlobalConfig(
            projects={
                "main": ProjectEntry(db_path=str(tmp_path / "main.db")),
            }
        )
        config_path = tmp_path / "config.json"
        config_path.write_text(config.model_dump_json(), encoding="utf-8")
        monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(config_path))

        with pytest.raises(ValueError, match="last remaining project"):
            delete_project("main")

    def test_delete_project_nonexistent_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deleting a nonexistent project raises ValueError."""
        config = GlobalConfig(
            projects={
                "main": ProjectEntry(db_path=str(tmp_path / "main.db")),
            }
        )
        config_path = tmp_path / "config.json"
        config_path.write_text(config.model_dump_json(), encoding="utf-8")
        monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(config_path))

        with pytest.raises(ValueError, match="not found"):
            delete_project("ghost")

    def test_delete_project_with_db_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """delete_db=True removes the SQLite file."""
        db_file = tmp_path / "platform.db"
        db_file.write_text("fake db")
        config = GlobalConfig(
            projects={
                "main": ProjectEntry(db_path=str(tmp_path / "main.db")),
                "platform": ProjectEntry(db_path=str(db_file)),
            }
        )
        config_path = tmp_path / "config.json"
        config_path.write_text(config.model_dump_json(), encoding="utf-8")
        monkeypatch.setenv("MEMENTIC_GLOBAL_CONFIG", str(config_path))

        db_deleted, msg = delete_project("platform", delete_db=True)
        assert db_deleted is True
        assert not db_file.exists()
