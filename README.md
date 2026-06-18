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

| Provider | Set env var | Model example |
|---|---|---|
| DeepSeek (default) | `DEEPSEEK_API_KEY` | `deepseek-chat` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o` |
| OpenRouter | `OPENROUTER_API_KEY` | `openai/gpt-4o` |
| Ollama (local) | none | `llama3` |

```bash
# DeepSeek (default) — pass inline to avoid persisting in your shell
DEEPSEEK_API_KEY="sk-..." mementic init

# Or export for the current session
export DEEPSEEK_API_KEY="sk-..."
```
```pwsh
# PowerShell
$env:DEEPSEEK_API_KEY="sk-..."; mementic init
```
```bat
:: cmd.exe
cmd /c "set DEEPSEEK_API_KEY=sk-... && mementic init"
```

If the API key is missing or invalid, mementic exits immediately with a clear error
naming the required environment variable. No silent fallback.

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
mementic context --task "Add rate limiting to auth" --files src/auth/handler.py
```

Outputs a context pack with all relevant constraints and decisions.

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
1. **Primary (always):** the context pack from `mementic_get_task_context`.
   It is the compressed index — a fraction of the token cost of reading raw
   ADRs. Read it first, every time.
2. **Secondary (only when needed):** raw ADR and instruction files on disk.
   Read them to resolve ambiguity, not to check for things the context pack
   didn't mention.
3. **Never:** read all ADRs and instruction files as a batch before every task.

**After EVERY user message** (not just "before a task" — each message is a new
context window):
1. Call `mementic_get_task_context` with the task description and ALL files
   you intend to touch — even new files, even speculative changes.
2. Call `mementic_get_entity_history` for each file being modified.
3. Validate your plan against every returned constraint. Do NOT write code
   that violates an active constraint.
4. Only read raw ADR or instruction files if the context pack leaves ambiguity
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
| `mementic init [--repo PATH] [--no-scan] [--interactive] [--project NAME]` | Initialize graph from configured sources |
| `mementic capture [--session PATH] [--notes TEXT] [--tail N] [-k KEYWORD]` | Extract decisions from session (auto-resolves: new/skip/update) |
| `mementic context --task TEXT --files FILE... [--prefix]` | Generate context pack with relevant constraints and decisions |
| `mementic query [--entity PATH] [--search TERM] [--depth N]` | Search graph |
| `mementic list [decisions\|constraints\|entities] [--status S] [-n N] [--stale]` | Browse graph in scannable format |
| `mementic deprecate <id> [--reason TEXT]` | Retire a decision or constraint (don't edit history) |
| `mementic graph [-o PATH] [-O] [--filter TYPE]` | Generate interactive HTML visualization |
| `mementic metrics [--json] [--reset]` | Show token usage metrics |
| `mementic status` | Show health checklist (config, API key, graph, session provider) |
| `mementic log` | Show recent graph mutations |
| `mementic server` | Start MCP server (stdio) |
| `mementic project create <name> [--description TEXT]` | Create a new project |
| `mementic project list` | List all configured projects |
| `mementic project set-default <name>` | Set the default project |
| `mementic project info [name]` | Show project details (repos, DB path, description) |
| `mementic project delete <name> [--force] [--keep-db]` | Delete a project and optionally its database |

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
uv run pytest -n auto             # run tests (parallel)
uv run ruff check .               # lint
uv run mypy .                     # type check
```
