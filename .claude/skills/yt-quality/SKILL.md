---
name: yt-quality
description: Measure footage quality per segment and label each segment with a role — move, hold, transition_in, transition_out, setup, teardown, accidental — with a confidence, plus the measured defects (blur, exposure, shake, obstruction, horizon). Use when the user asks how good the footage is, which shots are blurry, shaky, over- or under-exposed, obstructed or tilted, or wants setup/teardown and transition candidates marked, or wants a junk take flagged as accidental. Trigger phrases - "verifică calitatea footage-ului", "care cadre sunt mișcate sau blurate", "quality check", "etichetează setup și teardown", "unde e blurat sau tremurat", "clipul ăsta e de aruncat". Labels only — it never deletes, cuts or trims anything. Not for camera-motion vocabulary (that is `motion`) or semantic content (that is `describe`).
compatibility: Run from the repository root. Needs the repo .venv with opencv-python-headless and numpy; CPU only, no GPU and no network. Reads clips read-only and writes quality/<stem>.json into the workspace. Prefers a matching motion/<stem>.json in the workspace for segmentation and shake; without it, it falls back to coarse still/moving segments and warns.
---

# yt-quality

Measure the quality of a clip and label every segment with a role before any editing decision. Sampled frames are measured for blur, exposure, obstruction, frame difference and horizon; a matching `motion/<stem>.json` drives segmentation and carries the camera label and shake. Each segment gets a role with a confidence and the measured defects. **Labels only — nothing is ever deleted, cut or trimmed here.** All commands run from the repository root.

## Step 1: analyse one clip or a folder

```bash
.venv/bin/python scripts/quality.py /path/to/clip.mp4 --workspace /path/to/workspace
```

- `INPUT` is one video file, or a folder scanned non-recursively for `.mp4`, `.mov` and `.m4v` files (any letter case, sorted by name).
- `--workspace` is required. `DIR/quality/<stem>.json` is written per clip, created if missing. The source folder is never written to.
- `--motion-dir` (default `DIR/motion`) is where the matching `motion/<stem>.json` is looked up. When it is present it drives segmentation and carries the camera label and shake; when it is absent the tool still works on its own coarse still/moving segmentation and adds the warning `no motion file; segments are coarse`.
- `--sample-fps` (default 2) sets how many frames per second are decoded for the measurements; the rest are skipped. `--width` (default 320) scales frames down before measuring.
- `--force` re-analyses a clip whose output already exists. Without it, a clip whose JSON exists and validates against the `quality` schema is skipped and counted as `skipped`.
- On any error it prints one `[error] ...` line to stderr and exits `2` (bad flags, missing input, unreadable video) or `3` (runtime failure after work started: disk write failed). It never asks questions.

## Step 2: read the result

```bash
.venv/bin/python scripts/quality.py /path/to/clip.mp4 --workspace /path/to/workspace --json
```

The `--json` output is a single object, e.g. `{"clips": 21, "analyzed": 21, "skipped": 0, "output_dir": "/abs/workspace/quality"}`. Read `DIR/quality/<stem>.json` for the per-clip detail; it is validated against `schemas/quality.schema.json` and written atomically, so a run leaves a complete valid file or none.

## What the fields mean

