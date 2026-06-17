# Testing Guidelines

## Framework

- Use `pytest` as the test runner.
- Use `pytest-cov` for coverage reporting.
- Use `pytest-xdist` for parallel test execution (`-n auto`).

## Running Tests

- Full suite (parallel): `uv run pytest -n auto`
- Single test (sequential, for debugging): `uv run pytest -xvs path/to/test_file.py::test_name`
- With coverage: `uv run pytest -n auto --cov --cov-report=term-missing`

## Test Organization

- Mirror the source directory structure under `tests/`.
- Name test files `test_<module>.py`.
- Name test functions `test_<behavior>`.

## Writing Tests

- One assertion per test when practical.
- Use descriptive test names that explain the behavior.
- Use fixtures for shared setup (define in `conftest.py`).
- Parametrize tests for multiple input/output cases.
- Mock external dependencies; never call real APIs or databases in unit tests.

## Coverage

- Aim for >80% line coverage on new code.
- Run `uv run pytest -n auto --cov --cov-report=term-missing` to check.
