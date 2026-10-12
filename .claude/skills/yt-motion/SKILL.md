---
name: yt-motion
description: Analyse the camera motion of a clip with OpenCV optical flow on the CPU — a sampled trajectory, a split of the take into motion phases (still, ramp, steady move, ramp, still) and CameraBench-labelled moves (pan, tilt, roll, dolly/zoom, complex) with speed, steadiness, steady range and cut handles. Use when the user asks what the camera did in a clip, where the stable moves are, or wants moves and handles to cut on. Trigger phrases - "analizează mișcarea camerei", "ce mișcări sunt în clip", "camera motion", "găsește mișcările stabile", "unde începe și se termină o panoramă". Not for image quality defects (that is `quality`), semantic moves such as orbit or tracking (that is `describe`) or generating clips.
compatibility: Run from the repository root. Needs the repo .venv with opencv-python-headless; CPU only, no GPU and no network. Reads clips read-only and writes motion/<stem>.json into the workspace. About 1.5x real time or faster per clip at the default settings.
---

# yt-motion

Measure how the camera moved in a clip before any editing decision. A Lucas-Kanade optical-flow pass with a partial-affine RANSAC fit per sampled frame pair builds a camera trajectory; the take is split into motion phases and each steady span becomes a labelled move with a steady range and cut handles. Labels use the CameraBench geometric vocabulary; the meaning of the motion (orbit, tracking, reveal) is left to `describe`. All commands run from the repository root.

## Step 1: analyse one clip or a folder

```bash
.venv/bin/python scripts/motion.py /path/to/clip.mp4 --workspace /path/to/workspace
```

- `INPUT` is one video file, or a folder scanned non-recursively for `.mp4`, `.mov` and `.m4v` files (any letter case, sorted by name).
- `--workspace` is required. `DIR/motion/<stem>.json` is written per clip, created if missing. The source folder is never written to.
- `--sample-fps` (default 10) sets the analysis sampling rate; frames between samples are skipped. `--width` (default 320) scales frames down before the optical-flow pass.
- `--force` re-analyses a clip whose output already exists. Without it, a clip whose JSON exists and validates against the `motion` schema is skipped and counted as `skipped`.
- On any error (missing input, unreadable video, write failure) it prints one `[error] ...` line to stderr and exits 1. It never asks questions.

## Step 2: read the result

```bash
.venv/bin/python scripts/motion.py /path/to/clip.mp4 --workspace /path/to/workspace --json
```

The `--json` output is a single object, e.g. `{"clips": 21, "analyzed": 21, "skipped": 0, "output_dir": "/abs/workspace/motion"}`. Read `DIR/motion/<stem>.json` for the per-clip detail; it is validated against `schemas/motion.schema.json` and written atomically, so a run leaves a complete valid file or none.

## What the fields mean

- `analysis.method` is `lucas_kanade_affine_partial_ransac` and `analysis.source` is `optical_flow`; gyro is a later upgrade. `sample_fps` is the effective rate (the source fps divided by the integer frame step), `width` the analysis width and `frames_analyzed` the number of sampled frames.
- `trajectory` has one row per sampled frame pair: midpoint time `t`, image translation `dx`/`dy` as fractions of frame width/height, `scale` (1.0 = no change), `roll_deg` (positive = the image rotates clockwise on screen), `inlier_ratio`, plus the smoothed `speed` and the high-frequency `shake` residual.
- `phases` tile the clip exactly. Moving stretches are split where the dominant component and its sign change (with hysteresis, so a short blip does not split a run), then each sub-run gets the `ramp_up` / `steady_move` / `ramp_down` treatment; a run shorter than 1 s, with no steady middle, or shaky is a single `irregular` phase. Each phase carries a `dominant_motion`, `speed` and `steadiness` word.
- `moves` are the usable spans: only `steady_move` phases of at least 1.5 s produce one. Each move has a `start_s`/`end_s` that include the ramps, a `steady_start_s`/`steady_end_s` for the clean middle, `handle_in_s`/`handle_out_s` recommended cut points (never mid-acceleration), a CameraBench `label`, `speed`, `steadiness`, a `confidence` in 0–1 and the steady window means.
- Labels name what the camera did, inverting the image motion. A component only competes while its rate is real: pan/tilt/roll have per-second significance floors, and a scale change must also be sign-consistent to count as a dolly (so parallax or stabilisation drift is not a dolly). Pan/tilt/roll labels are unambiguous; `dolly_in`/`dolly_out` come with `semantic_check: true` because 2D flow cannot separate a dolly from a zoom — the vision step decides. A window with two comparable significant components is `complex_motion` (`semantic_check: true`) and still yields a move.
- Speed is `slow` (< 0.15), `regular` (≤ 0.6) or `fast`; steadiness is `static`, `no_shaking`, `minimal_shaking`, `unsteady` or `very_unsteady`.

## Notes

- Label, never delete: the tool only reports what the camera did. Intent (a deliberate whip pan) and defect (shake on a hold) are separate fields handled later.
- This is the second stage of the CPU-only D1a analysis (after `ingest`, before `quality`); output feeds `scripts/quality.py` and later `describe`/`index`.
