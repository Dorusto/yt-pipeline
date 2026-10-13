---
name: yt-quality
description: Measure footage quality per segment and label each segment with a role — move, hold, transition_in, transition_out, setup, teardown, accidental — with a confidence, plus the measured defects (blur, exposure, shake, obstruction, horizon). Use when the user asks how good the footage is, which shots are blurry, shaky, over- or under-exposed, obstructed or tilted, or wants setup/teardown and transition candidates marked. Trigger phrases - "verifică calitatea footage-ului", "care cadre sunt mișcate sau blurate", "quality check", "etichetează setup și teardown", "unde e blurat sau tremurat". Labels only — it never deletes, cuts or trims anything. Not for camera-motion vocabulary (that is `motion`) or semantic content (that is `describe`).
compatibility: Run from the repository root. Needs the repo .venv with opencv-python-headless and numpy; CPU only, no GPU and no network. Reads clips read-only and writes quality/<stem>.json into the workspace. Prefers a matching motion/<stem>.json in the workspace for segmentation and shake; without it, it falls back to coarse still/moving segments and warns.
---

# yt-quality

Measure the quality of a clip and label every segment with a role before any editing decision. Sampled frames are measured for blur, exposure, obstruction and frame difference; a matching `motion/<stem>.json` drives segmentation and carries the camera label and shake. Each segment gets a role with a confidence and the measured defects. **Labels only — nothing is ever deleted, cut or trimmed here.** All commands run from the repository root.

## Step 1: analyse one clip or a folder

```bash
.venv/bin/python scripts/quality.py /path/to/clip.mp4 --workspace /path/to/workspace
```

- `INPUT` is one video file, or a folder scanned non-recursively for `.mp4`, `.mov` and `.m4v` files (any letter case, sorted by name).
- `--workspace` is required. `DIR/quality/<stem>.json` is written per clip, created if missing. The source folder is never written to.
- `--motion-dir` (default `DIR/motion`) is where the matching `motion/<stem>.json` is looked up. When it is present it drives segmentation and carries the camera label and shake; when it is absent the tool still works on its own coarse still/moving segmentation and adds the warning `no motion file; segments are coarse`.
- `--sample-fps` (default 2) sets how many frames per second are decoded for the measurements; the rest are skipped. `--width` (default 320) scales frames down before measuring.
- `--force` re-analyses a clip whose output already exists. Without it, a clip whose JSON exists and validates against the `quality` schema is skipped and counted as `skipped`.
- On any error (missing input, unreadable video, write failure) it prints one `[error] ...` line to stderr and exits 1. It never asks questions.

## Step 2: read the result

```bash
.venv/bin/python scripts/quality.py /path/to/clip.mp4 --workspace /path/to/workspace --json
```

The `--json` output is a single object, e.g. `{"clips": 21, "analyzed": 21, "skipped": 0, "output_dir": "/abs/workspace/quality"}`. Read `DIR/quality/<stem>.json` for the per-clip detail; it is validated against `schemas/quality.schema.json` and written atomically, so a run leaves a complete valid file or none.

## What the fields mean

- `segments` tile the clip exactly: the first starts at `0.0`, the last ends at `duration_s`, each starts where the previous ended, and every segment is kept. `end_s` is always greater than `start_s`: parts shorter than `MIN_RUN_SECONDS` (0.5 s) are folded into a neighbour, so no zero-length sliver ever reaches the output. Nothing is dropped.
- `role` is one of `move`, `hold`, `transition_in`, `transition_out`, `setup`, `teardown`, `accidental`, with a `confidence` in 0–1 and short `role_evidence` strings. `setup` / `teardown` / `accidental` are *likely* roles, never verdicts.
- `setup` and `teardown` only appear outside the content of the take, never in a fixed head/tail time window. A part is content when it overlaps a motion move, carries a deliberate camera label (not `null`, not `static`, not `minor_motion`) or is a `still` phase at least `HOLD_MIN_SECONDS` long; `unclassified motion` is not content. `setup` can only precede the first content part, `teardown` can only follow the last one. When the whole clip has no content part (a junk take) the clip midpoint decides: parts starting before it are `head`, the rest `tail`.
- A segment whose motion label is `static` never gets role `move`. It is a `hold` when the still/hold rule applies, otherwise a `hold` with confidence `0.4` and evidence `no significant camera motion`.
- `motion_label` is the CameraBench label of the overlapping move from the motion file, or the segment's own dominant motion; `null` without a motion file.
- `defects` lists only the measured defects that cross their threshold, each with a normalised `value` (0–1, higher is worse) and a human-readable `detail`: `blur` (Laplacian variance below half the clip's own median), `exposure` (clipped highlights, crushed shadows or mean luma outside 40–200), `shake` (trajectory residual), `obstruction` (large, flat, static area), `horizon` (roll offset on a hold or static shot).
- `metrics` always carries the raw numbers behind the defects (`laplacian_rel`, `clip_high_pct`, `clip_low_pct`, `mean_luma`, `shake`, `obstruction`, `horizon_deg`), even when no defect is reported.
- Intent and defect stay separate: blur or shake on a fast `move` is still reported as a defect, but the segment also gets the evidence string `motion blur during a fast move (intent)` / `shake during a fast move (intent)`. The director decides.

## Notes

- Label, never delete: the tool only reports what it measured. It never cuts, trims or ranks.
- This is the third stage of the CPU-only D1a analysis (after `ingest` and `motion`); output feeds the director and the review UI.
