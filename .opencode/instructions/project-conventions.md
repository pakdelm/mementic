# Project Conventions

## Repository Management

- Use `git` for version control.
- Write concise, imperative commit messages (e.g., "Add user authentication").
- Never commit secrets, `.env` files, or generated artifacts.
- Commit or push only with explicit user approval. Exception: a user-launched autonomous spec
  run (`/ship`, `/integrate`, the `orchestrator` agent) is that approval for the `spec-lead`
  and `integrator` agents to commit inside spec worktrees and push `spec/*` branches. They
  never push `main`, never rebase or force-push, and never merge. The human reviews and merges
  each PR.

## Dependency Management

- Use `uv` as the package manager and `pyproject.toml` for dependency declaration.
- Run `uv sync` to install dependencies; `uv add` to add packages; `uv lock` to regenerate the lockfile.
- Keep `uv.lock` committed for reproducible builds.
- Use dev-dependencies for tools (linters, test runners, type checkers).
- Prefer `uv run <tool>` over global tool installations.

## Configuration

- Use environment variables for secrets and environment-specific settings.
- Use `.env.example` to document required variables.
- Never hardcode secrets or credentials.

## Logging

- Use the `logging` module (not `print`).
- Configure log levels via environment variable.
- Include contextual information in log messages.

## Error Handling

- Handle errors at the appropriate level.
- Log exceptions with tracebacks.
- Return meaningful error messages to users.
