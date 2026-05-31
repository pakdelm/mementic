# Project: mementic

This is a Python project.

## Build, Lint, and Test Commands

```bash
# Install dependencies
uv sync

# Lint
uv run ruff check .

# Type check
uv run mypy .

# Run tests
uv run pytest
```

## Project Structure

```
.opencode/          # OpenCode configuration (agents, skills, prompts, specs)
docs/instructions/  # Project-specific coding guidelines
src/                # Source code
tests/              # Tests
```

## Code Standards

- Follow the conventions in docs/instructions/
- Use type hints throughout
- Write tests for all new functionality

## External References

For detailed guidelines, read the instruction files configured in opencode.json:
- @docs/instructions/python-style.md
- @docs/instructions/testing.md
- @docs/instructions/project-conventions.md

## Mementic Memory

This project uses Mementic as its own architectural memory layer.
**You MUST use the following MCP tools as part of your workflow:**

- **Before any implementation task** that touches existing modules:
  call `mementic_get_context_pack` with the task description and affected
  file paths. Load the returned constraints and validate your plan against
  them. Do not write code that violates an active constraint.

- **During a task**, when you make an architecturally significant decision:
  call `mementic_propose_decision` with the title, rationale, rejected
  alternatives, and affected entities. Call `mementic_propose_constraint`
  for any new rules that future agents must enforce.

- **After completing a task**, remind the user to run `mementic review`
  to accept the proposed decisions into permanent memory.
