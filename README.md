# Mementic

Semantic memory layer for engineering teams and AI coding agents.

Mementic ingests architectural decisions (ADRs), constraints, and project
conventions from repositories and agent instruction files into a structured
knowledge graph. Agents (OpenCode, Claude Code, Cursor, Windsurf) query it
via MCP tools to inherit the team's architectural context in every session.

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

### Configure your LLM provider

Mementic supports LLM-based extraction for richer results. Without an API
key, it runs in deterministic mode (reduced quality). Supported providers:

| Provider | Key stored in | Model example |
|---|---|---|
| DeepSeek (default) | OS keyring / `DEEPSEEK_API_KEY` | `deepseek-chat` |
| OpenAI | OS keyring / `OPENAI_API_KEY` | `gpt-4o` |
| OpenRouter | OS keyring / `OPENROUTER_API_KEY` | `openai/gpt-4o` |
| Ollama (local) | none required | `llama3` |

**Interactive setup (recommended):** `mementic init` prompts for provider,
model, and API key. The key is stored in your OS keychain (Windows Credential
Locker, macOS Keychain, Linux Secret Service) — never in a file.

```bash
mementic init           # first-time setup, prompts for key
mementic init --key     # rotate an existing key
```

**CI / headless environments:** set the environment variable for your provider.

```bash
export DEEPSEEK_API_KEY="sk-..."
mementic init
```

If no API key is available from keyring or env, mementic exits with a clear
error naming the required environment variable and suggesting `mementic init --key`.

## Quick start

### 1. Initialize the graph

```bash
cd ~/dev/my-app
mementic init
```

Auto-discovers `./docs/adr/`, `./docs/instructions/`, `.cursor/instructions/`,
and `.windsurf/instructions/` directories. Registers the repo in
`~/.mementic/config.json` under the default project. Use `--interactive` for
a guided setup walkthrough.

Scans all configured sources, extracts decisions and constraints via LLM, and
populates the knowledge graph.

### 2. Query the graph

```bash
mementic status
mementic query --search "auth"
mementic query --entity src/auth/handler.py
```

### 3. Load context before tasks

```bash
mementic context --keyword auth --keyword rate --keyword limit --keyword security
```

Outputs a mementic memory with constraints and decisions ranked by keyword overlap.
Agents generate 15-25 single-word keywords per task for best results.

### 4. Capture decisions after tasks

```bash
# Auto-discovers latest session from any supported agent
mementic capture

# With manual session path and notes
mementic capture --session /path/to/session.log --notes "Migrated Redis to Valkey"
```

Extracts architectural decisions from the agent session log + git diff.
Decisions are auto-resolved against the existing graph: new slugs are
accepted, duplicates are skipped, and conflicting changes deprecate existing
entries. No manual review required.
Tracks the last captured commit so subsequent captures only diff new changes.
Runs without git (warns and uses notes only).

### 5. View audit log

```bash
mementic log
```

Shows recent graph mutations with timestamps.

## Multi-project workflow

Mementic supports multiple isolated projects, each with its own knowledge
graph database. Commands automatically detect which project to use based on
your current directory.

```bash
# Create a project
mementic project create platform --description "Core platform services"

# Register repos in it
cd ~/dev/auth-service && mementic init --project platform
cd ~/dev/billing && mementic init --project platform

# Now any command run inside those repos targets "platform" automatically
cd ~/dev/auth-service
mementic capture --notes "added rate limiting"
# → resolves to "platform" — no --project needed

# List and inspect projects
mementic project list
mementic project info platform
```

The `--project` flag is only required when a repo belongs to multiple
projects (rare). See `mementic project --help` for all subcommands.

## Agent integration (OpenCode)

Register the MCP server in `opencode.json`:

```json
{
  "mcp": {
    "mementic": {
      "type": "local",
      "command": ["mementic", "server"],
      "enabled": true
    }
  }
}
```

The agent automatically discovers these MCP tools:
- `get_task_context` — load constraints and decisions for a task (call after every user message)
- `record_decision` — record architectural decisions
- `record_constraint` — record new constraints
- `deprecate_rule` — retire outdated decisions or constraints
- `get_entity_history` — trace decision history of a code entity

OpenCode custom commands are also available in the TUI:
- `/mementic-context <task>` — load constraints before working
- `/mementic-capture` — record session decisions


## Agent enforcement

For mementic to work, your agent must be instructed to use it. Add this to your
project's `AGENTS.md` or equivalent instruction file (Claude Code, Cursor, etc.):

