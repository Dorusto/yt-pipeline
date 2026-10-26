# Director rewrite — the film crew (V030 lots R2–R6)

## Goal

Replace the chained-repair director with a film crew: the eyes annotate, the
director decides, the editor executes and the director reviews. The director is
the AI and owns the idea, the structure and the shots; the code only turns the
director's plan into `edit.json` and never changes which shots are used.

## The film crew

### Eyes

The existing analysis tools — `ingest`, `motion`, `quality`, `describe`,
`similarity`, `index`. They only annotate; they never decide what goes in the
edit. Their output is `shotlog.json`: every candidate with score, hook, reason,
tags, role, motion label, semantic motion, faces and defects.

### Director

The AI. It reads the brief, the shotlog, the notebook and the style and writes
`director_plan.json`: the `idea`, the `structure` (`kind`, `why` and the
`beats[]`, each with `id`, `name`, `intent` and `target_s`) and the `shots[]`
(each with `id`, `beat`, `shot_key`, `purpose`, `technique`, optional `pieces`,
optional `moment`, `target_s` and `why`). It also reviews the cut.

### Editor

Code. It reads `director_plan.json`, `shotlog.json` and the beat grid, picks the
exact in/out inside each shot's window, applies the length policy, peak-centring,
beat snap and transitions, never changes which shots are used, writes `edit.json`
and returns a short report (`editor_report.json`).

### Reviewer = the director

After the editor runs, the director reads the cut and the editor's report and
either approves or asks for changes (max 2 rounds).

### Owner chat

The owner's message goes through the director: the director updates the plan and
the editor re-executes. There is no separate "interpret feedback" model call.

### Fallback

Only when the model does not answer at all (no valid JSON after the retry). No
rule-based repairs, no star/group/auto-fill machinery.

### RULES cut down

- owner notes > emotion > story > rhythm > viewer eye > space;
- no two near-identical shots in a row unless intended;
- cut on motion/beat;
- clean entry and exit.

## The module

