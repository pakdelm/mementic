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

3. Initialize the local graph and run the suite:
   ```sh
   uv run mementic init
   uv run pytest
   ```

   Lint, formatting, and type check commands are in the checklist below.

4. Install the pre-commit hooks:
   ```sh
   uv run pre-commit install
   ```

### Docker and sbx tests

The default test run excludes Docker tests. Run the docker marker explicitly:

```sh
uv run pytest -m docker -n 0
```

`scripts/sbx_smoke.py` is the manual Docker Sandboxes (sbx) smoke test:

```sh
uv run python scripts/sbx_smoke.py --layout both
```

It requires the `sbx` CLI with kit builder support; the builder sandbox handles
the image build, so a host Docker CLI is not required. Pass `--keep` to retain
the sandbox and temporary workspace for inspection. The kit installs the pinned
release tag, not the working tree, so the smoke test exercises the released
package; unreleased source changes are covered by `pytest` and the `-m docker`
contract tests.

Kit layout, the stem-matched recipe rule, the build command, and the version pin
are documented in [`docker/sbx-mementic/README.md`](docker/sbx-mementic/README.md).

## Pull Request Checklist

Before submitting a pull request, ensure:

1. All tests pass with `uv run pytest`
2. Lint clean with `uv run ruff check .`
3. Formatting clean with `uv run ruff format --check .`
4. Type check clean with `uv run mypy .`
5. No new dependencies without discussion
6. Tests cover new functionality
7. Follow existing code style (PEP 8, 100-char line limit, Google-style docstrings)
8. Docker tests pass if the change touches the kit or container path (`uv run pytest -m docker -n 0`)

## Licensing

Contributions are accepted under the Apache-2.0 license. By opening a pull
request you confirm you have the right to submit the work under that license.
No CLA or DCO sign-off is required.

## Code Style

- **PEP 8** with a 100-character line limit
- **Google-style docstrings** for all public functions and classes
- **Type hints** on all function signatures (parameters and return types)
- Use `|` union syntax over `Optional` / `Union` (Python 3.10+)
- **Absolute imports** ordered: standard library → third-party → local

## Getting Help

- Use [issues](https://github.com/pakdelm/mementic/issues) for bugs and feature requests.
