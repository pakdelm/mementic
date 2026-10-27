# Mementic

Declarative architectural memory for AI coding agents.

Mementic gives your AI coding agent the architectural rules it needs before it
starts working. Your project's decisions and constraints live in a local graph
you can query, so the agent reads what the project decided instead of
rediscovering it every session.

Without it, agents reread the same ADRs and instruction files before every task.
That burns tokens, and every agent repeats the same work.

What it does:

- Keeps decisions and constraints in a local SQLite file.
- Hands the agent a ranked list of the rules that apply before it starts.
- Never sends anything off your machine.

![mementic demo](scripts/demo/terminal.gif)

This is a beta. The core loop, `init`, `query` and `add`, works end to end and I
use it daily, but the surface is small and I have mostly tested it with one
agent. I would not run it in production yet.

## How mementic differs

Most tools that claim to remember your project watch what the agent did and
summarize it. That gives you a picture of past behavior, not a record of what the
project decided or why.

Mementic stores the decisions themselves. When an agent asks what rules apply to
a task, the answer comes from what the project wrote down.

A few things follow from that:

- Decisions carry a rationale; constraints carry a description and an optional
  `enforced_by` link to the decision that enforces them.
- Nothing is inferred. The only way in is `mementic add decision` or
  `mementic add constraint`.
- The graph is a SQLite file on your disk. No cloud, no API keys, no telemetry.

## Install

```bash
uv tool install git+https://github.com/pakdelm/mementic.git
```

That gives you a `mementic` binary. Upgrade later with `uv tool upgrade mementic`.
There is no PyPI package, and no configuration file to write.

## Quick start

```bash
cd ~/dev/my-project

# 1. Create an empty graph at .mementic/graph.db (idempotent)
mementic init

# 2. Your agent records decisions and constraints as it works:
#    mementic add decision "Use JWT Auth" --rationale "Token-based auth for APIs"
#    mementic add constraint "Services must be stateless" --entity src/services.py

# 3. Before a coding task, ask what rules apply here
mementic query keywords auth rate limit session

# 4. See what happened
mementic log
```

`init` also writes an enforcement section into `AGENTS.md`. It creates the file
if needed and replaces the section in place on every run, leaving the rest of the
file alone. The section tells your agent to query before working and to record
new rules through `mementic add`. In a Git work tree, it also adds `.mementic/`
to `.gitignore` so the graph is never committed.

Adding the same title twice is safe. If the content is near-identical (80% word
overlap), the second add is skipped. If it differs materially, the old entry is
deprecated and the new one takes its place.

Everything lives in `.mementic/`. No global config, no registration, no API keys.

## Working directory

The graph belongs to the directory where you ran `mementic init`. Run mementic
from that directory. A subdirectory or child repository has no graph until you
initialize one there. There is no environment variable to override the scope; the
current directory is the scope.

For a workspace with several repositories, initialize once at the workspace root,
start the agent there, and use paths relative to that root:

```sh
cd ~/dev/workspace
mementic init
mementic add constraint "Services must be stateless" --entity repo-a/src/services.py
mementic query file repo-a/src/services.py
```

`mementic query file --prefix repo-a/src` returns every rule bound under that
prefix. Matching starts at the beginning of the stored path, so it is not a
substring search and not a glob.

## Docker Sandboxes (sbx)

The v3 mixin in `docker/sbx-mementic/` installs the CLI into a workload. The kit
image is not published. Stage it from a checkout and run it from the workspace
root that should own `.mementic/`:

```sh
uv run python scripts/kit.py stage .sbx-kit
sbx run docker.io/docker/sbx-kit-shell:1.0.0 --kit .sbx-kit --name mementic
mementic init
```

See `docker/sbx-mementic/README.md` for the details.

Use direct mode, not `--clone`. The graph lives in the mounted workspace at
`.mementic/graph.db` and persists on the host. The mixin does not set a scope
variable or change the working directory, so run mementic from the directory that
owns `.mementic`. The workload has to provide `python3`. Docker's default agent
templates do; the `*-minimal` variants may not. On Windows the guest path shows up
under `/c/...`.

## Agent setup

`mementic init` syncs an enforcement section into `AGENTS.md`. Agents that read
`AGENTS.md` pick it up automatically. For anything else, copy the block from
`AGENTS.md` into that agent's instruction file.

The commands agents need:

| Action | Command |
|---|---|
| Load context before a task | `mementic query keywords kw1 kw2 ...` |
| Check what affects a file | `mementic query file path/to/file.py` |
| Record a decision | `mementic add decision TITLE --rationale "..."` |
| Record a constraint | `mementic add constraint DESC --entity PATH` |
| Deprecate outdated rules | `mementic deprecate ID1 ID2 ... --reason "reason"` |
| Search the graph | `mementic query search <term>` |
| Trace why an entry was deprecated | `mementic history <id>` |

`--entity` takes a file path and can be repeated. It is optional for a decision
and required for a constraint, because a constraint with no file anchor cannot be
found by `query file`. `--rationale` is required on `add decision`: it is the
justification text the context pack shows later. The short forms are `-e` and
`-r`, except on `deprecate`, where `-r` means `--reason`.

`query keywords` and `query file` both take `--json` for scripts and `--compact`
(`-c`) for plain text. Compact output is the `[mementic: ...]` echo line plus the
pack text with no panel around it, where the echo line carries shown and matched
counts plus the terms or paths that were queried.

### The enforcement instruction (summary)

