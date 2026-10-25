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
`shot_plan.json`: the idea (logline), the structure (beats, each with a reason)
and the shots (each with a purpose and a technique). It also reviews the cut.

### Editor

Code. It reads `shot_plan.json`, `shotlog.json` and the beat grid, picks the
exact in/out inside each shot's window, applies the length policy, peak-centring,
beat snap and transitions, never changes which shots are used, writes `edit.json`
and returns a short report.

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
- **Files:** `schemas/shot_plan.schema.json`, `prompts/director.md` (new,
  replaces `prompts/screenwriter.md`), `prompts/agent_director.md`,
  `.claude/skills/yt-llm-director/SKILL.md`.
- **Design:** the plan schema requires `logline`, `structure[]` with `id`,
  `name`, `intent`, `reason`, `target_s`, and `slot_plan[]` with `id`, `beat`,
  `shot_key`, `target_s`, `purpose`, `technique` (`technique` is a short phrase
  from the playbook, e.g. "cut on the move", "hold the still", "match cut"). The
  director prompt tells the model to write the plan only, no timecodes.
- **Acceptance:** a test that the schema requires the new fields and rejects a
  plan without them; a test that `prompts/director.md` exists and names the plan
  schema.
- **Cap:** 0.6 USD. **Hard:** yes (coder-max).

### R3 — code editor + report (cap 0.6 USD)

- **Issue:** #118.
- **Files:** `scripts/editor.py` (new), `schemas/editor_report.schema.json`
  (new), tests.
- **Design:** `editor.py --workspace W --plan shot_plan.json --out edit.json
  --report editor_report.json` reads the plan, the shotlog and the beats, picks
  the exact windows (length policy, peak-centring, beat snap, transitions),
  writes `edit.json` (schema `edit.schema.json`) and a short report
  (`schema_version`, `slots`, `clamped[]`, `warnings[]`, `wall_time_s`,
  `llm_cost_usd: 0`). It never changes the shot keys or the slot order; a shot
  with no usable window is a report warning, not a silent swap.
- **Acceptance:** on a synthetic workspace, `editor.py` writes a valid
  `edit.json` and a report; the shot keys match the plan exactly; a plan slot
  outside the shotlog is a usage error.
- **Cap:** 0.6 USD. **Hard:** no.

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

### R5 — remove old code + rewrite RULES + update skills and guide + squash the c30 "wip" commit (cap 0.5 USD)

- **Issue:** #118.
- **Files:** `scripts/llm_director.py`, `scripts/director.py`,
  `prompts/screenwriter.md`, `prompts/editor.md`, `prompts/reviewer.md`,
  `docs/RULES.md`, `.claude/skills/yt-llm-director/SKILL.md`,
  `.claude/skills/yt-director/SKILL.md`, `docs/guides/user-guide.md`,
  `docs/guides/tools.md`.
- **Design:** remove the old three-role loop, the star/group/auto-fill repairs
  and the rule-based fallback (keep only the "model did not answer" fallback);
  rewrite the RULES editing-grammar section to the short list; update the skills
  and the guide; squash the c30 "wip" commit (`556bc05`) into proper history
  before the PR.
- **Acceptance:** full suite green; no dead code; RULES matches the new grammar;
  the squash leaves a clean history.
- **Cap:** 0.5 USD. **Hard:** no.

### R6 — test on fresh copies of Kotor and Monschau (cap 0.5 USD)

- **Issue:** #118.
- **Files:** `docs/reports/director-rewrite.md` (new).
- **Design:** run the new director end-to-end on fresh copies of the Kotor and
  Monschau footage; compare the cut to the owner's expectations (`eval_edit.py`);
  report the two runs, the eval results and the owner's check.
- **Acceptance:** the report with the two runs and the eval results.
- **Cap:** 0.5 USD. **Hard:** no.

## Build order

R2 → R3 → R4 → R5 → R6.

## Questions for the owner

1. **What does the Director tab show by default?**
   - (A) the director's plain-words explanation of the cut only;
   - (B) the explanation plus the plan (logline, beats, shots);
   - (C) the explanation plus a collapsed plan.
   Recommended: (C) — the explanation is what the owner reads, the plan is one
   click away when something looks wrong.

2. **What is the Debug tab called and who sees it?**
   - (A) "Debug", visible to everyone;
   - (B) "Debug", hidden behind a flag or a query parameter;
   - (C) a different name, e.g. "Details" or "Under the hood".
   Recommended: (B) — the plan, the report and the model calls are for the
   owner and the architect, not for a first-time visitor.

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
