---
name: implement
description: Implement one spec, plan ticket, or focused change in the current workspace with TDD, mementic constraints, the project gates, and a reviewer pass.
disable-model-invocation: true
---

# Implement

Adapted from Matt Pocock's `implement` skill. This version works in the current
workspace, uses the repository's `planner`/`implementer`/`reviewer` agents where
they help, and never commits without explicit approval.

## Process

### 1. Resolve the target

The target is one of:

- a spec file such as `.opencode/specs/54_deterministic-export-import.md`
- a plan file such as `.opencode/specs/54_deterministic-export-import.plan.md`
- a ticket such as `T2` inside that plan
- a focused change described in the conversation

If the reference is ambiguous, ask once. If a plan exists, work its **frontier**:
the tickets whose blockers are all done. Do not skip a blocker.

### 2. Ground yourself

Run `uv run mementic query keywords --compact <5-12 narrow terms>` and echo the
`[mementic: ...]` line. Before opening any file, run
`uv run mementic query file <path> --compact`. Cite every constraint you apply or
reject as `[mementic: <id>]`. If the plan lists decisions, read them instead of
re-deriving them.

### 3. Agree the seams

For each acceptance criterion in scope, name the public seam where behavior is
observed and the test that will prove it. Prefer an existing seam, use the highest
seam that works, and keep the number of new seams to a minimum. If the seams were
not already agreed, propose them and wait for a yes before writing tests.

### 4. Work test-first

For each criterion:

1. Write `test_<behavior>` and watch it fail for the right reason:
   `uv run pytest -xvs tests/test_<module>.py::test_<behavior>`.
2. Write the smallest change that makes it pass.
3. Repeat with the next criterion. Do not write all the tests first; that tests
   imagined behavior instead of the behavior the last cycle taught you.

Tests assert behavior through public interfaces, not implementation details. Do
not mock internal collaborators into meaninglessness, and do not weaken, skip, or
delete a test to get green.

### 5. Run the gates

Run the named test file often. Before reporting done, run all four gates from the
repository root:

```
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

`uv run ruff format .` is allowed to fix formatting; do not run bulk fixers over
files you did not touch. If a failure predates your change, say so and show the
baseline evidence.

### 6. Review

Launch the `reviewer` subagent in slice mode with:

```
Mode: slice
Worktree: <repository root>
MAIN (mementic workdir): <repository root>
Spec: <spec or plan path>
Acceptance criteria under review: <verbatim>
Change set: uncommitted changes in the worktree
Implementer report: <your summary>
Mementic constraints in play: [mementic: <id>] ...
```

Fix every `critical` and `warning` finding. `nit` findings are optional. If the
reviewer says the spec or plan is wrong, surface that to the user instead of
silently changing scope. If subagents are unavailable, run the same checklist
yourself.

### 7. Commit only with approval

Load the `git-commit` skill and follow it. Never commit, amend, push, or force-push
without explicit user approval. Autonomous spec runs are the exception, and those
are owned by `spec-lead`, not by this skill.

### 8. Record decisions and report

If the work settled a durable decision that is not yet in mementic, record it with
`uv run mementic add decision "<title>" -r "<rationale>"`. If you are working
inside a `spec-lead` worktree, leave decision recording to the spec-lead.

Report:

```
Target: <spec / plan / ticket>
[mementic: ...]
ACs:
- [x] <criterion> — tests/...::test_name
Files changed: <path> — <why>
Gates: pytest <result>, ruff <result>, ruff format <result>, mypy <result>
Assumptions: <choice — reason>
Open items: <none | list>
```

## Rules

- Stay inside the target's scope. Do not fix unrelated code in the same change.
- Do not add dependencies. Report the need instead.
- Use dataclasses for internal data and pydantic only where input is validated or
  parsed.
- Follow `.opencode/instructions/python-style.md`,
  `.opencode/instructions/testing.md`, and `.opencode/instructions/libraries.md`.
- Leave git history alone unless the user asks for a commit.
