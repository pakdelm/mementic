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
.opencode/          # OpenCode configuration (agents, skills, prompts)
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
