# Mementic

Semantic memory layer for engineering teams and AI coding agents.

Mementic ingests architectural decisions (ADRs), constraints, and project conventions from Confluence, repositories, and opencode instructions into a structured knowledge graph. Agents (opencode, Claude Code, Cursor) query it via MCP tools to inherit the team's architectural context in every session.

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

Mementic requires an LLM. Supported providers:

| Provider | Set env var | Model example |
|---|---|---|
| DeepSeek (default) | `DEEPSEEK_API_KEY` | `deepseek-chat` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o` |
| OpenRouter | `OPENROUTER_API_KEY` | `openai/gpt-4o` |
| Ollama (local) | none | `llama3` |

```bash
# DeepSeek (default) — pass inline to avoid persisting in your shell
DEEPSEEK_API_KEY="sk-..." opencode

# Or export for the current session
export DEEPSEEK_API_KEY="sk-..."
```
```pwsh
# PowerShell
$env:DEEPSEEK_API_KEY="sk-..."; opencode
```
```bat
:: cmd.exe
cmd /c "set DEEPSEEK_API_KEY=sk-... && opencode"
```

If the API key is missing or invalid, mementic exits immediately with a clear error
naming the required environment variable. No silent fallback.

## Quick start

### 1. Initialize the graph

```bash
mementic init
```

If no `.mementic.json` exists, `init` auto-discovers `./docs/adr/` and
`./docs/instructions/` directories and creates a default config. Use
`--interactive` for a setup walkthrough, or `--force` to require an existing
config.

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
# Auto-discovers latest opencode session
mementic capture

# With manual session path and notes
mementic capture --session /path/to/session.log --notes "Migrated Redis to Valkey"
```

Extracts architectural decisions from the agent session log + git diff.
Decisions are auto-resolved against the existing graph: new slugs are
accepted, duplicates are skipped, and conflicting changes deprecated existing
entries. No manual review required.
Tracks the last captured commit so subsequent captures only diff new changes.
Runs without git (warns and uses notes only).

### 5. View audit log

```bash
mementic log
```

Shows recent graph mutations with timestamps.

## Agent integration (opencode)

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
- `get_context_pack` — load constraints before starting work
- `propose_decision` — record architectural decisions
- `propose_constraint` — record new constraints
- `deprecate_entity` — retire outdated decisions or constraints
- `get_system_evolution` — trace decision history of a code entity

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
1. **Primary (always):** the context pack from `mementic_get_context_pack`.
   It is the compressed index — a fraction of the token cost of reading raw
   ADRs. Read it first, every time.
2. **Secondary (only when needed):** raw ADR and instruction files on disk.
   Read them to resolve ambiguity, not to check for things the context pack
   didn't mention.
3. **Never:** read all ADRs and instruction files as a batch before every task.

**Before ANY task:**
1. Call `mementic_get_context_pack` with the task description and ALL files
   you intend to touch — even new files, even speculative changes.
2. Call `mementic_get_system_evolution` for each file being modified.
3. Validate your plan against every returned constraint. Do NOT write code
   that violates an active constraint.
4. Only read raw ADR or instruction files if the context pack leaves ambiguity
   you must resolve to proceed safely.

**After architecturally significant changes:**
1. Call `mementic_propose_decision` with title, rationale, rejected
   alternatives, and affected entities.
2. Call `mementic_propose_constraint` for any new rules.

**After the task:**
Run `mementic capture` in the terminal. If capture reports flagged
conflicts, run `mementic review` to resolve them.
```

## CLI reference

| Command | Description |
|---|---|---|
| `mementic init [--config PATH] [--interactive] [--force]` | Auto-bootstrap config and initialize graph |
| `mementic capture [--session PATH] [--notes TEXT]` | Extract decisions from session (auto-resolves: new/skip/update) |
| `mementic context --task TEXT --files FILE...` | Generate context pack with relevant constraints and decisions |
| `mementic query [--entity PATH] [--search TERM] [--depth N]` | Search graph |
| `mementic list [decisions\|constraints\|entities] [--status] [-n N]` | Browse graph in scannable format |
| `mementic deprecate <id> [--reason TEXT]` | Retire a decision (don't edit history) |
| `mementic graph [-o PATH] [-O] [--filter TYPE]` | Generate interactive HTML visualization |
| `mementic metrics [--json] [--reset]` | Show token usage across four sections |
| `mementic status` | Show health checklist (config, API key, graph, session provider) |
| `mementic log` | Show recent graph mutations |
| `mementic server` | Start MCP server (stdio) |

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

Data is stored as JSONL in `.mementic/metrics.jsonl` (auto-created,
automatically gitignored with the rest of `.mementic/`). No LLM cost for reading or summarizing
metrics — it's just filesystem I/O.

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
uv run pytest                     # run tests
uv run ruff check .               # lint
uv run mypy .                     # type check
```

## License

MIT
