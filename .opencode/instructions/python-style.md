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

## Error Handling

- Raise specific exceptions, never bare `Exception`.
- Use custom exception classes where appropriate.
- Avoid bare `except:` clauses.