```markdown
## Mementic Memory (HARD CONSTRAINT)

This project uses Mementic as its architectural memory layer. These rules apply
to every task, every session.

**Token discipline — primary vs. secondary sources:**
1. **Primary (always):** the mementic memory from `mementic_get_task_context`.
   It is the compressed index — a fraction of the token cost of reading raw
   ADRs. Read it first, every time.
2. **Secondary (only when needed):** raw ADR and instruction files on disk.
   Read them to resolve ambiguity, not to check for things the mementic memory
   didn't mention.
3. **Never:** read all ADRs and instruction files as a batch before every task.

**After EVERY user message** (not just "before a task" — each message is a new
context window):
1. Think about the task: what domains, concepts, and concerns are relevant?
   Generate 15-25 single-word keywords that capture the task's domain
   (e.g., for "fix authentication bug" → ["auth", "token", "security",
   "password", "hashing", "argon2", "login", "session"]).
2. Call `mementic_get_task_context(keywords=[...])` with those keywords.
3. Call `mementic_get_entity_history` for each file being modified.
4. Validate your plan against every returned constraint. Do NOT write code
   that violates an active constraint.
5. Only read raw ADR or instruction files if the mementic memory leaves ambiguity
   you must resolve to proceed safely.

**Why every message, not just "before a task":** Context packs are free (no LLM
calls, just SQLite reads). Architectural constraints can change between messages.
Assuming previous context is still valid is how violations happen.

**After architecturally significant changes:**
1. Call `mementic_record_decision` with title, rationale, rejected
   alternatives, and affected entities.
2. Call `mementic_record_constraint` for any new rules.

**After the task:**
Run `mementic capture` in the terminal. Decisions are auto-resolved —
no manual review step.
```

## CLI reference

| Command | Description |
|---|---|
| `mementic init [--repo PATH] [--no-scan] [--interactive] [--key] [--project NAME]` | Initialize graph from configured sources |
| `mementic capture [--session PATH] [--notes TEXT] [--tail N] [-k KEYWORD]` | Extract decisions from session (auto-resolves: new/skip/update) |
| `mementic context --keyword KEYWORD [...] [--project NAME]` | Generate mementic memory with constraints/decisions ranked by keyword overlap |
| `mementic query [--entity PATH] [--search TERM] [--depth N]` | Search graph |
| `mementic list [decisions\|constraints\|entities] [--status S] [-n N] [--stale]` | Browse graph in scannable format |
| `mementic lint [--fix] [--json] [--project NAME]` | Check graph health with deterministic lint rules |
| `mementic deprecate <id> [--reason TEXT]` | Retire a decision or constraint (don't edit history) |
| `mementic graph [-o PATH] [-O] [--filter TYPE]` | Generate interactive HTML visualization |
| `mementic metrics [--json] [--reset]` | Show token usage metrics |
| `mementic status` | Show health checklist (config, API key, graph, session provider) |
| `mementic log` | Show recent graph mutations |
| `mementic server` | Start MCP server (stdio) |
| `mementic session-cleanup <feature_description> [-d ID] [--reason TEXT] [--project NAME]` | Deprecate entities and clean references after a session |
| `mementic inject [--agent NAME] [--dry-run]` | Inject enforcement instructions into AGENTS.md |
| `mementic project create <name> [--description TEXT]` | Create a new project |
| `mementic project list` | List all configured projects |
| `mementic project set-default <name>` | Set the default project |
| `mementic project info [name]` | Show project details (repos, DB path, description) |
| `mementic project delete <name> [--force] [--keep-db]` | Delete a project and optionally its database |

## Agent session efficiency

Does mementic actually reduce what an agent does? We ran the same implementation
tasks in real OpenCode sessions — once without mementic (agent reads raw ADRs
and instruction files), once with (agent loads keyword-ranked context packs
via MCP).

The benchmark compares session cost side-by-side:
- **Files read** — how many files the agent opened seeking architectural context
- **Session log tokens** — total session size, a proxy for agent effort
- **Tool calls** — total agent activity (reads, writes, edits, searches)

### How to run

```bash
python scripts/benchmark_ab.py              # 2 runs per task per condition
python scripts/benchmark_ab.py --runs 3     # more statistical robustness
python scripts/benchmark_ab.py --tasks add-stale-flag   # single task only
```

The runner manages git state and config; you run OpenCode when prompted.
After each session, it runs the success criteria (tests + ruff + mypy)
and parses the session log for metrics. Outputs a side-by-side comparison.

To parse a session log manually:
```bash
python scripts/benchmark_ab_measure.py ~/.local/share/opencode/log/session.log --json
```

Numbers are from mementic's own codebase. Your project's results will differ.

## Measuring token efficiency

Mementic tracks its own costs so you can answer: *does the LLM spend on mementic pay for itself through agent context savings?*

### Check your token spend

```bash
mementic metrics
```

Shows lifetime usage across all operations:

```
Token Usage Since Inception
───────────────────────────────────────────────────
  LLM calls made:      42
  Input tokens sent:   128,450
  Output tokens:        18,320
  Total tokens:        146,770

Breakdown by Activity
───────────────────────────────────────────────────
  mementic init         18 calls    101,300 tokens    (one-time setup cost)
  mementic capture      24 calls     45,470 tokens    (per-session extraction)
```

Use `--json` for machine-readable output and `--reset` to clear the log and
start fresh.

### How it works

| Concept | What it means |
|---|---|
| **Extraction cost** | LLM tokens spent running `init` and `capture` |
| **Context pack** | The subset of decisions and constraints mementic returns for a specific task |

Metrics are stored in the project's SQLite database (the `extraction_log`
table). Legacy fallback to `.mementic/metrics.jsonl` when no project is
configured.

### Quick token count

The `count_tokens()` utility is available programmatically:

```python
from mementic.format import count_tokens
print(count_tokens("how many tokens is this?"))
```

Uses `tiktoken` (accurate) if installed, falls back to `len(text) // 4`
(~95% accurate for English).

## Development

```bash
uv sync                           # install dependencies
uv run pytest                    # run tests (parallel)
uv run ruff check .               # lint
uv run mypy .                     # type check
```
