# Mementic

Semantic memory layer for engineering teams and AI coding agents.

Mementic ingests architectural decisions (ADRs), constraints, and project
conventions from repositories and agent instruction files into a structured
knowledge graph. Agents (OpenCode, Claude Code, Cursor, Windsurf) query it
via CLI commands to inherit the team's architectural context in every session.

## Install

### One-command install (recommended)

```bash
uv tool install git+https://github.com/pakdelm/mementic.git
```

This gives you a system-wide `mementic` binary. Update with:

```bash
uv tool upgrade mementic
```

### From source

```bash
git clone https://github.com/pakdelm/mementic.git
cd mementic
uv sync
```

### Provider selection

Mementic needs an LLM provider for extraction. Provider resolution follows
this order:

1. **`--provider` flag** (explicit): `mementic init --provider openrouter`
2. **Environment variable auto-detect:** If any of `OPENROUTER_API_KEY`,
   `OPENAI_API_KEY`, `DEEPSEEK_API_KEY` is set, that provider is used.
3. **Interactive prompt:** When no flag and no env var, `mementic init` asks
   which provider to use.

Supported providers:

| Provider | API key env var | Model example |
|---|---|---|
| OpenRouter (default) | `OPENROUTER_API_KEY` | `deepseek/deepseek-v4-pro` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o` |
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-v4-pro` |
| Ollama (local) | none required | `llama3` |

**Interactive setup (recommended):** `mementic init` prompts for provider,
model, and API key. The key is stored in your OS keychain (Windows Credential
Locker, macOS Keychain, Linux Secret Service) — never in a file.

```bash
mementic init           # interactive: choose provider, enter key
mementic init --key     # rotate an existing key
```

**CI / headless environments:** set the environment variable for your provider.

```bash
export OPENROUTER_API_KEY="sk-..."
mementic init --provider openrouter
```

If no API key is available from keyring or env, mementic exits with a clear
error naming the required environment variable and suggesting `mementic init --key`.

## Quick start

### 1. Initialize the graph

```bash
cd ~/dev/my-app
mementic init --provider openrouter
```

Creates a local `.mementic/` directory with the graph database and config.
Auto-discovers `./docs/adr/`, `./docs/instructions/`, `.cursor/instructions/`,
and `.windsurf/instructions/` directories. No global config, no project
registration — everything lives in `.mementic/`.

Scans all configured sources, extracts decisions and constraints via LLM, and
populates the knowledge graph.

### 2. Query the graph

```bash
mementic status
mementic query search auth
mementic query file src/auth/handler.py
```

### 3. Load context before tasks

```bash
mementic query keywords auth rate limit security
```

Outputs a mementic memory with constraints and decisions ranked by keyword overlap.
Agents generate 5-12 narrow, single-word technical keywords per task for best results.

### 4. Capture decisions after tasks

```bash
# Auto-discovers latest session from any supported agent
mementic capture

# With manual session path and agent hint
mementic capture --session /path/to/session.log --agent opencode
```

Extracts architectural decisions from the agent session log + git diff.
Decisions are auto-resolved against the existing graph: new slugs are
accepted, duplicates are skipped, and conflicting changes deprecate existing
entries. No manual review required.
Tracks the last captured commit so subsequent captures only diff new changes.
Runs without git (warns and uses session-only extraction).

### 5. View audit log

```bash
mementic log
```

Shows recent graph mutations with timestamps.

## Agent integration

`mementic init` automatically detects your agent (OpenCode, Claude Code,
Cursor, or Windsurf) and injects enforcement instructions into `AGENTS.md`.
No per-harness configuration files needed — it works the same across all
agents.

Agents invoke mementic as CLI commands:

| Agent action | CLI command |
|---|---|
| Load context before every task | `mementic query keywords kw1 kw2 ...` |
| Trace file history | `mementic query file path/to/file.py` |
| Record architectural decisions | `mementic add decision TITLE --rationale "..."` |
| Record new constraints | `mementic add constraint DESC -e PATH` |
| Retire outdated rules | `mementic deprecate ID1 ID2 ... -r "reason"` |
| Capture session decisions | `mementic capture` (user-triggered only) |

All commands support `--json` for programmatic output.


## Agent enforcement

For mementic to work, your agent must be instructed to use it. Add this to your
project's `AGENTS.md` or equivalent instruction file (Claude Code, Cursor, etc.):

