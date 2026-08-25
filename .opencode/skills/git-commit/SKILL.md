---
name: git-commit
description: Use when preparing a git commit. Reviews the diff, writes a conventional commit message, and optionally pushes. Never commits without explicit user approval.
---

# Git Commit

## Workflow

1. Check the current state:
   ```
   git status
   git diff
   git diff --cached
   ```
2. Review the diff for:
   - Secrets, `.env` references, or generated artifacts — block the commit if found.
   - Unrelated changes that should be in a separate commit — flag them.
   - Missing test coverage for new behavior — flag it.
3. Write a conventional commit message following the project style:
   ```
   <type>: <imperative description>

   <optional body with rationale and affected modules>
   ```
   Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `style`, `perf`.
4. Present the proposed commit message to the user for approval.
5. Only stage and commit when the user explicitly says to:
   ```
   git add <files>
   git commit -s -m "<message>"
   ```
   The `-s` flag adds the `Signed-off-by` trailer required by the DCO
   (`.github/workflows/dco.yml` fails any PR commit without it). Never omit it.

## Rules

- Every commit must carry a Signed-off-by trailer (`git commit -s`). If a
  push is rejected or the DCO check fails, amend with `git commit --amend -s`
  only after the user asks.
- Never commit without explicit user instruction.
- Never commit secrets or generated artifacts.
- Never amend commits or force-push unless the user explicitly asks.
- If tests don't pass, block the commit and tell the user why.
- If lint or type check fails, block the commit and offer to fix first.
- One logical change per commit. Split unrelated changes into separate commits.
