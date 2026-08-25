# Python Code Style

## General Principles

- Write clear, readable code over clever code.
- Follow PEP 8 conventions.
- Maximum line length: 100 characters.
- Avoid complex abstractions.
- Use interfaces where necessary.
- Use json configs for envs that are no secrets.
- Use .env for secrets only.

## Type Hints

- Use type hints for all function signatures (parameters and return types).
- Use `from __future__ import annotations` for forward references.
- Prefer `|` union syntax over `Optional` / `Union` (Python 3.10+).

## Imports

- Standard library first, then third-party, then local.
- Use absolute imports.
- Never use `import *`.

## Naming

- Classes: `PascalCase`
- Functions, methods, variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private members: prefix with single underscore `_private`

## Docstrings

- Use Google-style docstrings for all public functions and classes.
- Include a brief description, Args, Returns, and Raises sections.

## Comments

- **No section-banner comments.** Never write decorative divider lines
  (`# --- Foo ---`, `# === Foo ===`, `# ── Foo ──`) to group code. If a
  region needs a label, extract it into a well-named function with a
  docstring.
- **No narration comments.** Never write a comment that restates what the
  next line obviously does (`# increment counter`, `# call the API`).
  Comments may only carry non-obvious *why*: rationale, constraints,
  gotchas, or evidence that cannot be expressed in code.
- **Intent lives in docstrings, not comments.** If a comment explains the
  purpose or contract of a function/class/constant, move it into the
  entity's docstring.
- **No ticket/spec breadcrumb tags** (`Spec 30 Phase 2`, `JIRA-123`) in
  comments or docstrings. Provenance belongs in version control and specs,
  not sprinkled through source.
- Prefer extracting a helper function with a docstring over writing a
  paragraph of comments above an inline block.

## Error Handling

- Raise specific exceptions, never bare `Exception`.
- Use custom exception classes where appropriate.
- Avoid bare `except:` clauses.
