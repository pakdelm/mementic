## Mementic Memory (HARD CONSTRAINT — ALWAYS ACTIVE)

This project uses Mementic as its architectural memory layer. These rules
apply to every task, every session, no exception.

**Before ANY task** (planning, implementation, refactoring, or review):
1. Call `mementic_get_task_context` with the task description and ALL files
   you intend to touch — even new files, even speculative changes.
2. **Sufficiency gate — output these two lines before any further reads:**
   - `Context pack says:` — 1-2 sentence summary (or "returned nothing relevant").
   - `Files I still need to read and why:` — each with one-clause justification.
3. Call `mementic_get_entity_history` for each file being modified.
4. Validate your plan against every returned constraint.
5. Only read raw ADR or instruction files if the context pack leaves ambiguity.

**Proposing decisions/constraints (cheap, no LLM):**
When you make a decision or establish a rule, call
`mementic_record_decision` / `mementic_record_constraint` immediately.

**Session capture (expensive, LLM-driven):**
Run `mementic capture` at most once per session (end of session). Skip if
all decisions were proposed inline via the MCP tools above.

**If the context pack fails:** State the error explicitly, fall back to
targeted reads only, and report the failure to the user.