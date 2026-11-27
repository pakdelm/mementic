---
name: grill-with-docs
description: Relentlessly interview the user to sharpen a plan or design, recording settled decisions in mementic and a spec instead of ADR or glossary files.
disable-model-invocation: true
---

# Grill With Docs

Adapted from Matt Pocock's `grill-with-docs`, `grilling`, and `domain-modeling`
skills. This repository has no `GLOSSARY.md` and no `docs/adr/`: mementic is the
domain memory, and `.opencode/specs/` holds the written plan. "Docs" here means
durable mementic entries and, once the design is settled, a spec.

## When to use

Use this skill when the user wants to stress-test an idea, plan, or design before
committing to a spec or code. Do not use it for factual questions or for work that
is already specified.

## Process

1. **Ground yourself.** Before the first read or search, run
   `uv run mementic query keywords --compact <5-12 narrow terms>` and echo the
   `[mementic: ...]` line. Before opening any file, run
   `uv run mementic query file <path> --compact`. Cite every constraint you apply
   or reject as `[mementic: <id>]`.

2. **Build a design tree.** Every decision is a node; its answer unlocks the
   decisions that hang off it. The **frontier** is every decision whose
   prerequisites are settled: the questions you can ask now without guessing at
   answers you have not heard.

3. **Ask one frontier per round.** Number each question, include the options, and
   give your recommended answer. Word each question so that "yes" accepts the
   recommendation. Then wait for the user's answers before the next round. A
   question whose answer depends on another open question belongs to a later
   round, not this one.

4. **Facts are your job.** When a frontier question needs evidence from the
   filesystem, git history, or an external source, dispatch an `explore` subagent
   with a fresh context window. Do not ask the user for a fact you can look up.
   Do not block the rest of the round on it: only the questions downstream of that
   fact wait for the subagent.

5. **Sharpen the language.** When the user uses a term that conflicts with an
   existing decision or constraint, call it out immediately. When a term is vague
   or overloaded, propose a precise canonical term. Check the code when the user
   states how something works; surface any contradiction.

6. **Record as you go.** When a branch is settled, record the durable decision
   immediately:
   ```
   uv run mementic add decision "<specific title>" -r "<context, decision, why, alternatives rejected>"
   ```
   Record only decisions that are hard to reverse, surprising without context, or
   the result of a real trade-off. Skip tactical choices. If a decision supersedes
   an earlier one, retire the old one with
   `uv run mementic deprecate <id> -r "<reason>"` and cite both IDs.
   Add a constraint only when there is a concrete, enforceable rule and a file or
   directory it binds:
   ```
   uv run mementic add constraint "<rule>" -e <path>
   ```
   Do not create `GLOSSARY.md` or `docs/adr/`. If the user asks for an ADR, record
   a mementic decision instead.

7. **Write the spec only at the end.** Do not act on the design or write code
   until the user confirms a shared understanding. If the user wants the result
   written down, load the `to-spec` skill or follow `spec-write` to create
   `.opencode/specs/<NN>_<slug>.md`. Link the recorded decision IDs from the spec.

## Round format

```
❓ **Q1** - **<question title>**: <question body, including the options>

➡️ <your recommended answer and one line of reasoning>

---

❓ **Q2** - **<question title>**: <question body>

➡️ <your recommended answer and one line of reasoning>
```

## Stop condition

The session is done when the frontier is empty: every branch of the design tree
has been visited and nothing is left silently assumed. Report the decisions
recorded, the IDs, and any open questions. Do not start implementing until the
user explicitly confirms.
