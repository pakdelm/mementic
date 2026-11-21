---
description: Check or sync open spec PRs so they merge together (/integrate check | /integrate sync)
agent: orchestrator
---

Run the `integrator` subagent in `$1` mode (default `sync`) over every open pull request whose
head branch starts with `spec/` in this repository.

Build its brief yourself: list the open `spec/*` PRs with `tools.github.list_pull_requests`,
read each PR's base branch and its "Stacked on #..." line to derive stacks and merge order
(parents first, then spec number), and find each spec's worktree with
`tools.opencode.worktree_list`. Pass `MAIN` = `git rev-parse --show-toplevel`.

Do not ask me anything. Report the integrator's table and anything that needs a human.
