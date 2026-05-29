# Mementic

Semantic memory layer for engineering teams and AI coding agents.

Mementic ingests architectural decisions (ADRs), constraints, and project conventions from Confluence, repositories, and opencode instructions into a structured knowledge graph. Agents (opencode, Claude Code, Cursor) query it via MCP tools to inherit the team's architectural context in every session.

## Install

```bash
git clone https://github.com/pakdelm/mementic.git
cd mementic
uv sync
```

Set your DeepSeek API key by copying the example env file and editing it:

```bash
cp .env.example .env
# Edit .env with your actual key
```

## Quick start

### 1. Configure sources

Create `.mementic.json`:

```json
{
  "sources": {
    "adr_dirs": [{"path": "./docs/adr"}],
    "opencode_instructions": [{"path": "./docs/instructions"}]
  },
  "output": {
    "graph_path": "./.mementic/graph.json"
  }
}
```

### 2. Initialize the graph

```bash
uv run mementic init
```

Scans all configured sources, extracts decisions and constraints via LLM, and populates the knowledge graph.

### 3. Query the graph

```bash
uv run mementic status
uv run mementic query --search "auth"
uv run mementic query --entity src/auth/handler.py
```

### 4. Enforce constraints before tasks

```bash
uv run mementic enforce --task "Add rate limiting to auth" --files src/auth/handler.py
```

Outputs a context pack with all relevant constraints and decisions.

### 5. Capture decisions after tasks

```bash
uv run mementic capture --session /path/to/session.log --notes "Migrated Redis to Valkey"
```

Extracts architectural decisions from an agent session log + git diff, with human review before writing to the graph.

### 6. Review pending proposals

```bash
uv run mementic review
```

## Agent integration (opencode)

Register the MCP server in `opencode.json`:

```json
{
  "mcp": {
    "mementic": {
      "type": "local",
      "command": [".venv\\Scripts\\python.exe", "-m", "mementic"],
      "enabled": true
    }
  }
}
```

The agent automatically discovers these MCP tools:
- `get_context_pack` — load constraints before starting work
- `propose_decision` — record architectural decisions for review
- `propose_constraint` — record new constraints
- `get_system_evolution` — trace decision history of a code entity

## CLI reference

| Command | Description |
|---|---|
| `mementic init [--config-path PATH]` | Initialize graph from sources |
| `mementic capture --session PATH [--notes TEXT]` | Extract decisions from session |
| `mementic enforce --task TEXT --files FILE...` | Generate context pack for task |
| `mementic query [--entity PATH] [--search TERM]` | Search graph |
| `mementic server` | Start MCP server (stdio) |
| `mementic status` | Show graph statistics |
| `mementic review` | Review pending proposals |

## Development

```bash
uv sync           # install dependencies
uv run pytest     # run tests (91 tests)
uv run ruff check .  # lint
uv run mypy .     # type check
```

## License

MIT