This is a shortened copy of what `init` writes into your agent's instruction
file.

```markdown
## Mementic Memory (HARD CONSTRAINT)

This project keeps its architectural rules in a local graph (`.mementic/`).
Run every mementic command from the directory that contains `.mementic`. If
your CWD is a subdirectory, `cd` to that directory first. Entity paths are
relative to it: `src/auth.py` in a single repo project,
or `repo-a/src/auth.py` from a multi repo workspace root.

These steps apply to code work: reading, searching, editing, or making
architectural claims. Messages that trigger none of those (acknowledgements,
ops commands, meta discussion) need no query.

1. **Query.** Before the first file read, search, or code decision of a task,
   run `mementic query keywords --compact <5-12 narrow terms>`. Query again
   when the task shifts to a new module, domain, or file area, after recording
   an entry, and after context compaction. Echo the `[mementic: ...]` summary
   line verbatim; it is the auditable trace of what mementic contributed.
2. **File gate.** Before opening ANY file, run
   `mementic query file <path> --compact` for every path. Locating files with
   grep/glob is fine; reading content without a query is not. No exceptions,
   even for reads. Pass file paths; `:line` and `:symbol` suffixes are accepted
   but discarded.
3. **Validate.** Do not write code that violates an active constraint; cite
   every entity you apply or reject as `[mementic: <id>]`.
4. **Record inline.** Run `mementic add decision TITLE -r "..."` or
   `mementic add constraint DESC -e PATH` immediately, with no gate. Deprecate
   superseded rules with `mementic deprecate ID... -r "reason"`.

The pack replaces reading ADRs, specs, and instruction files wholesale, but
not the code you are about to edit. If mementic fails, state the error and
fall back to targeted reads. Query again at task boundaries and after graph
writes.
```

## Why I built this

I ran two real implementation tasks in OpenCode, same tasks, same quality bar
(tests, lint, typecheck), with and without mementic. The graph at the time held
330 decisions and 215 constraints.

Without mementic the agent kept rereading the same ADRs and instruction files,
and spawned subagents that each did the same discovery work. With mementic the
context pack answered "what rules apply here?" before the agent started, so it
went straight to the work.

A single module change:

| Metric | Without | With |
|---|---|---|
| Unique files read | 9 | 4 |
| Total file reads | 19 | 14 |
| LLM tokens | 106K | 59K |
| Subagents spawned | 1 | 0 |

A cross cutting feature spanning several modules:

| Metric | Without | With |
|---|---|---|
| Unique files read | 20 | 14 |
| Total file reads | 120 | 59 |
| LLM tokens | 324K | 149K |
| Subagents spawned | 7 | 2 |

Across both: 51% fewer tokens, 75% fewer subagents, 47% fewer file reads.

These numbers are a one-off anecdote, not a reproducible benchmark: Windows 11,
Python 3.13, October 2026, two tasks run in OpenCode against my graph (330
decisions, 215 constraints). Treat them as a signal rather than a promise. How
much you gain depends on how many architectural rules your project actually has
written down.

## Limitations

A few things worth knowing before you try it:

- I test with OpenCode. Other agents that read `AGENTS.md` should work, but they
  have not had the same use. If something breaks, open an issue and I will fix
  it.
- The graph starts empty. `init` creates it and scans nothing. It fills up as you
  or your agent record decisions while working.
- It is single user and local only. The graph lives on your machine, and there is
  no sync or team sharing yet.

If any of that sounds like fun to fix, contributions are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## CLI reference

| Command | What it does |
|---|---|
| `mementic --version` | Print the installed package version and exit |
| `mementic init` | Create `.mementic/graph.db` when missing (idempotent) and create or update the AGENTS.md enforcement section |
| `mementic query keywords <kw...> [--json] [-c/--compact]` | Get ranked context for a task |
| `mementic query file <path> [--prefix] [--json] [-c/--compact]` | Constraints and decisions for a file (exact match by default) |
| `mementic query search <term>` | Full text search of the graph |
| `mementic query stats` | Store statistics |
| `mementic add decision <title> --rationale TEXT [--entity PATH]` | Record a decision (duplicates resolved automatically) |
| `mementic add constraint <desc> --entity PATH` | Record a constraint (duplicates resolved automatically) |
| `mementic deprecate <id...> [--reason REASON]` | Deprecate rules |
| `mementic history [id]` | Deprecation records; with an ID, follow the replacement chain |
| `mementic status` | Health checklist |
| `mementic list [decisions\|constraints\|entities]` | List everything |
| `mementic log [-n N]` | Recent changes |

## Storage

Everything lives in one SQLite file at `.mementic/graph.db`, schema v5
(`PRAGMA user_version = 5`). Five flat tables, no ORM:

| Table | Holds |
|---|---|
| `decisions` | id, title, rationale, created_at |
| `constraints` | id, description, enforced_by (FK to decisions), created_at |
| `entry_paths` | One row per entry and file association |
| `deprecations` | Tombstones: reason, optional successor, timestamp |
| `audit_log` | Every change |

There is no status column. An entry is dead when a `deprecations` row for it is
newer than its `created_at`, which means a slug that is added again comes back to
life on its own. Liveness is computed at query time, so packs and keyword queries
skip tombstones without any cleanup scan.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: PRs are welcome,
tests, lint, formatting, and typecheck must be clean, and no CLA or DCO sign-off
is required.

## License

Apache 2.0. See [LICENSE](LICENSE).
