# D1a-05 — quality.py round 2 fixes (architect audit findings)

The first implementation is good overall (roles, tiling, defects, intent-vs-defect
all verified). Two findings to fix, both in `scripts/quality.py` + tests. Files
touched: `scripts/quality.py`, `tests/test_quality.py`, and the skill only if its
description of behavior changes.

## Finding 1 (real bug) — `horizon_deg` from cumulative flow roll is wrong

The current horizon metric cumulatively sums the per-step `roll_deg` of the
motion trajectory. That estimator drifts, and the drift compounds: on clip
`...0051...` the output claims holds at `-13°` and `+33°`, while Canny+Hough edge
angles of the actual frames show `+2.0°` at t=0.5 s and `+3.5°` at t=18.5 s — the
real difference is ~1.5°, not 46°. 31 `horizon` defects across the test set are
mostly this artefact.

**Replace the measurement with a direct image measurement** (this is also the
"horizon tilt (roll offset on a static shot)" the shot-grammar guide asks for):

- In `measure_frame`, add `edge_angle_deg`: run `cv2.Canny(gray, 50, 150)` on the
  measurement-resolution gray frame, then `cv2.HoughLines(edges, 1, np.pi/360, 60)`.
  For each returned `(rho, theta)`, convert `theta` to degrees relative to
  horizontal (`deg = degrees(theta) - 90`, normalised into `[-90, 90)`), take the
  **median** of those angles as the frame's edge angle. Record the line count too.
  When there are fewer than `30` lines, the frame has `edge_angle_deg = None`
  (not measured).
- Per segment: `horizon_deg` = mean of the segment's measured frame edge angles
  minus the clip's median measured frame edge angle (the clip reference is the
  median over all sampled frames that have a measurement). When the segment has
  no measured frames, fall back to `0.0`. Keep the field name `horizon_deg` and
  keep `metrics.horizon_deg` always present.
- Keep the `horizon` defect rule as it is (`|horizon_deg| >= 1.5` and the segment
  is a `hold` or its label is `static`).
- Drop the cumulative-roll helper; do not use trajectory `roll_deg` for horizon
  any more. Trajectory `shake` is still used for the `shake` metric.
- The no-motion-file path must work too (edge angles come from the frames, not
  from the motion file).

## Finding 2 (cosmetic) — "still, clean" evidence contradicts listed defects

`assign_role`'s hold branch says `"still, clean"` when `laplacian_rel >= 0.5` and
there is no exposure defect, even if the segment carries `obstruction`, `shake`
or `horizon` defects (see segment `00:08.6-00:10.7` of clip `...0072...`:
evidence `still, clean` with an `obstruction:0.25` defect). Make the evidence
consistent: a hold is only "still, clean" when `build_defects(...)` for it is
empty; otherwise `"still, <comma-separated defect names>"` as the existing
fallback already does. (In `assign_role` you may call `build_defects` with the
segment's metrics/role/label as the hold branch already does on the fallback.)

## Keep unchanged

Everything else: CLI, flags, roles and their order, confidence numbers, tiling,
defect thresholds and values, intent-vs-defect notes, schema validation, atomic
writes, skip/`--force`, performance (the whole 21-clip folder was 27 s; stay
under ~2 min).

## Tests

- Adjust/extend the horizon tests: a fabricated frame set with known edge angles
  (e.g. synthesise frames with a drawn tilted line/rectangle at known angles, or
  unit-test the frame-to-segment aggregation with planted `edge_angle_deg` values)
  must produce the expected relative `horizon_deg`; a segment without measured
  frames falls back to `0.0`.
- Add/adjust a hold-evidence test: a hold with an obstruction defect must not say
  `still, clean`.
- All existing tests keep passing except what these two fixes legitimately change.

## Acceptance (the architect re-runs)

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/quality.py ~/media/yt-test/proxy --workspace /tmp/opencode/quality_r2 --motion-dir ~/media/yt-test/workspace/motion --force --json
```

then: horizon defect values across the set must be plausible (mostly below a few
degrees vs the clip reference; no more ±30° holds), and no segment may carry
`still, clean` evidence together with a non-empty defect list.
