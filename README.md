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
- **The graph is curated, not inferred.** Extracted findings are proposed and
  explicitly accepted or deprecated, so stale behavior from abandoned
  experiments does not linger as "memory."
- **Local and inspectable.** The graph is a SQLite file on your machine. No
  cloud processing, no behavioral telemetry.

## Install

```bash
uv tool install git+https://github.com/pakdelm/mementic.git
```

That's it. One command. You now have a `mementic` binary. Upgrade with
`uv tool upgrade mementic`.

### Pick an LLM provider

Mementic uses an LLM to extract decisions from sessions and docs. It supports
OpenRouter (default), OpenAI, DeepSeek, and Ollama (local, no key needed).
Without an API key, extraction falls back to deterministic local parsing —
no LLM calls, no network.

Easiest way:

```bash
mementic init           # prompts you for provider, model, API key
```

The key goes to your OS keychain, never to a file. For CI or headless:

```bash
export OPENROUTER_API_KEY="sk-..."
mementic init --provider openrouter
```

Supported providers and their env vars:

| Provider | Env var | Example model |
|---|---|---|
| OpenRouter | `OPENROUTER_API_KEY` | `deepseek/deepseek-v4-pro` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o` |
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-v4-pro` |
| Ollama | none (local) | `llama3` |

## Quick start

```bash
cd ~/dev/my-project

# 1. Scan the project and build the graph
mementic init --provider openrouter

# 2. See what's in there
mementic status
mementic query search auth

# 3. Before a coding task, ask "what rules apply here?"
mementic query keywords auth rate limit session

# 4. After a session, capture what changed
mementic capture

# 5. See what happened
mementic log
```

`mementic init` scans all markdown files in the repo — ADRs, agent instructions,
docs — and detects ADRs by their format. Everything lives in `.mementic/` — no
global config, no registration.

`mementic capture` reads your latest agent session log plus the git diff,
extracts architectural decisions, and auto-resolves them against the existing
graph. New things get accepted, duplicates get skipped, conflicts get handled.
It works without git too (just warns and uses session-only extraction).

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
| Capture session | `mementic capture` (you run this, not the agent) |

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
3. Run `mementic query file <path>` for each file being modified.
4. Validate your plan against every returned constraint.

**After architecturally significant changes:**
1. `mementic add decision TITLE --rationale "..."` for new decisions.
2. `mementic add constraint DESC -e PATH` for new rules.
3. `mementic deprecate ID1 ID2 ...` to retire superseded rules.

**After the task:** Run `mementic capture` in your terminal.
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

- **Tested on one setup.** The code supports OpenCode, Claude Code, Cursor, and
  Windsurf — and OpenRouter, OpenAI, DeepSeek, and Ollama. But I've only actually
  tested it with **OpenCode + DeepSeek-v4**. The other combinations _should_ work
  (there are providers and session readers for all of them) but they haven't been
  through the same daily use. If you hit issues with another agent or model, open
  an issue — I'll fix it.
- **Edge cases exist.** It handles the happy paths well. Weird session formats,
  corrupted graphs, and unusual repo structures may break.
- **Single user, local-only.** The graph lives on your machine. It is not built
  for team sharing and has no sync feature.
- **No PyPI package yet.** It installs directly from the GitHub repo. It works,
  but it's not polished.
- **LLM extraction costs money.** Every `mementic capture` makes an API call
  (unless you use Ollama). With current models it's cheap, but it's not zero.
- **Agent setup is not fully automated.** `mementic init` handles most of it, but
  not every edge case across all four supported agents.

If any of this sounds like fun to fix, [contributions are welcome](CONTRIBUTING.md).

## CLI reference

| Command | What it does |
|---|---|
| `mementic init [--provider X] [--model X] [--key] [--deep] [--no-scan]` | Scan project, build the graph (deterministic by default; `--deep` adds full LLM extraction) |
| `mementic capture [--agent X] [--session PATH] [--tail N] [--keyword X]` | Extract decisions from a session (`--tail` limits session messages, `--keyword` adds scan keywords) |
| `mementic query keywords <kw...> [--json]` | Get ranked context for a task |
| `mementic query file <path> [--json]` | Constraints/decisions for a file |
| `mementic query search <term>` | Full-text search the graph |
| `mementic query stats` | Graph statistics |
| `mementic add decision <title> --rationale TEXT` | Record a decision |
| `mementic add constraint <desc> -e PATH` | Record a constraint |
| `mementic deprecate <id...> [-r REASON]` | Retire rules |
| `mementic status` | Health checklist |
| `mementic list [decisions\|constraints\|entities]` | List everything |
| `mementic log [-n N]` | Recent mutations |

## Config file reference

`mementic init` creates a local `.mementic/` directory with the graph database
(`graph.db`) and a minimal `config.json`:

```json
{
  "llm": {
    "provider": "openrouter",
    "model": "deepseek/deepseek-v4-pro"
  }
}
```

All configuration lives in `.mementic/config.json` alongside `graph.db`. API
keys are never stored here — they live in your OS keyring (or an environment
variable for CI).

### Sessions and log directories

By default, each supported agent uses its own built-in platform paths. Adding
a `sessions` key is optional — only needed when your session logs live in
non-standard locations. The `log_dirs` entries are **additive**: they extend
the defaults, not replace them.

```json
{
  "sessions": {
    "opencode": {
      "log_dirs": ["/mnt/nfs-shared/sessions"]
    }
  }
}
```

With this config, mementic searches the standard OpenCode log directory plus
`/mnt/nfs-shared/sessions`. All other agents (Claude Code, Cursor, Windsurf,
generic) continue using their built-in defaults unchanged.

## Project status & roadmap

Active development, used daily by the maintainer. The core loop — init, query,
capture, enforce — works end to end. Next milestones:

- **First PyPI release.** Install is currently from the GitHub repo only.
- **Broader testing.** Most verified on OpenCode + DeepSeek; other agents and
  providers have support but less battle testing (details in "What's missing").
- **Polish.** Good-first-issue-sized work: docs, error messages, examples.

Contributions in any of those areas are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: PRs welcome, DCO
sign-off required (`git commit -s`), tests must pass, lint and typecheck must
be clean.

## License

Apache 2.0. See [LICENSE](LICENSE).
