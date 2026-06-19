# Project: mementic

This is a Python project.

## Mementic Memory (HARD CONSTRAINT — ALWAYS ACTIVE)

This project dogfoods Mementic as its own architectural memory layer. These rules
are not optional. They apply to **every task, every session, no exceptions.**
"Task" means ANY user request — including questions, exploration, planning,
code review, and informational queries. There are no read-only exceptions.
If you are about to read files or search code, you need context first.

The context pack replaces reading **ADRs, specs, and instruction files** — it
tells you WHAT rules exist. It does NOT replace reading the **code you are about
to edit** — that tells you WHERE the change goes. Read code surgically: grep/glob
to the exact function, then read that section. Never batch-read ADRs or specs;
that is the token waste the graph eliminates.

**After EVERY user message** (not just "before a task" — each message is a new
context window):
1. Call `mementic_get_task_context` with a clear task description describing
   what you are about to do. Be specific about the domain, action, and
   concerns (e.g., "fix authentication bug in login flow" not just "fix bug").
2. **Sufficiency gate — output these two lines before any further reads:**
   1. `Context pack says:` — 1-2 sentence summary (or "returned nothing relevant").
   2. `Files I still need to read and why:` — every code file you will open,
      each with a one-clause justification. If a file is not on this list,
      do not read it.
3. Call `mementic_get_entity_history` for each file being modified.
4. Validate your plan against every returned constraint. Do NOT write code
   that violates an active constraint.
5. Only read raw ADR or instruction files if the context pack leaves
   ambiguity you must resolve to proceed safely.

**Why every message, not just "before a task":** Context packs are free (no LLM
calls, just SQLite reads). Architectural constraints can change between messages
(e.g., another agent session captured a new decision). Assuming previous context
is still valid is how violations happen. The cost of re-fetching is near zero;
the cost of stale context is a violated constraint.

**You may NOT skip these steps by classifying a task as "read-only", "quick",
or "informational". The context pack exists precisely so you don't waste tokens
reading raw files to understand what rules apply. Skipping it defeats the
purpose.**

**If the context pack fails:** State the error explicitly, fall back to
targeted reads only (grep first, read specific sections), and report the
failure to the user. Do not silently read entire files wholesale.

**Proposing decisions/constraints is cheap (SQLite writes, no LLM). Capture is
expensive (LLM extraction). Use the right one:**

- **Inline proposals — always, no gate.** When you make a decision or establish
  a rule, call `mementic_record_decision` / `mementic_record_constraint`
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
