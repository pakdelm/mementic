---
name: python-refactor
description: Use when refactoring Python code — extracting functions, renaming symbols, restructuring modules, or applying design patterns safely with test verification.
---

# Python Refactor

## Workflow

1. Read the target module and its tests.
2. Identify the refactoring to perform.
3. Run the existing test suite to establish a baseline.
4. Apply the refactoring in small, verifiable steps.
5. Run tests after each step.
6. Update any affected imports across the codebase.

## Safety Rules

- Never change public API signatures without updating callers.
- Keep backward compatibility unless explicitly asked to break it.
- Run `ruff check .` and `mypy .` after each change.
