# Agent director — prepare and apply

You are the host coding agent. The `llm_director.py` tool prepared this file
with everything you need to direct one edit: the director prompt, the payload
(the brief, the shot facts, the owner's notes and the notebook) and, when a
review is asked, the editor report and the pictures of the cut.

Write the plan to `<workspace>/director_plan.json` (schema
`schemas/director_plan.schema.json`), then run:

    yt llm_director --workspace <workspace> --brief "..." --apply

`--apply` validates the plan, runs the code editor and writes `edit.json` and
`editor_report.json` with `meta.director.by = "agent"`. It makes no model call.

For the notes round, run `--prepare --review`: it writes the pictures of the
cut and the editor report here. Look at the pictures, then write
`<workspace>/director_review.json` (schema
`schemas/director_review.schema.json`) and run `--apply` again. At most 2
rounds; after the second the cut is delivered with your notes.
