## Mementic Memory (HARD CONSTRAINT — ALWAYS ACTIVE)

This project uses Mementic as its architectural memory layer. These rules
are not optional. They apply to **every task, every session, no exceptions.**
"Task" means ANY user request — including questions, exploration, planning,
code review, and informational queries. There are no read-only exceptions.
If you are about to read files or search code, you need context first.

The mementic memory replaces reading **ADRs, specs, and instruction files** — it
tells you WHAT rules exist. It does NOT replace reading the **code you are about
to edit** — that tells you WHERE the change goes. Read code surgically: grep/glob
to the exact function, then read that section. Never batch-read ADRs or specs;
that is the token waste the graph eliminates.

**After EVERY user message** (not just "before a task" — each message is a new
context window):
1. Think about the task: what domains, concepts, and concerns are relevant?
   Generate 5-12 narrow, single-word technical keywords. Favor specific
   terms (e.g., ``argon2``, ``pydantic``, ``SessionMiddleware``) over
   generic ones (e.g., ``security``, ``validation``, ``middleware``).
   Generic keywords match too many items and bloat context packs.
   If fewer than 5 keywords apply, 3-4 precise ones are better than
   padding with broad terms. Example for "fix authentication bug":
   ["argon2", "token", "login", "session"] — skip "security", "password",
   "hashing" because they match dozens of unrelated items.
2. Run `mementic query keywords <kw1> <kw2> ...` with those keywords.
   Echo the `[mementic: ...]` summary line to the user.

   **Multi-topic tasks:** When the user request spans multiple distinct
   sub-tasks, run `mementic query keywords` for each sub-task with focused,
   single-topic keywords. Separate queries produce better keyword matches
   than one diluted query for the whole.
3. **Sufficiency gate — output these two lines before any further reads:**
   1. `mementic:` — 1-2 sentence summary (or "returned nothing relevant").
   2. `Files I still need to read and why:` — every code file you will open,
      each with a one-clause justification. If a file is not on this list,
      do not read it.
4. Run `mementic query file <path>` for each file being modified.
5. Validate your plan against every returned constraint. Do NOT write code
   that violates an active constraint. Cite every entity you apply or
   reject with `[mementic: <id>]` so usage is auditable in the session log.

**Why every message, not just "before a task":** Context packs are free (no LLM
calls, just SQLite reads). Architectural constraints can change between messages
(e.g., another agent session captured a new decision). Assuming previous context
is still valid is how violations happen. The cost of re-fetching is near zero;
the cost of stale context is a violated constraint.

**You may NOT skip these steps by classifying a task as "read-only", "quick",
or "informational". The mementic memory exists precisely so you don't waste tokens
reading raw files to understand what rules apply. Skipping it defeats the
purpose.**

**If the mementic memory fails:** State the error explicitly, fall back to
targeted reads only (grep first, read specific sections), and report the
failure to the user. Do not silently read entire files wholesale.

**Echo summaries (HARD GATE — check before responding):** Every mementic
tool call returns a `[mementic: ...]` summary line. You MUST echo that line
verbatim to the user in your response. This applies to `query keywords`,
`query file`, `add decision`, `add constraint`, `deprecate`, and every other
mementic command. If you called a mementic command and the `[mementic: ...]`
line is not in your response text, fix it before sending. The user should see
exactly what mementic contributed to every message. Skipping this is a hard
constraint violation — equivalent to skipping `query keywords` itself. The
cost is zero (already computed), so there is no excuse.

**Proposing decisions/constraints is cheap (SQLite writes, no LLM). There is no
other write path:**

- **Inline proposals — always, no gate.** When you make a decision or establish
  a rule, run `mementic add decision <title> --rationale "<text>"` and
  `mementic add constraint <desc> -e <path>` immediately. No judgment call
  about "architectural significance" — if it's worth remembering, propose it.
  If it's not, don't. Duplicate proposals are skipped automatically; changed
  content under the same title deprecates the old entry and accepts the new one.
- **Multiple deprecations** — run `mementic deprecate <id1> <id2> ...`
  to retire outdated rules in one call.

**History first — tombstones and duplicates:**

- When you encounter a deprecated/tombstoned entry in any listing, run
  `mementic history <id>` before acting on it — the reason and any successor
  chain are recorded there.
- Before re-adding content that might already exist, check first with
  `mementic history <id>` (or `mementic query search <term>`); don't duplicate
  entries that were retired or already recorded.

**If you skip these steps, you are violating project constraints.**
A graph with no new decisions after a sprint of development is evidence
of a broken enforcement loop. Fix the instructions, not the agent.
