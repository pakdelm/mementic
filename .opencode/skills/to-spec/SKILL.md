---
name: to-spec
description: Turn the current conversation into a spec under .opencode/specs/ without interviewing, using the project template, mementic decisions, and agreed test seams.
disable-model-invocation: true
---

# To Spec

Adapted from Matt Pocock's `to-spec` skill. This repository tracks work as local
spec files under `.opencode/specs/`, not as GitHub issues. The spec file is the
tracker.

Synthesize what is already known. Do **not** interview the user. If a decision is
still genuinely open, list it under **Open questions** rather than inventing an
answer.

## Process

1. **Ground yourself.** Run
   `uv run mementic query keywords --compact <5-12 narrow terms>` and echo the
   `[mementic: ...]` line. Before reading a file, run
   `uv run mementic query file <path> --compact`. Read the relevant existing
   specs and the project template at `.opencode/specs/00_template.md`.

2. **Extract the settled design.** From the conversation and the codebase, write
   down:
   - the problem from the user's perspective
   - the solution from the user's perspective
   - the actors and flows worth stating as user stories
   - the implementation decisions already made
   - the test seams and the tests that will prove each acceptance criterion
   - what is explicitly out of scope
   - anything still open

3. **Choose the number and slug.** Take the highest `NN_` prefix in
   `.opencode/specs/`, add one, and use a lowercase hyphenated slug:
   `.opencode/specs/<NN>_<slug>.md`.

4. **Choose the seams before the template.** A seam is the public boundary where
   behavior is observed: a CLI command, a public function, an API endpoint, or a
   storage boundary. Prefer existing seams over new ones, and use the highest seam
   that proves the behavior. The ideal number of new seams is one. For each
   acceptance criterion, name the seam and the test. If a seam is not agreed, put
   it under **Open questions** instead of assuming it.

5. **Write the spec.** Follow the existing spec style:

   ```markdown
   # Spec <NN> — <Title>

   **Status:** draft

   ## Summary

   The solution, in one user-centric paragraph.

   ## Motivation

   The problem, why it matters now, and the actors involved.

   ## Acceptance Criteria

   - [ ] <verifiable behavior> — `tests/...::test_name`
   - [ ] <verifiable behavior> — `tests/...::test_name`

   ## Technical Notes

   Affected modules, interfaces, data or schema changes, constraints, test seams,
   and implementation decisions. Reference related specs and mementic IDs.
   ```

   Add any of these only when they add information the sections above do not
   already carry: `## User Stories`, `## Testing Decisions`, `## Out of Scope`,
   `## Further Notes`. Do not add empty headings.

6. **Keep it decision-rich, not code-rich.** Name modules and interfaces, not
   exact file paths or long code snippets. The exception is a prototype snippet
   that encodes a decision more precisely than prose (a state machine, reducer,
   schema, or type shape); inline only the decision-bearing part and say it came
   from a prototype.

7. **Record new decisions.** If the conversation settled a durable decision that
   is not yet in mementic, record it with
   `uv run mementic add decision "<title>" -r "<rationale>"` and cite the ID in
   the spec. Do not duplicate an existing decision; query first.

8. **Publish locally.** Write the spec file. Do not open a GitHub issue and do not
   post the spec to an external tracker. Report the path and the next step:
   `/to-tickets <NN>` for a ticket graph, or `/ship <NN>` for autonomous delivery.

## Rules

- One spec per feature or coherent change. Do not bundle unrelated work.
- Every acceptance criterion must be observable and testable.
- Reference related specs by filename and mementic entries by ID.
- If this spec supersedes an earlier one, say so in the Summary and link the old
  spec.
- Keep the spec in `.opencode/specs/` unless the user explicitly asks otherwise.
