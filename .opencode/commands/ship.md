---
description: Deliver one or more specs autonomously, each as its own pull request (e.g. /ship 45 46 47)
agent: orchestrator
---

Deliver these specs from `.opencode/specs/`: $ARGUMENTS

Run fully autonomously. Each spec gets its own worktree, its own `spec/<NN>-<slug>` branch,
and its own pull request (against `main`, or against its parent spec's branch when stacked).
Plan every spec first, prove the PRs merge together with the integrator, and do not ask me
anything after launch. Report the PR table with the merge order when every spec has finished.
