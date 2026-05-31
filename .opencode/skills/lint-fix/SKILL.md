---
name: lint-fix
description: Use when fixing lint violations reported by ruff. Applies auto-fixes first, then addresses remaining issues manually.
---

# Lint Fix

## Workflow

1. Run auto-fix first:
   ```
   uv run ruff check . --fix
   ```
2. Run the check again to see what remains:
   ```
   uv run ruff check .
   ```
3. For each remaining violation, read the line and apply the fix manually.
4. If a violation requires a design decision (e.g., "function too complex"), flag it for the user instead of silently refactoring.
5. Re-run ruff to confirm zero violations.
6. Run type check to make sure fixes didn't introduce type errors:
   ```
   uv run mypy .
   ```
7. Run tests to confirm nothing broke:
   ```
   uv run pytest
   ```

## Rules

- Never suppress a rule with `# noqa` without explaining why.
- Prefer `--fix` for mechanical fixes (unused imports, trailing whitespace, etc.).
- For structural violations (complexity, naming), fix the root cause, not the symptom.
- If a file has many violations, fix them in logical groups with a test run between each group.
