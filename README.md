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

This is a personal project, not a product. I use it daily, but it is not
recommended for production use. This project is my experiment in agentic
development. I build it with AI agents and improve it through feedback cycles.

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

`init` also injects enforcement instructions into `AGENTS.md` so your agent
knows to query before working and to record new rules inline via
`mementic add`. Duplicate adds are skipped automatically; changed content
under the same title deprecates the old entry and accepts the new one.

Everything lives in `.mementic/` — no global config, no registration,
no API keys.

## Agent setup

`mementic init` detects your agent and injects enforcement instructions into
`AGENTS.md`. Agents that read `AGENTS.md` — OpenCode and Claude Code — pick
the instructions up automatically. For Cursor and Windsurf, copy the
enforcement block from `AGENTS.md` into your agent's own instruction file.

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

`query keywords` and `query file` support `--json` for programmatic use.

### The enforcement instruction (summary)

This is a shortened summary of what gets injected into your agent's instruction
file. Your agent follows these rules to keep the graph current:

```markdown
## Mementic Memory (HARD CONSTRAINT)

This project uses Mementic as its architectural memory layer.

**After EVERY user message:**
1. Generate 5-12 narrow keywords for the task (e.g., "argon2 token login
   session" — skip generic terms like "security" or "password").
2. Run `mementic query keywords <kw1> <kw2> ...`
3. List the code files you intend to open, then run
   `mementic query file <path>` for each of them BEFORE reading any file —
   exploration reads included, not just files you plan to modify.
4. Validate your plan against every returned constraint.

**When you make a decision or establish a rule:**
1. `mementic add decision TITLE --rationale "..."` immediately — no gate.
2. `mementic add constraint DESC -e PATH` immediately.
3. `mementic deprecate ID1 ID2 ...` to retire superseded rules.
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

## What's missing (honest answer)

This is not production software. It's a personal tool that I open-sourced because
it might be useful to someone else. Here's what you should know before trying it:

- **Tested on one setup.** I've only actually tested this daily with
  **OpenCode**. Other agents that read `AGENTS.md` should work, but they
  haven't been through the same use. If you hit issues, open an issue — I'll
  fix it.
- **Cold start is empty.** `init` creates an empty graph and scans nothing.
  The graph fills up as you (or your agent) record decisions and constraints
  inline while working.
- **Edge cases exist.** It handles the happy paths well. Weird repo structures
  or corrupted graphs may break.
- **Single user, local-only.** The graph lives on your machine. It is not built
  for team sharing and has no sync feature.
- **No PyPI package yet.** It installs directly from the GitHub repo. It works,
  but it's not polished.

If any of this sounds like fun to fix, [contributions are welcome](CONTRIBUTING.md).

## CLI reference

| Command | What it does |
|---|---|
| `mementic init` | Create `.mementic/graph.db` when missing (idempotent) + inject AGENTS.md enforcement |
| `mementic query keywords <kw...> [--json]` | Get ranked context for a task |
| `mementic query file <path> [--prefix] [--json]` | Constraints/decisions for a file (exact match by default) |
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

**Upgrading from schema v4 (memento 0.3.0 or earlier)?** Old-schema databases
are not auto-upgraded. Start fresh: move the old `.mementic/graph.db` aside
(or delete it) and run `mementic init`. If you need data out of a v4
database, check out the v0.3.0 tag, which shipped the one-shot conversion
script.

## Project status & roadmap

Active development, used daily by the maintainer. The core loop — init,
query, inline add — works end to end. Next milestones:

- **First PyPI release.** Install is currently from the GitHub repo only.
- **Pack quality.** File-overlap signals via `entry_paths` for better
  context-pack precision.
- **Polish.** Good-first-issue-sized work: docs, error messages, examples.

Contributions in any of those areas are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: PRs welcome, DCO
sign-off required (`git commit -s`), tests must pass, lint and typecheck must
be clean.

## License

Apache 2.0. See [LICENSE](LICENSE).
