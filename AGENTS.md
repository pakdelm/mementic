# Project: mementic

This is a Python project.

<!-- mementic:start -->
## Mementic Memory (HARD CONSTRAINT — ALWAYS ACTIVE)

This project keeps its architectural rules in a local graph (`.mementic/`).
These steps apply to EVERY user message — questions, exploration, planning,
review, and informational queries included. There are no read-only exceptions.
Query before any read; record decisions inline as you make them.

1. **Query.** Before reading files or searching code, run
   `mementic query keywords <5-12 narrow terms>` (retry as
   `uv run mementic <command>` if `mementic` is not on PATH). For
   multi-topic requests, run one focused query per sub-task. Echo the
   `[mementic: ...]` summary line verbatim in your response — it is the
   auditable trace of what mementic contributed.
2. **Sufficiency gate.** Before any further reads, output two lines:
   `mementic:` (1-2 sentence summary, or "returned nothing relevant") and
   `Files I still need to read and why:` (every file you will open, each
   with a one-clause justification).
3. **File gate.** Before opening ANY file from that list, run
   `mementic query file <path>` for every path. Locating files with
   grep/glob is fine; reading content without a query is not. This covers
   exploration reads too.
4. **Validate.** Do not write code that violates an active constraint. Cite
   every entity you apply or reject as `[mementic: <id>]`.
5. **Record inline.** When you make a decision or establish a rule, run
   `mementic add decision TITLE -r "..."` or
   `mementic add constraint DESC -e PATH` immediately — no gate. Retire
   superseded rules with `mementic deprecate ID... -r "reason"`. Before
   re-adding content, check `mementic history <id>` — tombstones carry
   reasons and successor chains.

The pack replaces reading ADRs, specs, and instruction files wholesale; it
does not replace reading the code you are about to edit. If mementic fails,
state the error, fall back to targeted reads (grep first), and report it.
Re-query every message: constraints change between sessions, and the cost of
re-fetching is near zero.
<!-- mementic:end -->
