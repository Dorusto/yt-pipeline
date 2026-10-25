# Agent director — prepare and apply

You are the host coding agent. The `llm_director.py` tool prepared this file
with everything you need to direct one edit: the brief, the brief settings, the
editing rules, the shortlisted shots and their shot facts.

Write the plan first to `<workspace>/shot_plan.json` (schema
`schemas/shot_plan.schema.json`), then the editor answer to
`<workspace>/edit.json` in the editor answer shape. Then run:

    yt llm_director --workspace <workspace> --brief "..." --apply

`--apply` validates both files against the schemas and the editing grammar,
repairs mechanical faults with the same machinery the OpenRouter loop uses and
writes the final `edit.json` with `meta.director.by = "agent"`. It makes no
model call.
