# Agent Integration

Mementic works identically across AI coding agents. The MCP tools, CLI commands,
and enforcement pattern are the same everywhere — only the config file path
changes per agent.

## MCP Registration

All agents that support MCP use a `mcpServers` block in a JSON config file.

| Agent | Config file |
|-------|-------------|
| OpenCode | `.opencode.json` (or `opencode.jsonc`) |
| Claude Code | `.claude/mcp.json` |
| Cursor | `.cursor/mcp.json` |
| Windsurf | `.windsurf/mcp.json` |

The server entry is identical regardless of agent:

```json
{
  "mcpServers": {
    "mementic": {
      "command": "uv",
      "args": ["run", "mementic", "server"]
    }
  }
}
```

## Enforcement

Add the enforcement block to your agent's instructions file. Most agents read
markdown from a project-local directory or a root-level rules file.

| Agent | Instructions directory | Rules file (alternative) |
|-------|----------------------|--------------------------|
| OpenCode | `.opencode/instructions/` | `AGENTS.md` |
| Claude Code | `.claude/instructions/` | `CLAUDE.md` |
| Cursor | `.cursor/instructions/` | `.cursorrules` |
| Windsurf | `.windsurf/instructions/` | `.windsurfrules` |

Place this block in whichever file your agent reads first:

```markdown
## Mementic Memory (HARD CONSTRAINT — ALWAYS ACTIVE)

Before ANY task:
1. Run `mementic lint --fix` in the terminal.
2. Call `mementic_get_context_pack` with your task description and ALL files
   you intend to touch.
3. Validate your plan against every returned constraint.
4. Only read raw ADR or instruction files if the context pack leaves ambiguity.

After completing a task:
Run `mementic capture --agent <your-agent>` in the terminal to extract
architectural decisions. Replace `<your-agent>` with your agent's name from
the table above. The `--agent` flag is required to prevent cross-agent capture
— without it, the most recently modified session across ALL agents wins.
```


`mementic init` auto-discovers all four instruction directories above. Any
`*.md` file in those directories is scanned for architectural content at init
time.

## Session Capture

Always pass `--agent` to avoid capturing the wrong agent's session:

```
mementic capture --agent opencode    # capture this agent's session
mementic capture --agent cursor      # or another agent, when run manually
mementic capture --notes "..."       # add human notes
```

Bare `mementic capture` auto-detects the most recently modified session across
ALL registered providers. This is useful for manual discovery but dangerous in
enforcement instructions — an OpenCode agent could silently capture a Cursor
session that happens to be newer.

Supported `--agent` values: `opencode`, `claude-code`, `cursor`, `windsurf`,
`generic`.