- `segments` tile the clip exactly: the first starts at `0.0`, the last ends at `duration_s`, each starts where the previous ended, and every segment is kept. `end_s` is always greater than `start_s`: parts shorter than `MIN_RUN_SECONDS` (0.5 s) are folded into a neighbour, so no zero-length sliver ever reaches the output. Nothing is dropped.
- `role` is one of `move`, `hold`, `transition_in`, `transition_out`, `setup`, `teardown`, `accidental`, with a `confidence` in 0–1 and short `role_evidence` strings. `setup` / `teardown` / `accidental` are *likely* roles, never verdicts.
- `accidental` fires per segment on obstruction, a near-uniform frame or a static low-detail frame. On top of that a **run rule** upgrades whole chaotic stretches: a maximal run of consecutive segments that are *unstable* (raw `shake` ≥ 0.1 or steadiness `unsteady`/`very_unsteady`) and *not stable content* (a `move`/`hold` of at least 2 s with `shake` < 0.1, and for a `move` an overlapping move whose steadiness is `no_shaking`/`minimal_shaking`) becomes `accidental` with confidence `0.6` and evidence `chaotic run of <run>s with no stable move or hold (shake <mean>)` when the run lasts at least 5 s or 70 % of the clip. When those runs together cover at least 70 % of the clip, the whole clip becomes one `accidental` verdict with confidence `0.65` and evidence `junk take: <covered>s of <duration>s has no stable move or hold` — this is what turns a junk take like clip 0051 into a single verdict instead of a pile of plausible `move`s. By default the run rule covers junk frames only: a long shaky run keeps its move or hold role and reaches the vision model and the director with its shake defect, and `YT_PIPELINE_SHAKY=drop` restores the old upgrade to `accidental`. The rule only upgrades roles to `accidental`; it never changes a role back and never resurrects a segment.
- `setup` and `teardown` only appear outside the content of the take, never in a fixed head/tail time window. A part is content when it overlaps a motion move, carries a deliberate camera label (not `null`, not `static`, not `minor_motion`) or is a `still` phase at least `HOLD_MIN_SECONDS` long; `unclassified motion` is not content. `setup` can only precede the first content part, `teardown` can only follow the last one. When the whole clip has no content part (a junk take) the clip midpoint decides: parts starting before it are `head`, the rest `tail`.
- A segment whose motion label is `static` never gets role `move`. It is a `hold` when the still/hold rule applies, otherwise a `hold` with confidence `0.4` and evidence `no significant camera motion`.
- `motion_label` is the CameraBench label of the overlapping move from the motion file, or the segment's own dominant motion; `null` without a motion file.
- `motion_label_confidence` is the `confidence` of the motion move or phase that supplied `motion_label` (0–1), or `null` when there is no motion label (the frame-diff fallback with no motion file). It is the source's own confidence, already priced for its uncertainty reasons.
- `uncertain` is `true` when the segment's labels cannot commit and the vision step (`describe`) must adjudicate it; its verdict wins in the shotlog. `uncertain_reasons` lists why, and is empty when `uncertain` is `false`:
  - the four motion reasons (`scale_vs_radial_flow`, `parallax_vs_rotation`, `self_moving_regions`, `mixed_motion_signals`) are copied from the move or phase that supplied `motion_label`, for traceability;
  - `motion_uncertain` is added when that source itself was `uncertain`;
  - `role_signals_disagree` is quality's own reason, added when the role triggers contradict each other: a head/tail segment that is both a transition and a setup/teardown candidate; an accidental trigger (obstruction, near-uniform frame, static low-detail frame) on a segment that overlaps a motion move or carries a non-static label; or the `unclassified motion` fallback.
- `confidence` is the role confidence minus `0.15` per quality-level flag (`role_signals_disagree`, `motion_uncertain`), clamped to `[0.05, 1.0]`. The four propagated motion reasons do not deduct again — they are already priced into `motion_label_confidence`. When both flags are present the deduction is `0.30`. The accidental run rule sets its own fixed confidence (`0.6` / `0.65`) and keeps the segment's `uncertain_reasons`, adding `role_signals_disagree` when the upgrade contradicts a `move` segment with an overlapping move.
- `defects` lists only the measured defects that cross their threshold, each with a normalised `value` (0–1, higher is worse) and a human-readable `detail`: `blur` (Laplacian variance below half the clip's own median), `exposure` (clipped highlights, crushed shadows or mean luma outside 40–200), `shake` (trajectory residual), `obstruction` (large, flat, static area), `horizon` (measured roll above 5°).
- `metrics` always carries the raw numbers behind the defects (`laplacian_rel`, `clip_high_pct`, `clip_low_pct`, `mean_luma`, `shake`, `obstruction`, `horizon_deg`), even when no defect is reported.
- `horizon_deg` is the roll of the horizon in degrees, measured per frame from a dominant long near-horizontal line (the sea/sky boundary) and averaged over the segment. Positive means the right end of the line is higher on screen; `0` is level. A frame contributes only when at least 3 long near-horizontal lines (length ≥ 25% of the frame width, angle within 15° of horizontal) survive and their angles are clustered (median absolute deviation ≤ 3°); otherwise the frame abstains. When no frame in a segment has a measurement, `horizon_deg` is `null` — the tool abstains instead of guessing from arbitrary edges. The `horizon` defect fires only when `horizon_deg` is not `null` and `|horizon_deg| > 5`, with severity scaled to 15°; `null` never produces a defect. Long parallel non-horizon edges (rock striations, canopy edges) can still be read as a horizon when no sea line exists; the vision step in D1b is expected to veto such segments.
- Intent and defect stay separate: blur or shake on a fast `move` is still reported as a defect, but the segment also gets the evidence string `motion blur during a fast move (intent)` / `shake during a fast move (intent)`. The director decides.

## Notes

- Label, never delete: the tool only reports what it measured. It never cuts, trims or ranks.
- This is the third stage of the CPU-only D1a analysis (after `ingest` and `motion`); output feeds the director and the review UI.
