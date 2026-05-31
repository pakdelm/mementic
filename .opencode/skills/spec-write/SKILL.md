---
name: spec-write
description: Use when writing or updating specs in .opencode/specs/. Follows the project spec template and conventions.
---

# Spec Write

## Workflow

1. Read `.opencode/specs/00_template.md` for the standard format.
2. Read relevant existing specs for context and to avoid duplication.
3. Determine the next spec number (highest existing + 1).
4. Write the spec following the template:
   - **Status:** draft (always start as draft)
   - **Summary:** one paragraph, user-centric
   - **Motivation:** why this matters, what problem it solves
   - **Acceptance Criteria:** specific, testable, checkbox items
   - **Technical Notes:** affected modules, constraints, dependencies, implementation approach
5. Do not include full code listings or verbose Pydantic model definitions inline. Describe data models by their fields and purpose. Reserve code for illustrative CLI output examples and minimal pseudocode where structure is the point.
6. Reference related specs and ADRs by filename.

## Rules

- One spec per feature or sprint. Don't lump unrelated work together.
- Write specs for what to build and why — not how to type it.
- Acceptance criteria must be verifiable. "The system should be fast" is not a criterion. "API response under 200ms for 1000 concurrent requests" is.
- If a spec supersedes an earlier one, note it in the summary and link the old spec.
- Keep specs in the same directory as the template unless explicitly told to organize differently.
