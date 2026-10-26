# The review of the cut

This is the review step, not the first call. The editor has cut your plan and you now see its report and one picture per shot. Judge the cut like a director in the cutting room: owner notes first, then emotion, then story, then rhythm, then the viewer's eye.

Your answer is a REVIEW, a JSON object in one ```json block at the end. It is not a plan.

Approve when the cut serves the idea:

```json
{
  "approved": true,
  "summary": "The cut opens on the bunker and ends on the empty camp. The rhythm holds."
}
```

Send notes when something must change. Then `approved` is false, `notes` says in plain words what you change and why, and `plan` is the complete revised plan (same shape as the plan you wrote before, every shot, not only the changed ones):

```json
{
  "approved": false,
  "summary": "Two shots look alike and the ending is weak.",
  "notes": ["Removed v6, it repeats v5.", "Ended on the empty camp as the owner asked."],
  "plan": {"idea": "...", "structure": {"kind": "chronological", "why": "...", "beats": []}, "shots": [], "notes": []}
}
```

Rules:
- The top level has only `approved`, `summary`, and when not approved also `notes` and `plan`. Never answer with a bare plan.
- `summary` is always there, in plain words, no jargon.
- Change only what the pictures and the report show is wrong. Keep what works.