```markdown
## Mementic Memory (HARD CONSTRAINT)

This project uses Mementic as its architectural memory layer. These rules apply
to every task, every session.

**Token discipline — primary vs. secondary sources:**
1. **Primary (always):** the mementic memory from `mementic query keywords`.
   It is the compressed index — a fraction of the token cost of reading raw
   ADRs. Read it first, every time.
2. **Secondary (only when needed):** raw ADR and instruction files on disk.
   Read them to resolve ambiguity, not to check for things the mementic memory
   didn't mention.
3. **Never:** read all ADRs and instruction files as a batch before every task.

**After EVERY user message** (not just "before a task" — each message is a new
context window):
1. Think about the task: what domains, concepts, and concerns are relevant?
   Generate 5-12 narrow, single-word technical keywords
   (e.g., for "fix authentication bug" → ["argon2", "token", "login",
   "session"] — skip "security", "password", "hashing" because they match too
   many items).
2. Run `mementic query keywords kw1 kw2 ...` with those keywords.
3. Run `mementic query file <path>` for each file being modified.
4. Validate your plan against every returned constraint. Do NOT write code
   that violates an active constraint.
5. Only read raw ADR or instruction files if the mementic memory leaves ambiguity
   you must resolve to proceed safely.

**Why every message, not just "before a task":** Context packs are free (no LLM
calls, just SQLite reads). Architectural constraints can change between messages.
Assuming previous context is still valid is how violations happen.

**After architecturally significant changes:**
1. Run `mementic add decision TITLE --rationale "..."` with title, rationale,
   rejected alternatives, and affected entities.
2. Run `mementic add constraint DESC -e PATH` for any new rules.
3. Run `mementic deprecate ID1 ID2 ...` to retire any rules the new ones supersede.

**After the task:**
Run `mementic capture` in the terminal. Decisions are auto-resolved —
no manual review step.
```

## CLI reference

| Command | Description |
|---|---|
| `mementic init [--provider X] [--model X] [--key] [--deep] [--no-scan]` | Initialize graph from configured sources |
| `mementic capture [--agent X] [--session PATH] [--keyword X]` | Extract decisions from session (auto-resolves: new/skip/update) |
| `mementic query keywords <kw...> [--json]` | Return ranked context pack of constraints and decisions matched by keywords |
| `mementic query file <path> [--json]` | Show constraints and decisions affecting a specific file |
| `mementic add decision <title> --rationale TEXT [-e PATH]` | Record a new architectural decision |
| `mementic add constraint <desc> -e PATH` | Record a new constraint |
| `mementic deprecate <id...> [-r REASON]` | Retire decisions or constraints (accepts multiple IDs) |
| `mementic status` | Show health checklist (config, API key, graph, session provider) |
| `mementic list [decisions\|constraints\|entities]` | List decisions, constraints, or entities |
| `mementic log [--limit N]` | Show recent graph mutations |

## Config file reference

`mementic init` creates a local `.mementic/` directory with the knowledge
graph database and a minimal `config.json`:

```json
{
  "llm": {
    "provider": "openrouter",
    "model": "deepseek/deepseek-v4-pro"
  }
}
```

All configuration lives in `.mementic/config.json` alongside `graph.db`.
No global config files, no `~/.mementic/`. API keys are never stored here —
they live in your OS keyring (or environment variable for CI / Docker).

### Sessions and log directories

Mementic auto-discovers agent session logs from standard locations. By
default, each supported agent uses its own built-in platform paths.

Adding a `sessions` key to the config is optional — only needed when your
session logs live in non-standard locations. The `log_dirs` entries are
**additive**: they extend the defaults, not replace them.

```json
{
  "llm": {
    "provider": "openrouter",
    "model": "deepseek/deepseek-v4-pro"
  },
  "sessions": {
    "opencode": {
      "log_dirs": ["/mnt/nfs-shared/sessions"]
    }
  }
}
```

With this config, mementic searches the standard OpenCode log directory
**plus** `/mnt/nfs-shared/sessions`. All other agents (Claude, Cursor,
Windsurf) continue using their built-in defaults unchanged.

## Why Mementic

We ran two real implementation tasks in OpenCode sessions — without mementic
vs. with keyword-ranked context packs via CLI. Both conditions passed the same
tests + lint + typecheck. The graph held 330 decisions, 215 constraints, and
113 code entities at measurement time.

- **51% fewer LLM tokens** (430K → 209K). Context arrives in a ranked pack
  instead of the agent hunting through files for architectural rules.
- **75% fewer subagents** (8 → 2). Without mementic, each subagent re-reads
  ADRs and instruction files independently. Mementic delivers context once,
  eliminating redundant discovery.
- **47% fewer file reads** (139 → 73). Known constraints mean navigating
  straight to implementation files — no scanning the repo for conventions.
- **Small tasks win too.** A single-module edit eliminated its subagent
  entirely (1 → 0). The context pack answered "what rules apply here?"
  in the prompt — no subagent was needed.

**Localized feature** (single module):

| | Without | With |
|---|---|---|
| Unique files read | 9 | 4 |
| Total file reads | 19 | 14 |
| LLM tokens | 106K | 59K |
| Subagents | 1 | 0 |

**Cross-cutting feature** (multiple modules, new subcommand):

| | Without | With |
|---|---|---|
| Unique files read | 20 | 14 |
| Total file reads | 120 | 59 |
| LLM tokens | 324K | 149K |
| Subagents | 7 | 2 |

Mementic eliminates the *context discovery loop* — the most token-expensive
phase of agent coding, where agents repeatedly read files to answer "what
architectural rules apply to this change?"

Numbers are from mementic's own codebase.

## Development

```bash
uv sync                           # install dependencies
uv run pytest                     # run tests (parallel)
uv run ruff check .               # lint
uv run mypy .                     # type check
```
