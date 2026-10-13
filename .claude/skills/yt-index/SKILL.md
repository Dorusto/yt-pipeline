---
name: yt-index
description: Join a workspace's quality, motion, describe and similarity analyses into one shotlog.json — every quality segment as a scored candidate with score, hook, reason, a one-line summary and tags, the effective role and motion label (the vision verdict wins on uncertain segments), the near-duplicate links for the director's diversity rule and an eligibility flag. Use when the user wants the analysed workspaces scored and ranked, asks which shots are worth using, wants the vision and label scores fused, or needs the file the director ranks by. Trigger phrases - "fă shotlog-ul", "indexează workspace-ul", "scorează cadrele", "care cadre merită", "rank the shots", "build the shotlog". Not for choosing the final edit (that is `director`), for the raw analyses (`motion`, `quality`, `describe`, `similarity`) or for generating clips.
compatibility: Run from the repository root. No dependencies beyond the standard library and the repo .venv; CPU only, no model call and no spend. Reads quality/<stem>.json (required), motion/<stem>.json, describe/<stem>.json and similarity.json (all optional) read-only, writes shotlog.json into the workspace. Safe to re-run; an up-to-date output is skipped unless --force.
---

# yt-index

Join the per-clip analyses of a workspace into **one `WORKSPACE/shotlog.json`**: every quality segment becomes a scored candidate the director ranks. The score fuses a vision content component (0.55) and a quality/motion label component (0.45). On an **uncertain** segment the `describe` verdict supplies the effective `role` and `motion_label` (`role_source` / `label_source: "verdict"`), otherwise the geometry labels are kept. Every shot carries `score + hook + reason`, the `summary` and `tags` from describe (null / empty when there is no vision) and the `similar_to` links the director uses for diversity. No model is called. All commands run from the repository root.

## Step 1: build the shotlog

```bash
.venv/bin/python scripts/index.py --workspace /path/to/workspace
```

- `--workspace` is required and must hold `quality/`. `DIR/shotlog.json` is written there, created if missing. The source analyses and clips are never written to.
- `--weight-vision` (default 0.55) and `--weight-labels` (default 0.45) are the fusion weights; `audio_energy` and `text` stay 0.0 until the D2/D4 tools exist.
- `--clips` limits the run to comma-separated 4-digit counters or stem substrings (e.g. `--clips 0072,0081`). Without it every `quality/*.json` is processed.
- `--force` recomputes even when `shotlog.json` is newer than every input it was built from. Without it an up-to-date output is skipped and counted as `skipped`.
- On any error (missing `quality/`, unreadable quality file, write failure, schema mismatch) it prints one `[error] ...` line to stderr and exits 1. It never asks questions.

## Step 2: read the result

```bash
.venv/bin/python scripts/index.py --workspace /path/to/workspace --json
```

The `--json` output is a single object, e.g. `{"shots": 39, "eligible": 32, "with_vision": 18, "suppressed": 0, "weights": {"vision": 0.55, "labels": 0.45, "audio_energy": 0.0, "text": 0.0}, "wall_time_s": 0.05, "llm_cost_usd": 0.0}`. Read `DIR/shotlog.json` for the full records; it is validated against `schemas/shotlog.schema.json` and written atomically, so a run leaves a complete valid file or none. `llm_cost_usd` is always `0.0` — this tool calls no model service.

## What the fields mean

- `shots` has one entry per quality segment, in clip order then time order. `key` is `<clip counter>/<segment id>` (e.g. `0081/s7`), the same format as `similarity.json`.
- `role`, `motion_label` and `confidence` are the **effective** labels: the vision verdict when the segment is `described` and has a non-null `verdict`, otherwise the geometry values. `role_source` / `label_source` say which won (`verdict` or `geometry`). `uncertain` and `uncertain_reasons` are echoed from quality.
- `score` is the fused number the director ranks by; `scores.vision` and `scores.labels` are the components (`scores.vision` is null when there is no vision data, but the fusion still uses a neutral `0.5` prior for that shot). Not-eligible shots are zeroed. `labels = clamp(ROLE_BASE + 0.25*confidence + steadiness bonus + clean/defect term, 0, 1)`; `vision = min(1, interest/100 + 0.15 if hero)`, or `0.5` when there is no vision; `score = (0.55*vision + 0.45*labels) / (0.55 + 0.45)`.
- `eligible` is false for a confident `accidental` / `setup` / `teardown` (effective role) and for a suppressed shot; everything else is eligible, including an uncertain accidental/setup/teardown and any eligible shot with no vision data (labels only). The director picks only eligible shots.
- `summary` and `tags` come straight from the describe answer (`summary` is at most 12 words, `tags` at most six lowercase tags); they are `null` / `[]` when the shot has no vision and are what `shotlog.html` shows.
- `hook` names the draw: the vision `subject`, else the first 8 words of the `description`, else `"<role> · <motion_label>"`. `reason` is the plain-words audit trail, e.g. `"interest 60 · travel_backward by verdict · move · obstruction"` or `"no description · static by geometry · accidental · clean · not eligible"`.
- `similar_to` lists the other keys of every `similarity.json` pair containing this shot, most similar first. `defects` carries the measured defect names from quality.
- `suppressed_by` is set when two shots in the same clip overlap by more than 50% of the shorter one: the lower-scored shot is suppressed by the higher-scored one and becomes ineligible. It does not fire on the current tiling; it keeps the VISION overlap rule alive.

## Notes

- This is the join stage of D1b: it consumes the outputs of `ingest`, `motion`, `quality`, `describe` and `similarity` and produces the file the director ranks by.
- It never cuts, trims or deletes anything; it only scores and links what the other tools measured.
- Audio and text score components are reserved in the schema at zero weight until D2/D4.
