# Project: mementic

This is a Python project.

## Mementic Memory (HARD CONSTRAINT — ALWAYS ACTIVE)

This project dogfoods Mementic as its own architectural memory layer. These rules
are not optional. They apply to **every task, every session, no exceptions.**

**Why:** The instruction files, ADRs, and specs are the *source* of architectural
knowledge. The graph is the *compressed index*. Reading all source files before
every task wastes tokens. Instead, the graph returns only the constraints and
decisions relevant to the files you are about to touch.

**Before ANY task** (planning, implementation, refactoring, or review):
1. Run `mementic lint --fix` in the terminal to clean up stale entities,
   orphaned nodes, and broken references before querying the graph.
2. Call `mementic_get_context_pack` with the task description and ALL files
   you intend to touch — even new files, even speculative changes.
3. Call `mementic_get_system_evolution` for each file being modified to
   understand what decisions shaped it.
4. Validate your plan against every returned constraint. If a constraint
   seems inapplicable, explain why before proceeding. Do NOT write code
   that violates an active constraint.

**Immediately after making an architecturally significant change:**
1. Call `mementic_propose_decision` with title, rationale, rejected
   alternatives, and affected entities.
2. Call `mementic_propose_constraint` for any new rules future agents
   must enforce.
3. Do not defer this — propose while the context is fresh.

**After completing a task:**
Run `mementic capture` in the terminal to extract decisions from the
session log + git diff. Then `mementic review` to inspect any
conflict-flagged items.

**If you skip these steps, you are violating project constraints.**
A graph with no new decisions after a sprint of development is evidence
of a broken enforcement loop. Fix the instructions, not the agent.
