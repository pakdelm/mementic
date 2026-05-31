---
description: Release a new version (bump pyproject.toml, update CHANGELOG, commit, tag)
---

Release the project at version $ARGUMENTS (e.g. `0.2.0`). Execute the following
steps in order, stopping on any failure:

## 1. Validate version

- Strip a leading `v` if present.
- Confirm the version matches the pattern `X.Y.Z` (semver).
- If not valid, report the error and stop.

## 2. Pre-flight checks

- Confirm we are in a git repository (`git rev-parse --git-dir`).
- Confirm the working directory is clean (`git status --porcelain` produces no output).
- Confirm the tag `v{version}` does not already exist locally (`git tag --list v{version}`).

## 3. Read current version

Parse `pyproject.toml` to get the current `[project] version`.

## 4. Two scenarios

### Scenario A: version matches current version (first tag)

This means we are tagging the current state without bumping anything.

- Run: `git tag v{version}`
- If user requested push: `git push origin v{version}`
- Report what was done and stop.

### Scenario B: version is greater than current version (normal release)

Confirm the new version is strictly greater than the current version. If not, error.

**Update pyproject.toml:**
- Replace the `version = "..."` line under `[project]` with the new version.
- Use a regex or string replace that is precise enough to only match the version line.

**Update CHANGELOG.md:**
- If `CHANGELOG.md` does not exist, warn but continue (do not block).
- If it exists, replace the first occurrence of `[Unreleased]` with
  `[{new_version}] - {today's date in YYYY-MM-DD}`.
- If `[Unreleased]` is not found, warn but continue.

**Commit and tag:**
- `git add pyproject.toml` (and CHANGELOG.md if it was updated)
- `git commit -m "Release v{version}"`
- `git tag v{version}`

**Push (only if user explicitly requested):**
- `git push`
- `git push origin v{version}`

## 5. Report

Summarize every action taken. If the user asked for push and it was done,
remind them that the tag is now on the remote.

## Safety notes

- Never push unless the user explicitly asks for it (e.g. "release 0.2.0 and push").
- Never proceed if the working directory is dirty — tell the user to commit or stash first.
- Never force-overwrite an existing tag.
- If CHANGELOG.md is missing or has no `[Unreleased]` section, warn but don't block.
