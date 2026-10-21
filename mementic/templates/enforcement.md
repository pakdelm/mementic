<!-- mementic:start -->
## Mementic Memory (HARD CONSTRAINT — ALWAYS ACTIVE)

This project keeps its architectural rules in a local graph (`.mementic/`).
Run every mementic command from the directory that contains `.mementic`; if
your CWD is a subdirectory, `cd` to that directory first. Entity paths are
relative to it (`repo-a/src/auth.py`, not `src/auth.py`).

These steps apply to code work — reading, searching, editing, or making
architectural claims. Messages that trigger none of those (acknowledgements,
ops commands, meta discussion) need no query.

1. **Query.** Before the first file read, search, or code decision of a task,
   run `mementic query keywords --compact <5-12 narrow terms>` (retry as
   `uv run mementic <command>` if `mementic` is not on PATH). Re-query when
   the task shifts to a new module, domain, or file area; after recording an
   entry; and after context compaction. For multi-topic requests, run one
   focused query per sub-task. Echo the `[mementic: ...]` summary line
   verbatim in your response — it is the auditable trace of what mementic
   contributed.
2. **File gate.** Before opening ANY file, run
   `mementic query file <path> --compact` for every path. Locating files with
   grep/glob is fine; reading content without a query is not. This covers
   exploration reads too. No read-only exceptions. Pass file paths; `:line` and
   `:symbol` suffixes are accepted but discarded.
3. **Validate.** Do not write code that violates an active constraint. Cite
   every entity you apply or reject as `[mementic: <id>]`.
4. **Record inline.** When you make a decision or establish a rule, run
   `mementic add decision TITLE -r "..."` or
   `mementic add constraint DESC -e PATH` immediately — no gate. Retire
   superseded rules with `mementic deprecate ID... -r "reason"`. Before
   re-adding content, check `mementic history <id>` — tombstones carry
   reasons and successor chains.

The pack replaces reading ADRs, specs, and instruction files wholesale; it
does not replace reading the code you are about to edit. If mementic fails,
state the error, fall back to targeted reads (grep first), and report it.
Re-query at task boundaries and after graph writes: constraints change
between sessions, and an earlier pack may be stale.
<!-- mementic:end -->
