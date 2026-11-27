---
name: implement-spec
description: Deliver a whole spec with the local planner, spec-lead, implementer, and reviewer stack; interactive single-spec work bypasses spec-lead, autonomous single-spec work launches one spec-lead, batches go through /ship.
disable-model-invocation: true
---

# Implement Spec

Adapted from Matt Pocock's `implement-spec` skill. The local equivalent of the
upstream integration branch is the `spec/<NN>-<slug>` branch that `spec-lead`
creates. `spec-lead` stays the autonomous per-spec worker; this skill decides
whether that layer is needed.

## Choose the mode

- **Interactive single spec** (default when the user is present and wants to
  watch): run the loop in the current session. Skip both `spec-lead` and the
  orchestrator.
- **Autonomous single spec** (the user says "autonomously", "open a PR", or
  "don't ask me"): launch one `spec-lead` directly and skip the orchestrator.
- **Batch or stacked specs** (two or more specs, or one spec stacked on another):
  use `/ship <NN...>`. The orchestrator owns lane planning, stacking, and the
  integrator. Do not duplicate that logic here.

If the mode is unclear, ask once. Do not ask again after launch.

## Ground yourself first

Run `uv run mementic query keywords --compact <5-12 narrow terms>` and echo the
`[mementic: ...]` line. Before reading a file, run
`uv run mementic query file <path> --compact`. Cite every constraint you apply as
`[mementic: <id>]`. Record `MAIN` = `git rev-parse --show-toplevel`; mementic
commands always run with `workdir: MAIN`.

## Ensure the ticket graph

Read `.opencode/specs/<NN>_<slug>.plan.md` if it exists. If it does not, launch the
`planner` subagent against the spec and use its plan; in interactive mode, the
user may prefer `/to-tickets` first so they can approve the breakdown. If a plan
exists but the start ref has moved enough that its contracts are stale, replan the
remaining tickets with `planner`.

The plan is a **task graph**, not a step list. The **frontier** is every ticket
whose blockers are done. Keep working the frontier.

## Interactive single spec

1. Work the frontier in dependency order. For each ticket, either load the
   `implement` skill and do the work yourself, or launch one `implementer` with a
   fresh context window and a brief that points at the spec, the plan ticket, and
   the files.
2. After each ticket, verify the slice's named tests and run the four gates:
   `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
   `uv run mypy .`.
3. Run a fresh `reviewer` in slice mode on the change set. Forward `critical` and
   `warning` findings to the same implementer, then re-check.
4. Commit only with explicit user approval, using the `git-commit` skill. Never
   commit on `main`.
5. Tick the ticket in the plan when its acceptance criteria are proven. Set the
   spec status to `in-progress` when work starts and `done` when every criterion
   is proven, unless the user prefers to do that themselves.
6. If a ticket proves the plan wrong, replan the remaining tickets; do not force
   the original contract.
7. Stop after three review rounds on one ticket, record the unresolved findings
   as open items, and continue with independent tickets.

## Autonomous single spec

1. Build the `spec-lead` brief:

```
Spec: .opencode/specs/<NN>_<slug>.md
MAIN (mementic workdir only, never edit): <path>
Start ref: origin/main
PR base: main
Worktree name: spec-<NN>-<slug>
Branch: spec/<NN>-<slug>
Plan (from planner, verbatim): <paste, or "no plan; run planner first">
Other specs in flight: none
Known decisions: [mementic: <id>] ...
Mode: autonomous. Never ask the user. Resolve ambiguity yourself, record it,
flag it under Assumptions in the PR.
Report "PR_OPEN: <url>" as soon as the PR exists, and "FINAL: ..." at the end.
```

2. Launch `spec-lead` with the `subagent` tool and keep its `sessionID`. Do not
   run the implementation loop yourself; `spec-lead` owns the worktree, branch,
   commits, push, and PR.
3. End your turn after launching. Do not poll or sleep. When it reports
   `PR_OPEN:`, note the URL. When it reports `FINAL:`, relay the result.
4. Report the PR URL, the acceptance-criteria matrix, assumptions to check, open
   items, and any footprint deviations. Leave the worktree in place for follow-up
   fixes.

## Batch or stacked specs

Use `/ship <NN...>`. The orchestrator plans every spec, classifies independent,
hotspot-shared, and stacked lanes, launches at most three `spec-lead` runs at
once, and finishes with the `integrator`. Do not launch a parallel ad-hoc batch
from this skill.

## Rules

- Start every subagent with a fresh context window and communicate through
  pointers: spec path, plan path, ticket ID, commits. Do not paste context the
  subagent can read.
- `spec-lead` is the single writer of mementic decisions for its spec. In
  interactive mode, record durable decisions yourself with
  `uv run mementic add decision`.
- Never rebase, force-push, or merge a PR. Update branches with merge commits.
  `[mementic: autonomous-spec-agents-must-never]`
- Never edit files in `MAIN` during an autonomous run; it is only the workdir for
  mementic commands. `[mementic: agents-working-in-a-git-worktree-must]`
- Commit only on `spec/*` branches in autonomous runs; never on `main`.
- Run all four gates before a PR. A red gate means a draft PR, not a ready one.
- Clean up worktrees only after the PR is merged or the user asks. Spec-lead keeps
  them for follow-up fixes.

## Report

```
Mode: interactive | autonomous | batch
[mementic: ...]
Spec: .opencode/specs/<NN>_<slug>.md
Plan: .opencode/specs/<NN>_<slug>.plan.md | none
Tickets: <proven>/<total>   Frontier: <remaining>
PR: <url> | none
Gates: pytest, ruff, ruff format, mypy — pass | fail (<detail>)
Assumptions: <ids>   Open items: <count, one line each>
Worktree: <path or none>
```
