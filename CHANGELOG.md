# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-07-12

### Removed

- **MCP server** — removed `mementic/server.py` (~360 lines), `fastmcp` dependency, and all MCP tool implementations. Agent integration now uses CLI commands exclusively, making Mementic agent-agnostic with zero harness-specific configuration required.
- **Metrics infrastructure** — removed `mementic/usage.py`, `scripts/benchmark_*.py`, and associated CLI commands (~1100+ lines). Extraction cost tracking and benchmarking deferred to a future metrics module.
- **`mementic lint` and `mementic review` CLI commands** — removed underused commands to streamline the CLI surface.

### Added

- `--json` flag on `mementic query keywords` and `mementic query file` for machine-readable output.
- Multi-ID support on `mementic deprecate` (single command replaces `review_session`).
- `GraphStore.get_audit_entries(limit)` public method replacing direct `_conn` access.
- `sanitize()` utility in `mementic/console.py` for ASCII-safe CLI output on Windows cp1252 terminals.
- `sys.stdout`/`stderr` UTF-8 reconfiguration on import to prevent Unicode rendering crashes.

### Changed

- **AGENTS.md** — scoped mementic query trigger from "EVERY user message" to code/architecture tasks only, with explicit skip for conversational messages.
- **Enforcement template** — replaced all MCP tool references with CLI commands (`mementic query keywords`, `mementic add decision`, etc.).
- **`deprecate` command** — extended to handle both decisions and constraints (previously decisions-only).
- Moved dev dependencies to `[dependency-groups.dev]` in `pyproject.toml`.

### Fixed

- Duplicate post-bootstrap code block in `mementic init` (dead code removed).
- Missing `python-dotenv` dependency declaration causing CI failures.
- Unicode render crash on Windows cp1252 terminals.

## [0.1.2] - 2026-06-18

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
