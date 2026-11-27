---
name: to-tickets
description: Break a spec into a tracer-bullet ticket graph and persist it at .opencode/specs/<NN>_<slug>.plan.md for spec-lead and implement-spec.
disable-model-invocation: true
---

# To Tickets

Adapted from Matt Pocock's `to-tickets` skill. Tickets are local plan data, not
GitHub issues. One spec gets one plan file beside it:
`.opencode/specs/<NN>_<slug>.plan.md`. The plan reuses the `planner` agent's
slice format so `spec-lead` can consume it directly.

## Process

### 1. Resolve the source

Use the spec named by the user (a path, an `NN`, or a slug). If none is named,
use the spec from the current conversation. If the reference is ambiguous, ask.

Work from the spec file and its acceptance criteria. Do not invent scope the spec
does not contain.

### 2. Ground yourself

Run `uv run mementic query keywords --compact <5-12 narrow terms>` and echo the
`[mementic: ...]` line. Before reading a file, run
`uv run mementic query file <path> --compact`. Read the spec in full, then the
code each acceptance criterion touches.

### 3. Draft the vertical slices

Launch the `planner` subagent with:

```
Spec: .opencode/specs/<NN>_<slug>.md
Read from: MAIN at ref <BASE> (use `git show <BASE>:<path>` if MAIN has local changes)
Other specs in this batch: <NN slug, one line each> | none
```

If the planner is unavailable, do the same work yourself using its slicing rules.
If a plan file already exists, treat it as the draft and revise only what the
user asks to change.

### 4. Convert slices to tickets

Each ticket is a **tracer bullet**: a narrow but complete path through every layer
it touches (schema, domain, CLI or API, tests). A finished ticket is demoable or
verifiable on its own and fits in one fresh context window.

Give each ticket:

- an ID (`T1`, `T2`, ...)
- a short title
- the tickets that **block** it, or "None (can start immediately)"
- the end-to-end behavior it delivers, from the user's perspective
- its acceptance criteria, copied verbatim from the spec where possible
- the files it is expected to touch
- its interface contract (signatures, CLI options, schema, errors)
- the named test for each acceptance criterion

Order prefactoring first. A **wide refactor** is the exception to vertical
slicing: sequence it as **expand** (add the new form beside the old), **migrate**
call sites in batches that stay green, then **contract** (delete the old form).
The contract ticket is blocked by every migrate batch.

### 5. Quiz the user

Present the tickets as a numbered list. For each one, show the title, blockers,
and the behavior it delivers. Ask:

- Is the granularity right (too coarse or too fine)?
- Are the blocking edges genuine gates, not just ordering preferences?
- Should any tickets be merged or split further?
- Does each ticket fit one fresh context window?

Iterate until the user approves. Do not write the plan before approval.

### 6. Write the plan file

Write `.opencode/specs/<NN>_<slug>.plan.md`:

```markdown
# Plan: <Spec title>

**Spec:** .opencode/specs/<NN>_<slug>.md
**Status:** ready
**Generated:** <YYYY-MM-DD>
**Ref:** <git sha planned against>

[mementic: ...]

## Tickets

### T1 — <title>

**Blocked by:** None (can start immediately)

**What it delivers:** <end-to-end behavior>

**Acceptance criteria:**
- [ ] <criterion> — `tests/...::test_name`

**Files:** `<path>` (create|modify) — <what changes>

**Contract:** <signatures / CLI options / schema / errors>

**Tests:** `tests/...::test_name` — <what it asserts>

### T2 — <title>

**Blocked by:** T1

...

## Frontier

- T1, T3 (tickets whose blockers are all done)

## Notes

- <risks, assumptions, or out-of-scope items>
```

Use `**Status:** draft` while the breakdown is still being discussed, and
`**Status:** ready` once the user approves.

### 7. Report

Report the plan path, the ticket count, and the current frontier. The next step is
`/implement-spec <NN>` for interactive or autonomous execution, or `/ship <NN>`
when the spec is part of a batch.

## Rules

- One plan file per spec, never one combined file for several specs.
- Do not modify the spec or a parent issue as part of this skill.
- Do not create GitHub issues or native blocking links.
- The plan is local-only because `.opencode/specs/` is git-excluded. If the user
  needs it shared, they must commit or paste it explicitly.
- Prefer the planner's contract, test names, and footprints; do not silently
  rewrite them when converting to tickets.