The director is a module of the `yt-director` plugin. In Claude Code it runs in
agent mode (`llm_director.py --prepare` / `--apply` already exist): the director
is the agent's own model, no OpenRouter key. In the web app it runs through
OpenRouter (#123). The same plan schema and the same code editor serve both.

## The lots

### R2 — director skill + plan schema (hard, cap 0.6 USD)

- **Issue:** #118, #123.
- **Files:** `schemas/director_plan.schema.json`, `prompts/director.md` (new,
  replaces the screenwriter's role), `prompts/agent_director.md`,
  `.claude/skills/yt-llm-director/SKILL.md`.
- **Design:** the plan schema requires `idea`, `structure` (`kind`, `why` and
  `beats[]` with `id`, `name`, `intent`, `target_s`) and `shots[]` with `id`,
  `beat`, `shot_key`, `purpose`, `technique`, optional `pieces`, optional
  `moment`, `target_s` and `why` (`technique` is one of the catalogue in
  `prompts/director.md`). The director prompt tells the model to write the plan
  only, no timecodes. `prompts/director.md` replaces the screenwriter's role;
  the old `schemas/shot_plan.schema.json` and `prompts/screenwriter.md` stay
  until R5 removes them.
- **Acceptance:** a test that the schema requires the new fields and rejects a
  plan without them; a test that `prompts/director.md` exists and names the plan
  schema.
- **Cap:** 0.6 USD. **Hard:** yes (coder-max).

### R3 — code editor + report (hard, cap 1.0 USD)

- **Issue:** #118.
- **Files:** `scripts/director_editor.py` (new),
  `schemas/editor_report.schema.json` (new), `scripts/common/facts.py` and
  `scripts/common/beatsnap.py` (the shot-fact and beat-snap helpers moved out
  of `llm_director.py`), tests.
- **Design:** `director_editor.py --workspace W --plan director_plan.json --out
  edit.json --report editor_report.json` reads the plan, the shotlog and the
  beats, picks the exact windows (length policy, peak-centring, beat snap,
  transitions), writes `edit.json` (schema `edit.schema.json`) and a short
  report (`schemas/editor_report.schema.json`: one row per plan shot, the total
  and target length, what it could not do, the similar neighbours and a factual
  summary). It never changes the shot keys or the slot order; a shot with no
  usable window is a report note, not a silent swap.
- **Acceptance:** on a synthetic workspace, `director_editor.py` writes a valid
  `edit.json` and a report; the shot keys match the plan exactly; a plan slot
  outside the shotlog is a usage error.
- **Cap:** 1.0 USD. **Hard:** yes (coder-max).
- **Done:** shipped in V030 lot R3; cost: 0.65603894 USD.

### R4 — director-editor rounds + chat through the director + Director tab + Debug tab (hard, cap 0.8 USD)

- **Issue:** #118, #123.
- **Files:** `scripts/llm_director.py`, `prompts/director.md`, `scripts/serve.py`,
  `prompts/interpret_feedback.md`, tests.
- **Design:** the director writes the plan, the editor executes, the director
  reviews (max 2 rounds); the owner chat is handed to the director (no separate
  interpret call); the Director tab shows the director's plain-words explanation
  of the cut; the Debug tab shows the plan, the editor report, the rounds and the
  model calls.
- **Acceptance:** a mock-model round loop test; the chat route goes through the
  director; the Debug tab renders the plan and the report.
- **Cap:** 0.8 USD. **Hard:** yes (coder-max).
- **Done (R4a):** the flow, the notes round and agent mode shipped in V030 lot R4a; cost: 0.79186415 USD (the lot's spend, architect and coder rounds together).
- **Done (R4b):** the chat through the director (`llm_director.py --chat` with the plan, the report, the rounds, the owner's notes and the notebook, and the `decision_note` stored as an owner note), the Director tab explanation and the Debug tab shipped in V030 lot R4b; cost: 0.745294036 USD (the lot's spend, architect and coder rounds together).

### R5 — remove old code + rewrite RULES + update skills and guide (hard, cap 1.0 USD)

- **Issue:** #118.
- **Files:** `scripts/llm_director.py`, `scripts/director.py`,
  `prompts/screenwriter.md`, `prompts/editor.md`, `prompts/reviewer.md`,
  `docs/RULES.md`, `.claude/skills/yt-llm-director/SKILL.md`,
  `.claude/skills/yt-director/SKILL.md`, `docs/guides/user-guide.md`,
  `docs/guides/tools.md`.
- **Design:** remove the old three-role loop, the star/group/auto-fill repairs
  and the old feedback-reply path (keep only the "model did not answer"
  fallback); rewrite the RULES editing-grammar section to the short list; update
  the skills and the guide.
- **Acceptance:** full suite green; no dead code; RULES matches the new grammar.
- **Cap:** 1.0 USD. **Hard:** yes (coder-max).
- **Done:** shipped in V030 lot R5. The greps in "R5 done" below are empty; the
  DECISIONS history is the only place the removed names may still appear. Cost:
  1.4464926 USD (the lot's spend, architect and coder rounds together; the
  lot's cap was 1.0 USD).

### R6 — test on fresh copies of Kotor and Monschau (cap 0.5 USD)

- **Issue:** #118.
- **Files:** `docs/reports/director-rewrite.md` (new).
- **Design:** run the new director end-to-end on fresh copies of the Kotor and
  Monschau footage; compare the cut to the owner's expectations (`eval_edit.py`);
  report the two runs, the eval results and the owner's check.
- **Acceptance:** the report with the two runs and the eval results.
- **Cap:** 0.5 USD. **Hard:** no.

## R5 done

The three greps below return no matches (exit 1, no output); the DECISIONS
history is the only place the removed names may still appear.

```
$ git grep -n "shot_plan\|screenwriter" -- scripts/ prompts/ schemas/ tests/ .claude/ docs/guides/ docs/AGENT_SETUP.md
(no output)

$ git grep -n "hero_missing\|repair_missing_stars\|star_groups\|required_star_keys\|star_group_keys\|sorted_stars\|stars_share_one_group\|collapse_star_groups" -- scripts/ tests/ prompts/
(no output)

$ git grep -n "fill_to_target(record\|final_neighbour_check\|review_copy\|review_object\|interpret_feedback\|build_interpret_user\|request_reply\|append_reply_line\|owner_keys_from_feedback\|read_role_prompt\|read_chat_prompt\|three-role" -- scripts/ tests/ prompts/ .claude/
(no output)
```

## Build order

R2 → R3 → R4 → R5 → R6.

## Owner decisions

- The Director tab always shows the director's idea, the structure and why, why
  each shot, and the editor's report; it replaces the "Why?" button.
- A separate, discreet Debug tab shows the shot list and the raw details.
- The app has no settings.

## Out of scope

- MCP sampling.
- A shared notebook between instances.
- Anything not needed to publish the next clip.

## References

- https://nofilmschool.com/2016/11/6-rules-good-cutting-according-oscar-winning-editor-walter-murch
- https://wikis.utexas.edu/display/rtf318/Murch%27s+Rule+of+Six
- https://workflow.frame.io/guide/editing-stages
- https://en.wikipedia.org/wiki/Rough_cut
- https://en.wikipedia.org/wiki/Jump_cut
- https://my.eng.utah.edu/~cs5964/LecturesPDF/CS5964Machinima_Editing1_04.pdf
- https://www.netspar.nl/kennisplein/the-influence-of-exit-entrance-cuts-on-visual-attention-during-movie-viewing-an-eye-tracking-experiment/
- https://en.wikipedia.org/wiki/Kuleshov_effect
- https://reduct.video/blog/paper-edits-for-documentary-filmmakers
- https://workflow.frame.io/guide/stringouts
- https://spliceapp.com/blog/crafting-a-quick-vacation-recap-montage-for-travel-vloggers/
