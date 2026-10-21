# Mementic

Mementic gives your AI coding agent the architectural rules it needs before it
starts working. It is declarative architectural memory — the decisions and
constraints your project made, stored as an explicit local graph — not a
behavioral trace of past sessions.

Without Mementic, agents re-read the same ADRs and instruction files before every
task. That wastes tokens, and every agent does the same work separately. Mementic
fixes this:

- Store your project's decisions and constraints in a local SQLite graph.
- Before a task, the agent queries the graph and gets a ranked list of the rules
  that apply.
- Everything stays local.

It works with OpenCode, Claude Code, Cursor, and Windsurf.

This is a beta: the core loop — init, query, inline add — works end to end and
is used daily by the maintainer, but the surface is small and tested mainly
with one agent. Not recommended for production use. I build it with AI agents
and improve it through feedback cycles.

## How mementic differs

Most AI coding tools that remember your project use **trace-derived behavioral
memory**: they observe what the agent did in past sessions and replay or
summarize that behavior. The result is a statistical picture of how the agent
behaved, not a record of what the project decided and why.

mementic stores **declarative architectural memory**: the decisions your
project made and the rules that follow from them, as explicit, queryable
entries in a graph. When an agent asks "what rules apply to this task?", the
answer comes from what the project decided — not from what a past session
happened to do.

That difference has practical consequences:

- **Every entry has a rationale.** You can ask why a rule exists, not just
  whether it was seen before.
- **Nothing is inferred.** Mementic performs no LLM extraction. The only write
  path is `mementic add decision|constraint` — the agent (or human) states the
  decision and its rationale directly.
- **Local and inspectable.** The graph is a SQLite file on your machine. No
  cloud processing, no API keys, no telemetry.

## Install

```bash
uv tool install git+https://github.com/pakdelm/mementic.git
```

That's it. One command. You now have a `mementic` binary. Upgrade with
`uv tool upgrade mementic`. No LLM provider, API key, or config file needed —
mementic is zero-config by design.

## Quick start

```bash
cd ~/dev/my-project
# 1. Create an empty graph at .mementic/graph.db (idempotent)
mementic init

# 2. Your agent records decisions and constraints inline as it works:
#    mementic add decision "Use JWT Auth" --rationale "Token-based auth for APIs"
#    mementic add constraint "Services must be stateless" -e src/services.py

# 3. Before a coding task, ask "what rules apply here?"
mementic query keywords auth rate limit session

# 4. See what happened
mementic log
```

`init` also syncs an enforcement section into `AGENTS.md` — created when
missing, replaced in place on every re-run — so your agent knows to query
before working and to record new rules inline via `mementic add`. Everything
outside that section is left untouched. Duplicate adds are skipped
automatically; changed content under the same title deprecates the old entry
and accepts the new one.

Everything lives in `.mementic/` — no global config, no registration,
no API keys.

## Working directory

The graph is local to the directory where you run `mementic init`. Run mementic
from that directory; a subdirectory or child repository reports no graph until
it is initialized there. There is no `MEMENTIC_WORKSPACE_ROOT` or
`MEMENTIC_ROOT` override — the CWD is the scope.

For a multi-repo workspace, initialize once at the workspace root, start the
agent there, and use workspace-relative paths:

```sh
cd ~/dev/workspace
mementic init
mementic add constraint "Services must be stateless" -e repo-a/src/services.py
mementic query file repo-a/src/services.py
```

`mementic query file --prefix repo-a/src` returns every rule bound under that
prefix. `--prefix` is start-anchored, not a substring or glob query.

## Docker Sandboxes (sbx)

The v3 mixin in `docker/sbx-mementic/` installs the CLI into a workload. A
published image follows the next release; until then, stage the kit from the
checkout and run it from the workspace root that should own `.mementic/`:

```sh
uv run python scripts/kit.py stage .sbx-kit
sbx run docker.io/docker/sbx-kit-shell:1.0.0 --kit .sbx-kit --name mementic
mementic init
```

See `docker/sbx-mementic/README.md` for details.

Use direct mode, not `--clone`. The graph lives in the mounted workspace at
`.mementic/graph.db` and persists on the host. The mixin does not set a scope
environment variable or change the working directory: run mementic from the
directory that owns `.mementic`. The workload must provide `python3`; Docker's
default agent templates do, the `*-minimal` variants may not. On Windows, the
guest path appears under `/c/...`.

## Agent setup

`mementic init` syncs an enforcement section into `AGENTS.md`; agents that
read `AGENTS.md` — OpenCode and Claude Code — pick the instructions up
automatically. For Cursor and Windsurf, copy the enforcement block from
`AGENTS.md` into your agent's own instruction file.

Agents use these CLI commands:

| Action | Command |
|---|---|
| Load context before a task | `mementic query keywords kw1 kw2 ...` |
| Check what affects a file | `mementic query file path/to/file.py` |
| Record a decision | `mementic add decision TITLE --rationale "..."` |
| Record a constraint | `mementic add constraint DESC -e PATH` |
| Retire outdated rules | `mementic deprecate ID1 ID2 ... -r "reason"` |
| Search the graph | `mementic query search <term>` |
| Trace why an entry was retired | `mementic history <id>` |

`query keywords` and `query file` support `--json` for programmatic use and
`--compact` for undecorated, agent-friendly output: the `[mementic: ...]` echo
line plus the plain pack text, no Rich panel. The echo line is compact —
`shown/matched` ratios where relevant, with `|`-separated terms or paths.

### The enforcement instruction (summary)

This is a shortened summary of what gets injected into your agent's instruction
file. Your agent follows these rules to keep the graph current:

