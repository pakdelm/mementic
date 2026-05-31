---
description: Load architectural context before starting work
---

Before working on this task: $ARGUMENTS

Run the following steps:
1. Identify which files will be affected by this task
2. Call the `get_context_pack` MCP tool with the task description and affected files,
   passing `benchmark=True` to accumulate token savings data
3. Read the returned constraints and decisions carefully
4. Validate your plan against every constraint — do NOT write code that violates them
5. If a constraint seems inapplicable, explain why before proceeding
6. Proceed with the task, respecting all applicable constraints
