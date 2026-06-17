# Project: mementic

This is a Python project.

## Mementic Memory (HARD CONSTRAINT — ALWAYS ACTIVE)

This project dogfoods Mementic as its own architectural memory layer. These rules
are not optional. They apply to **every task, every session, no exceptions.**

The context pack replaces reading **ADRs, specs, and instruction files** — it
tells you WHAT rules exist. It does NOT replace reading the **code you are about
to edit** — that tells you WHERE the change goes. Read code surgically: grep/glob
to the exact function, then read that section. Never batch-read ADRs or specs;
that is the token waste the graph eliminates.

**Before ANY task** (planning, implementation, refactoring, or review):
1. Call `mementic_get_context_pack` with the task description and ALL files
   you intend to touch — even new files, even speculative changes.
2. **Sufficiency gate — output these two lines before any further reads:**
   1. `Context pack says:` — 1-2 sentence summary (or "returned nothing relevant").
   2. `Files I still need to read and why:` — every code file you will open,
      each with a one-clause justification. If a file is not on this list,
      do not read it.
3. Call `mementic_get_system_evolution` for each file being modified.
4. Validate your plan against every returned constraint. Do NOT write code
   that violates an active constraint.
5. Only read raw ADR or instruction files if the context pack leaves
   ambiguity you must resolve to proceed safely.

**If the context pack fails:** State the error explicitly, fall back to
targeted reads only (grep first, read specific sections), and report the
failure to the user. Do not silently read entire files wholesale.

**Proposing decisions/constraints is cheap (SQLite writes, no LLM). Capture is
expensive (LLM extraction). Use the right one:**

- **Inline proposals — always, no gate.** When you make a decision or establish
  a rule, call `mementic_propose_decision` / `mementic_propose_constraint`
  immediately. No judgment call about "architectural significance" — if it's
  worth remembering, propose it. If it's not, don't.
- **`mementic capture` — user-triggered, not agent-triggered.** Capture runs
  LLM extraction on the session log. It exists for sessions where architectural
  discussions happened but were NOT proposed inline (e.g., a design
  conversation that never produced an explicit proposal). The user decides when
  that's worth the extraction cost — do not run it yourself mid-task.

**If you skip these steps, you are violating project constraints.**
A graph with no new decisions after a sprint of development is evidence
of a broken enforcement loop. Fix the instructions, not the agent.
