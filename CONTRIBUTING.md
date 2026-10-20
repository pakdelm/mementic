# Contributing to mementic

## Development Setup

1. Clone the repository:
   ```sh
   git clone https://github.com/pakdelm/mementic.git
   cd mementic
   ```

2. Install dependencies (Python 3.11+ required):
   ```sh
   uv sync
   ```

3. Verify setup:
   ```sh
   uv run pytest
   uv run ruff check .
   uv run mypy .
   ```

## Pull Request Checklist

Before submitting a pull request, ensure:

1. All tests pass with `uv run pytest`
2. Lint clean with `uv run ruff check .`
3. Type check clean with `uv run mypy .`
4. No new dependencies without discussion
5. Tests cover new functionality
6. Follow existing code style (PEP 8, 100-char line limit, Google-style docstrings)

## DCO (Developer Certificate of Origin)

All contributions must include a `Signed-off-by: Name <email>` line in every commit
message. This certifies the contributor has the right to submit the work under the
project's Apache-2.0 license.

The email in the sign-off must match the commit author email. Committing under one
identity and signing off with another fails the DCO check.

See https://developercertificate.org/ for the full DCO text.

Use the `-s` flag to add the Signed-off-by line automatically:
```sh
git commit -s -m "Add feature X"
```

This requires `git user.name` and `user.email` to be configured.

Run `uv run pre-commit install` once to install the hooks. That includes the
`commit-msg` check, which catches a missing or mismatched sign-off before the
commit is created. `git commit --no-verify` skips it, but CI still enforces it.

## Code Style

- **PEP 8** with a 100-character line limit
- **Google-style docstrings** for all public functions and classes
- **Type hints** on all function signatures (parameters and return types)
- Use `|` union syntax over `Optional` / `Union` (Python 3.10+)
- **Absolute imports** ordered: standard library → third-party → local

## Getting Help

- Use [GitHub Discussions](https://github.com/pakdelm/mementic/discussions) for questions.
- Use [issues](https://github.com/pakdelm/mementic/issues) for bugs and feature requests.