```markdown
## Mementic Memory (HARD CONSTRAINT)

This project keeps its architectural rules in a local graph (`.mementic/`).
Run every mementic command from the directory that contains `.mementic`; if
your CWD is a subdirectory, `cd` to that directory first. Entity paths are
relative to it: `src/auth.py` in a single-repo project,
or `repo-a/src/auth.py` from a multi-repo workspace root.

These steps apply to code work — reading, searching, editing, or making
architectural claims. Messages that trigger none of those (acknowledgements,
ops commands, meta discussion) need no query.

1. **Query.** Before the first file read, search, or code decision of a task,
   run `mementic query keywords --compact <5-12 narrow terms>`. Re-query when
   the task shifts to a new module, domain, or file area, after recording an
   entry, and after context compaction. Echo the `[mementic: ...]` summary
   line verbatim — it is the auditable trace of what mementic contributed.
2. **File gate.** Before opening ANY file, run
   `mementic query file <path> --compact` for every path. Locating files with
   grep/glob is fine; reading content without a query is not. No read-only
   exceptions. Pass file paths; `:line` and `:symbol` suffixes are accepted but
   discarded.
3. **Validate.** Do not write code that violates an active constraint; cite
   every entity you apply or reject as `[mementic: <id>]`.
4. **Record inline.** Run `mementic add decision TITLE -r "..."` or
   `mementic add constraint DESC -e PATH` immediately — no gate. Retire
   superseded rules with `mementic deprecate ID... -r "reason"`.

The pack replaces reading ADRs, specs, and instruction files wholesale, but
not the code you are about to edit. If mementic fails, state the error and
fall back to targeted reads. Re-query at task boundaries and after graph
writes.
```

## Why I built this

I ran two real implementation tasks in OpenCode — same tasks, same quality bar
(tests + lint + typecheck), with and without mementic. The graph at the time had
330 decisions, 215 constraints.
Without mementic, the agent kept re-reading the same ADRs and instruction files,
spawning subagents that each did the same discovery work independently. With
mementic, the context pack answered "what rules apply here?" before the agent
started — so it went straight to implementation.

A single-module change:

| Metric | Without | With |
|---|---|---|
| Unique files read | 9 | 4 |
| Total file reads | 19 | 14 |
| LLM tokens | 106K | 59K |
| Subagents spawned | 1 | 0 |

A cross-cutting feature spanning multiple modules:

| Metric | Without | With |
|---|---|---|
| Unique files read | 20 | 14 |
| Total file reads | 120 | 59 |
| LLM tokens | 324K | 149K |
| Subagents spawned | 7 | 2 |

Across both: **51% fewer tokens, 75% fewer subagents, 47% fewer file reads.**

This is on _my_ codebase with _my_ graph, using _my_ setup. Your mileage will
vary depending on how dense your project's architectural rules are. But the
pattern held — the agent spent way less time doing discovery and more time
doing the actual work.

## Limitations

A few things to know before trying it:

- **Tested daily with OpenCode.** Other agents that read `AGENTS.md` should
  work, but they haven't been through the same use. If you hit issues, open an
  issue — I'll fix it.
- **Cold start is empty.** `init` creates an empty graph and scans nothing.
  The graph fills up as you (or your agent) record decisions and constraints
  inline while working.
- **Single user, local-only.** The graph lives on your machine. It is not built
  for team sharing and has no sync feature.

If any of this sounds like fun to fix, [contributions are welcome](CONTRIBUTING.md).

## CLI reference

| Command | What it does |
|---|---|
| `mementic init` | Create `.mementic/graph.db` when missing (idempotent) + create or update the AGENTS.md enforcement section |
| `mementic query keywords <kw...> [--json] [--compact]` | Get ranked context for a task |
| `mementic query file <path> [--prefix] [--json] [--compact]` | Constraints/decisions for a file (exact match by default) |
| `mementic query search <term>` | Full-text search the graph |
| `mementic query stats` | Store statistics |
| `mementic add decision <title> -r TEXT [-e PATH]` | Record a decision (auto-resolves duplicates) |
| `mementic add constraint <desc> -e PATH` | Record a constraint (auto-resolves duplicates) |
| `mementic deprecate <id...> [-r REASON]` | Retire rules |
| `mementic history [id]` | Deprecation records; with an ID, follow the replacement chain |
| `mementic status` | Health checklist |
| `mementic list [decisions\|constraints\|entities]` | List everything |
| `mementic log [-n N]` | Recent mutations |

## Storage

Everything lives in one local SQLite file at `.mementic/graph.db`
(schema **v5**, `PRAGMA user_version = 5`). Five flat tables, no ORM:

| Table | Holds |
|---|---|
| `decisions` | id, title, rationale, created_at |
| `constraints` | id, description, enforced_by (FK to decisions), created_at |
| `entry_paths` | One row per entry/file association |
| `deprecations` | Tombstones: reason, optional successor (`replaced_by`), timestamp |
| `audit_log` | Every mutation |

There is no status column. An entry is *dead* only when a `deprecations` row
for it exists with a timestamp newer than the entry's `created_at` (so a
re-added slug derives as live again) — liveness is derived at query time, so
context packs and keyword queries always exclude tombstones without any
cleanup scans.

## Project status & roadmap

Active development, used daily by the maintainer. Next milestones:

- **Publish the sbx kit image** (`docker.io/pakdelm/sbx-kit-mementic`).
- **Team sharing** via an always-on mutation log.

Contributions in any of those areas are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: PRs welcome, DCO
sign-off required (`git commit -s`), CLA on your first contribution
([CLA.md](CLA.md)), tests must pass, lint and typecheck must be clean.

## License

Apache 2.0. See [LICENSE](LICENSE).
