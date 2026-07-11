## Mementic Memory (HARD CONSTRAINT — ALWAYS ACTIVE)

This project uses Mementic as its architectural memory layer. These rules
apply to every task, every session, no exceptions. "Task" means ANY user
request — including questions, exploration, planning, code review, and
informational queries. There are no read-only exceptions. If you are
about to read files or search code, you need context first.

**After EVERY user message** (not just "before a task" — each message is a new
context window):
1. Think about the task: what domains, concepts, and concerns are relevant?
   Generate 15-25 single-word keywords that capture the task's domain
   (e.g., for "fix authentication bug" → ["auth", "token", "security",
   "password", "hashing", "argon2", "login", "session"]).
2. Call `mementic_get_rules(keywords=[...])` with those keywords.
   Echo the `[mementic: ...]` summary line to the user.
2. **Sufficiency gate — output these two lines before any further reads:**
   - `mementic:` — 1-2 sentence summary (or "returned nothing relevant").
   - `Files I still need to read and why:` — each with one-clause justification.
3. Call `mementic_get_file_history` for each file being modified.
4. Validate your plan against every returned constraint. Cite every entity you
   apply or reject with `[mementic: <id>]` so usage is auditable in the session log.
5. Only read raw ADR or instruction files if the mementic memory leaves ambiguity.

**You may NOT skip these steps by classifying a task as "read-only", "quick",
or "informational". The mementic memory exists precisely so you don't waste tokens
reading raw files to understand what rules apply. Skipping it defeats the
purpose.**

**Echo summaries:** Every mementic MCP tool response includes a `[mementic: ...]`
summary line. Echo it to the user so they see what mementic contributed.

**Proposing decisions/constraints (cheap, no LLM):**
When you make a decision or establish a rule, call
`mementic_record_decision` / `mementic_record_constraint` immediately.
To retire an outdated rule, call `mementic_deprecate`.

**Session capture (expensive, LLM-driven):**
Run `mementic capture` at most once per session (end of session). Skip if
all decisions were proposed inline via the MCP tools above.

**Post-recording cleanup:** After recording decisions or constraints via
`mementic_record_decision` / `mementic_record_constraint`, call
`mementic_review_session` with the feature description and any entities
you deprecated.

**If the mementic memory fails:** State the error explicitly, fall back to
targeted reads only, and report the failure to the user.