# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.1] - 2026-10-20

- Merge pull request #62 from pakdelm/feat/performance

## [0.4.0] - 2026-10-19

- Merge pull request #60 from pakdelm/feat/assess-openwiki
- Merge pull request #59 from pakdelm/feat/research

## [0.3.3] - 2026-08-26

- Merge pull request #57 from pakdelm/feat/spec-31

## [0.3.2] - 2026-08-25

- Merge pull request #55 from pakdelm/feat/spec-30

## [0.3.1] - 2026-08-23

- Merge pull request #53 from pakdelm/feat/spec-29

## [Unreleased]

### Added
- **Compact query output (Spec 33)** — `mementic query keywords` and
  `query file` gain `--compact` (`-c`; echo line plus undecorated pack text, no
  Rich panel); the `[mementic: ...]` summary line is compacted across
  `query keywords`, `query file`, and `query search` (keywords keeps
  shown-of-matched counts as `N of M`; file/search use plain counts;
  `|`-separated terms or path).

### Changed
- **CLI and context-pack refactor.** `cli.py` is now a `mementic/cli/`
  package with one module per command group; decision and constraint writes
  (including deterministic auto-resolve) live in `mementic/entries.py` as
  `EntryWriter`; context-pack ranking and rendering moved to
  `mementic/context/`; schema constants moved to `mementic/schema.py`.
  `count_tokens` is now `estimate_tokens` to distinguish budget estimation
  from the removed usage metrics. Auto-resolve and the successor write now run
  in one SQLite transaction, so a failed re-add cannot orphan a tombstone.
- **Output noise reduction.** `history` without an ID is capped at 20 records
  (`-n`), `status` renders hints on passing checks (an empty graph is healthy),
  `add` no longer appends `(accepted)`/`(active)`, and `deprecate` prints one
  line per result (`Deprecated x: reason`) with a summary only for multi-ID runs.
- **Consistent CLI language.** `query search` prints a sentence when nothing
  matches, `list entities` marks path liveness, `log` reports shown and total
  records (and `No changes recorded yet.` when empty), and `init` prints
  project-relative paths. Timestamps in `log` and `history` render in UTC and
  are labelled as such.
- **Path liveness fix.** `query file` and `list entities --stale` resolve
  repo-relative paths against the repo root instead of the process CWD.
- **Dead code removed.** Legacy `audit.jsonl` fallback and path helpers,
  `format_memory_context`, `resolve_repo_name`, `timestamp`, `inject.dry_run`,
  and the `scripts/probe_packs.py` context-pack probe harness.
- **Relational storage simplification (Spec 29, schema v5).** Replaced the
  GraphStore hybrid (in-memory NetworkX graph serialized into SQLite payloads,
  full-graph rewrite per change) with a pure relational schema accessed
  through a thin `sqlite3` module (`mementic/store.py`). Decisions and
  constraints are flat tables; file associations live in `entry_paths` rows;
  entry lifecycle moved from cached status columns to a `deprecations`
  tombstone table — liveness is derived at query time, never stored.
  NetworkX dependency removed.
- **New `mementic history [id]` command** — shows deprecation records;
  with an ID it prints the entry's replacement chain (reasons, successors).
- **`mementic deprecate` writes structured tombstones** (reason + optional
  successor in the `deprecations` table) instead of appending
  `[DEPRECATED: ...]` prose to rationale text.
- **`list decisions|constraints`** gained a "Superseded by" column for dead
  entries (successor ID or "none").
- **`query file <path>`** runs on `entry_paths` with exact match by default
  and opt-in `--prefix`; paths whose files no longer exist are filtered
  lazily via a filesystem check at read time.
- **Migration:** one-shot `scripts/migrate_v4_to_v5.py` converts v4 databases
  in place (automatic `.bak` backup, drops legacy proposed-status zombies,
  materializes entry paths, backfills tombstones). Run
  `uv run python scripts/migrate_v4_to_v5.py` before using mementic against
  an existing database.
- **Task-scoped enforcement (Spec 33)** — the AGENTS.md template now triggers
  queries on actions (the first read, search, or code decision of a task, task
  shifts, after graph writes, after compaction) instead of after every
  message; acknowledgement, ops, and meta messages need no query, and the
  per-file gate remains absolute.

### Removed
- `mementic/graph.py`, `mementic/lint.py`, code-entity/edge machinery
  (`CodeEntity`, `EntityType`, `GraphEdge`, `GraphPayload`), the `batch()`
  context manager, and `clean_dead_references()`.
- `networkx` and `types-networkx` dependencies.
- `add decision --rejected` flag.

## [0.3.0] - 2026-08-23

- Merge pull request #51 from pakdelm/feat/specs-23-27## [0.2.5] - 2026-08-06

- Merge pull request #49 from pakdelm/feat/improve-spec-20## [0.2.4] - 2026-07-17

- Merge pull request #47 from pakdelm/fix/more-release-fixes
- Merge pull request #46 from pakdelm/fix/release-bugs
- Merge pull request #45 from pakdelm/fix/release-bugs

## [0.2.3] - 2026-07-17

- Add release script

## [0.2.2] - 2026-07-12

### Fixed
- `mementic init --provider`/`--model` now preserves existing `sessions` overrides instead of silently dropping them.
- Invalid `--provider` value exits cleanly with an error message instead of a raw traceback.
- Removed dead empty ADR/instruction ingestion loops in `run_init` (the codebase-first auto-scan handles ingestion).
- Fixed stale `~/.mementic` docstrings in `session/generic.py`.
- Corrected README `log` flag (`--limit` -> `-n`) and documented `query search` / `query stats`.

### Tests
- Added regression tests for sessions preservation and invalid-provider exit.

## [0.2.1] - 2026-07-12

### Changed
- **Local per-repo config (Spec 22)** — replaced the global `~/.mementic/` config model with a local `.mementic/` directory. LLM provider/model and session-log overrides now live in `.mementic/config.json`; API keys are stored in the OS keyring. The project registry and `--project` flag were removed.
- **Interactive provider selection** — `mementic init` now prompts for provider and model when no API key or environment variable is set.

### Fixed
- Replaced stale `deepseek-chat` references with `deepseek-v4-pro` across the codebase and docs.
- Stripped ANSI codes in the `init --help` test.

### Docs
- Improved the config reference; removed the Docker SBX section.
- Fixed stale `deepseek-chat` references in README and spec.

### Tests
- Rewrote skipped tests and deleted dead test classes.

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
