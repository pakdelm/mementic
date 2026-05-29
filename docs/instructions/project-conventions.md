# Project Conventions

## Repository Management

- Use `git` for version control.
- Write concise, imperative commit messages (e.g., "Add user authentication").
- Never commit secrets, `.env` files, or generated artifacts.

## Dependency Management

- Use `pip` / `pyproject.toml` for dependency declaration.
- Pin dependencies with exact versions in production.
- Use dev-dependencies for tools (linters, test runners, type checkers).

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
