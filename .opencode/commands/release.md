---
description: Release a new version — deterministic, no hand-editing files
---

Release the project at version `$ARGUMENTS`.

## Execution

Run the deterministic release script. It handles ALL mechanical work: branch
creation, version bump, changelog update, commit, tag, and push.

```
uv run python scripts/release.py $ARGUMENTS
```

If the user asks for a preview first:
```
uv run python scripts/release.py $ARGUMENTS --dry-run
```

The script will:
1. Validate: on `main`, clean tree, valid semver, version > current, no duplicate tag/branch
2. Create branch `release/v{version}`
3. Bump `pyproject.toml` and update `CHANGELOG.md`
4. Commit, tag, push branch + tag

## After the script

Inform the user: a PR needs to be created manually from the pushed release
branch to `main`. **Must use merge commit** (not squash or rebase) or the tag
becomes orphaned.
