---
description: Release a new version — deterministic, no hand-editing files
---

Release the project at version `$ARGUMENTS`.

## Execution

Run the deterministic release script. It handles the mechanical work: branch
creation, version bump, changelog update, commit, and branch push.

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
4. Commit and push the branch

## After the script

Inform the user: a PR needs to be created manually from the pushed release
branch to `main`. **Must use merge commit** (not squash or rebase) or the tag
becomes orphaned.

After the PR merges, pull `main` and tag the release:
```
uv run python scripts/release.py <version> --tag-only
```

Pushing the tag triggers CI to build the wheel and sdist and publish the
GitHub Release from the version's `CHANGELOG.md` section. No local token is
required; re-run the workflow to retry.
