---
description: Capture architectural decisions made during this session for review
agent: build
---

Review the architectural changes made in this session. For each significant decision:

1. Call `propose_decision` with:
   - title: short, descriptive name
   - rationale: why this approach was chosen
   - rejected_alternatives: what was considered and rejected
   - affected_entities: list of files/modules changed

2. Call `propose_constraint` for any new rules that must be enforced going forward.

Only propose decisions that are architecturally significant (design pattern changes, new libraries, strategic choices). Skip routine refactors, syntax fixes, and variable renames.

After proposing, remind the user to run `mementic review` in the terminal to accept the proposals.
