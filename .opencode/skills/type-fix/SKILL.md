---
name: type-fix
description: Use when fixing mypy type errors. Works through errors systematically from the bottom up — fix callees before callers.
---

# Type Fix

## Workflow

1. Run mypy to get the full error list:
   ```
   uv run mypy .
   ```
2. Group errors by file. Fix one file at a time.
3. Within each file, fix errors from bottom to top — callees before callers. This prevents cascading re-checks.
4. For each error:
   - Missing type annotation → add it.
   - Type mismatch → determine whether the type hint is wrong or the code is wrong. Fix whichever is incorrect.
   - `Any` inferred → add explicit type.
   - Incompatible override → ensure signature matches parent.
5. Re-run mypy after each file to confirm the fix didn't introduce new errors.
6. When all errors are fixed, run the test suite:
   ```
   uv run pytest
   ```

## Rules

- Never use `# type: ignore` without a comment explaining why.
- When a third-party library lacks type stubs, check if stubs exist (`types-*` packages) before silencing.
- Prefer narrowing unions with `isinstance` checks over `cast()`.
- If fixing a type error requires changing a public API, flag it for review.
- For complex generics, add a `TypeAlias` to keep signatures readable.
