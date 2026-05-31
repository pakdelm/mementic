# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-05-31

### Added

- Decoupled session capture with provider protocol (Spec 06b):
  - `SessionProvider` protocol and `SessionProviderRegistry` for agent-agnostic session discovery
  - `OpenCodeSessionProvider` — finds sessions in `~/.local/share/opencode/log/`
  - `ClaudeCodeSessionProvider` — recursively scans `~/.claude/projects/` for `*.jsonl`
  - `GenericSessionProvider` — reads any file, config-driven via `sessions.generic.log_dirs`
  - `mementic capture --agent opencode|claude-code|generic` flag
  - `sessions.<agent>.log_dirs` in `.mementic.json` for per-agent path overrides
  - 53 new tests (310 total)

### Changed

- `mementic/session.py` refactored into `mementic/session/` package
- `mementic capture` auto-detection now searches across all registered agents
- `.mementic.json` now includes `sessions` section with default provider paths

## [0.1.0] - 2026-05-31

Initial release:
- Knowledge graph with decisions, constraints, and code entities
- MCP server for agent integration (get_context_pack, propose_decision, etc.)
- CLI: init (auto-bootstrap), capture (with conflict detection), context (with --benchmark),
  query (with --depth traversal), lint (5 deterministic rules), review, graph (pyvis HTML),
  metrics, status, list, deprecate, log
- GitHub Actions CI (ruff, mypy, pytest on Python 3.11–3.13)
- Pre-commit hooks (ruff, ruff-format, trailing-whitespace, etc.)
- Multi-provider LLM support (DeepSeek, OpenAI, OpenRouter, Ollama)
- Token efficiency measurement with metrics infrastructure (Spec 08)
- Self-contained HTML graph visualization (no CDN, works offline)
- Auto-accept capture with per-decision conflict detection
