---
name: test-debug
description: Use when diagnosing and fixing failing tests. Follows a systematic bisect-and-fix workflow — reproduce, isolate, identify root cause, apply minimal fix, verify.
---

# Test Debug

## Workflow

1. Reproduce the failure: run the failing test with verbose output.
   ```
   uv run pytest -xvs path/to/test_file.py::test_name
   ```
2. Read the test and the source code it exercises.
3. Determine whether the failure is in the test (wrong expectation) or the source (real bug).
4. If the root cause is unclear, add temporary debug prints or use `--pdb`:
   ```
   uv run pytest --pdb -xvs path/to/test_file.py::test_name
   ```
5. Apply the smallest possible fix.
6. Re-run the specific test to confirm the fix.
7. Run the full suite to check for regressions:
   ```
   uv run pytest
   ```
8. If the bug was in source code, add a regression test that would have caught it.

## Rules

- Never change both the test and the source at the same time — fix one, verify, then the other.
- If the test expectation is wrong, confirm with the spec or existing behavior before changing it.
- Check `git log -p -- path/to/file.py` to understand why the code was written that way.
- If the failure is flaky (passes sometimes), mark it with `@pytest.mark.flaky` and file a follow-up.
