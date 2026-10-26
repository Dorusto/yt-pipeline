---
name: yt-director-editor
description: Execute a validated director plan (director_plan.json) as the exact cut windows — the code editor of the film crew. It reads the plan, the shotlog and the labels, cuts each planned shot inside its usable window (peak-centred, landing floor kept, techniques like clean entry/exit, jerky approach and time compression), snaps every cut to the beat grid when one is given, and writes edit.json plus editor_report.json without changing, swapping, dropping or reordering the shots. No model call. Use when the user asks to cut a plan into an edit, run the code editor, turn director_plan.json into edit.json, or wants the editor report. Trigger phrases - "taie planul în edit", "rulează editorul de cod", "director_plan.json în edit.json", "editor_report.json", "cut the plan into an edit", "run the code editor", "execute the director plan". Not for writing the plan (`llm-director`), the rule-based cut (`director`), rendering (`render`) or analysing footage (`ingest`/`motion`/`quality`/`describe`).
compatibility: Run with the `yt` entry point on PATH. Needs the repo .venv; CPU only, no GPU, no network and no model call. Reads director_plan.json, shotlog.json, ingest.json, quality/<stem>.json and motion/<stem>.json from the workspace read-only and writes edit.json and editor_report.json atomically. Deterministic: the same plan and inputs always produce the same edit.
---

# yt-director-editor

The code editor of the film crew: it takes the director's plan and executes it. It never changes the shots — the cut has exactly the plan's shots in the plan's order — and what it cannot do goes in `editor_report.json`. No model is called. All commands run through `yt <tool>`.

## Step 1: cut a plan

```bash
yt director_editor --workspace /path/to/workspace --plan director_plan.json --json
yt director_editor --workspace /path/to/workspace --plan director_plan.json --beats beats.json --music track.mp3
```

- `--workspace` is required and must hold `shotlog.json`, `ingest.json`, `quality/` and `motion/`; `--plan` is the plan JSON, validated against `schemas/director_plan.schema.json` before anything is cut.
- `--out` (default `WORKSPACE/edit.json`) is the edit list; `--report` (default `WORKSPACE/editor_report.json`) is the report.
- `--beats FILE` (optional) is the beat grid JSON from `scripts/beats.py --out`; without it the editor uses `WORKSPACE/beats.json` when that file exists. With a grid every timeline cut snaps to a beat (preferring a downbeat, then a half-bar beat, then the nearest beat, within the slot's cap) and every non-first slot joins with a hard cut; a cut that cannot move adds a `cut k: not snapped to a beat` line to the report's `could_not_do`.
- `--music FILE` and `--ambient-under-music-db DB` are recorded in `edit.json` `audio.music` and `audio.ambient_under_music_db`, so the render's mix is reproducible from the edit alone.
- `--speed-ramp` (default off) lets the middle half of a long camera move play up to 1.2x so more of the move fits; without it every slot plays at normal speed (`middle_speed` 1.0).
- `--json` prints the machine-readable summary. `--help` lists every flag.

## Step 2: read the result

`edit.json` validates against `schemas/edit.schema.json` and `editor_report.json` against `schemas/editor_report.schema.json` before they are written, so a run either leaves complete, valid files or none.

The `--json` summary is a flat object: `ok`, `workspace`, `plan`, `edit`, `report`, `slots`, `total_length_s`, `target_length_s`, `could_not_do` (count), `similar_neighbours` (count), `wall_time_s` and `llm_cost_usd` (always `0.0` — this tool never calls a model). Without `--json` it prints one line: `N slots, X.Xs -> /path/edit.json`.

The report carries one row per plan shot (`id`, `shot_key`, `in_s`, `out_s`, `length_s`, `technique_asked`, `technique_done`, `why_not`, and `pieces[]` when the shot was cut in several pieces), the `could_not_do` list, the `similar_neighbours` pairs and a one-sentence `summary`.

Exit codes: `0` success, `2` usage or input error (bad flags, missing workspace or plan, a plan that does not validate, an unknown shot key), `3` runtime failure after work started (a write failure or an invalid record).

## How the editor cuts

- **The plan is the authority.** Every plan shot becomes one slot (or several pieces for `jerky_approach`/`time_compression`), in plan order, with the plan's `id`, `shot_key`, `why` as the slot's `reason` and the shotlog's score, role, motion label and description. A photo shot becomes a `kind: "photo"` slot with the plan's `target_s` clamped to 2–4 s and a deterministic Ken Burns move.
- **The window.** Each shot is cut inside its usable window from the shot facts (the hold's bounds, the move's handle window, a phrase's move-in through the landing hold). A cut shorter than the window is centred on the plan's `moment.at_s` when given, else the shot's peak, else the window middle, and never ends before the landing floor. A calm hold may run longer than 5 s inside its window; every other shot is clamped to 2–5 s.
- **Techniques.** `opening_shot` caps the cut at 3 s; `clean_entry` starts 0.5 s before the subject enters; `clean_exit` ends 0.3 s after the subject leaves; `jerky_approach` cuts 3–4 short pieces spread over the window and keeps the entry; `time_compression` cuts 2–5 equal slices. A technique the window cannot support falls back to a plain cut and is named in `could_not_do`.
- **The neighbour check.** Consecutive video slots are compared on the frames inside their `in_s..out_s` with the same embedding `similarity.py` uses (cached in `<workspace>/similarity_windows.json`); a pair at or over the threshold is listed in the report's `similar_neighbours`. The editor reports it, it never drops a shot for it.
- **Re-run safety.** The editor always rewrites `edit.json` and `editor_report.json` from the plan; run it again with the same plan and inputs and it produces the same files. The plan is never modified.

## Notes

- The editor is the only tool that turns a plan into cut windows; the director (`llm-director`) writes the plan and reviews the cut, the rule-based `director` builds an edit without a plan, and `render` turns `edit.json` into an MP4.
- Outputs go to the workspace, never into the footage folder.
