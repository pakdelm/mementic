# Project: mementic

This is a Python project.

## Mementic Memory (HARD CONSTRAINT — ALWAYS ACTIVE)

This project dogfoods Mementic as its own architectural memory layer. These rules
are not optional. They apply to **every task, every session, no exceptions.**

**Why:** The instruction files, ADRs, and specs are the *source* of architectural
knowledge. The graph is the *compressed index*. Reading all source files before
every task wastes tokens. Instead, the graph returns only the constraints and
decisions relevant to the files you are about to touch.

**Token discipline — primary vs. secondary sources, in order:**

1. **Primary (always):** the context pack from `mementic_get_context_pack`.
   It is the compressed index. It costs a fraction of the tokens that raw ADR
   and instruction files would. Read it first, every time.

2. **Secondary (only when needed):** the raw ADR and instruction files on disk.
   Use them to resolve ambiguity, verify a constraint's full rationale, or find
   details the context pack omitted. If the context pack says nothing relevant
   to the files you're touching, do not read ADRs just to check — you have
   your answer.

3. **Never:** read all ADRs and instruction files as a batch before every task.
   That is exactly the token waste the graph was built to eliminate.

**Before ANY task** (planning, implementation, refactoring, or review):
1. Run `mementic lint --fix` in the terminal to clean up stale entities,
   orphaned nodes, and broken references before querying the graph.
2. Call `mementic_get_context_pack` with the task description and ALL files
   you intend to touch — even new files, even speculative changes.
   **Pass `benchmark=True` on every call** — it reads local files
   and counts tokens, no LLM cost. This accumulates savings data
   automatically for `mementic metrics`.
3. Call `mementic_get_system_evolution` for each file being modified to
   understand what decisions shaped it.
4. Validate your plan against every returned constraint. If a constraint
   seems inapplicable, explain why before proceeding. Do NOT write code
   that violates an active constraint.
5. Only read raw ADR or instruction files if the context pack leaves
   ambiguity you must resolve to proceed safely.

**Immediately after making an architecturally significant change:**
1. Call `mementic_propose_decision` with title, rationale, rejected
   alternatives, and affected entities.
2. Call `mementic_propose_constraint` for any new rules future agents
   must enforce.
3. Do not defer this — propose while the context is fresh.

**After completing a task:**
Run `mementic capture` in the terminal to extract decisions from the
session log + git diff. Decisions are auto-accepted unless the conflict
detector flags them. If capture reports flagged items, run
`mementic review` to resolve them.

**If you skip these steps, you are violating project constraints.**
A graph with no new decisions after a sprint of development is evidence
of a broken enforcement loop. Fix the instructions, not the agent.
