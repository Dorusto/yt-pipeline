# D1a-04 — `scripts/motion.py`: camera trajectory, motion phases and CameraBench moves

## Goal

One standalone CLI tool that analyses camera motion in a clip with OpenCV optical
flow on the CPU: a sampled camera trajectory, a split of the take into motion
phases (still → ramp → steady move → ramp → still) and usable moves labelled with
the CameraBench vocabulary (speed, steadiness, steady range and handles). Plus its
agent skill and its tests. Output feeds `scripts/quality.py` (D1a-05) and later
`describe`/`index`.

## Files to create or change (exact paths, nothing else)

- `scripts/motion.py` — create
- `.claude/skills/yt-motion/SKILL.md` — create
- `tests/test_motion.py` — create

Do not touch any other file. In particular do not edit `CLAUDE.md`, `README.md`,
`docs/`, `schemas/`, `scripts/common/`, `config.yaml*`, or any existing script,
skill or test. `scripts/ingest.py` exists; you may read it for style but must not
change it.

## Rules that apply (copied in full from CLAUDE.md, docs/VISION.md and docs/DECISIONS.md)

1. **Code and documentation → in English. Comments in code → none (self-documenting
   code).** A module docstring and one-line docstrings on public functions/classes
   are fine — same style as `scripts/ingest.py` and `scripts/common/schema.py`.
2. **Every new tool** is a standalone script in `scripts/` plus a skill in
   `.claude/skills/`, with `--help`, `--json` and re-run safety. No tool imports
   another tool; shared code only in `scripts/common/`.
3. **Files are the interface.** Stages talk through JSON/YAML on disk, validated
   against schemas in `schemas/`. Any stage can be re-run alone.
4. **Same contract for humans and agents.** Files and flags in, files out.
   `--help` with examples. `--json` prints a machine-readable summary. Safe to
   re-run: steps already done are skipped.
5. **Originals are read-only.** All outputs go to a project workspace next to the
   footage. Never write into the source folder, never modify or delete a source
   file. The repo is public: never commit footage, frames or media files.
6. **Definition of done (every task):** tests pass; ran end-to-end on sample media;
   outputs validated against `schemas/`; no source file modified.
7. **The build LXC has 4 CPU cores, no GPU.** Analysis must stay fast on CPU: work
   at reduced resolution, sample the frame rate, skip frames with `cap.grab()`.
   Target: at most about 1.5x real time per clip at the default settings. No new
   dependencies: OpenCV is `opencv-python-headless` (already in `requirements.txt`).
   **Do not re-install MediaPipe — OpenCV was chosen intentionally.** No torch, no
   GPU, no network.
8. **Intent and defect are separate fields. Motion tools label, never delete.**
   Nothing is thrown away here; the tool only reports what the camera did.
9. **Skills live in the repo** in `.claude/skills/`, only portable frontmatter
   fields (`name`, `description`, `compatibility`). Commands relative to the repo
   root. The skill describes what the script does today.
10. **GitHub commits** are made by the architect, not by you. Do not run `git
    commit`, `git push` or open PRs.

## Interface

```bash
.venv/bin/python scripts/motion.py INPUT --workspace DIR [--force] [--sample-fps 10] [--width 320] [--json]
.venv/bin/python scripts/motion.py --help
```

- **INPUT** (positional, required): one video file, or a folder to process every
  `.mp4` / `.mov` / `.m4v` in it (non-recursive, case-insensitive, sorted by name).
- **`--workspace DIR`** (required): output root. Writes `DIR/motion/<stem>.json`
  per clip (`<stem>` = file name without extension), creating directories as
  needed. Never writes into the source folder.
- **`--force`**: re-analyse even when the output already exists. Without it, a clip
  whose output JSON already exists and validates against the `motion` schema is
  skipped (counted as `skipped` in the summary).
- **`--sample-fps`** (default `10`): analysis sampling rate. Frames between samples
  are skipped with `grab()`.
- **`--width`** (default `320`): analysis width in pixels; frames are scaled down to
  this width keeping aspect ratio.
- **`--json`**: one JSON object on stdout, e.g.
  `{"clips": 21, "analyzed": 21, "skipped": 0, "output_dir": "/abs/workspace/motion"}`.
  Without `--json`, a short human summary (analyzed/skipped counts, total time).
- **Exit codes:** `0` success. `1` on any error with one `[error] ...` line on
  stderr (missing input, unreadable video, write failure). Never prompt.
- **Re-run safety:** partial output is never left behind — write each JSON only
  after analysis finished and validated (write to a temp name then `os.replace`).

## Algorithm (fixed — implement this, not a variant)

### 1. Trajectory from sparse optical flow

For each consecutive pair of sampled frames (gray, scaled to `--width`):

1. `cv2.goodFeaturesToTrack` on the previous gray: `maxCorners=200`,
   `qualityLevel=0.01`, `minDistance=15`, `blockSize=7`.
2. `cv2.calcOpticalFlowPyrLK` to the next gray: `winSize=(21, 21)`, `maxLevel=3`.
3. `cv2.estimateAffinePartial2D` (RANSAC) on the matched points → 2x3 matrix
   `[[a, b, tx], [c, d, ty]]`. If the fit fails or has fewer than 8 inliers, record
   the pair as invalid: `dx=dy=roll_deg=0`, `scale=1`, `inlier_ratio=0`.
4. Per pair, with `w`/`h` the analysis frame size:
   - `dx = tx / w`, `dy = ty / h` (fractions of frame width/height **of the image
     motion**)
   - `scale = sqrt(a*a + c*c)` (1.0 = no change)
   - `roll_deg = degrees(atan2(c, a))` — **positive means the image rotates
     clockwise on screen**
   - `inlier_ratio` = mean of the inlier mask.

Record one trajectory row per pair: `t` = midpoint time in seconds of the pair
(first pair is at `0.5 / sample_fps`), the four numbers above, and later `speed`
and `shake`. Rows go into `trajectory[]` in the output JSON.

### 2. Speed and shake

Smooth `dx`, `dy`, `log(scale)` and `roll_deg` with a moving average of 5 samples
(edges: shrink the window). Per sample:

- `speed_score = max( hypot(dx, dy), |log(scale)| / 0.25, |roll_deg| / 45.0 )
  * sample_fps` — components are per sample step, so multiply by `sample_fps` to
  get per-second rates. This is the number classified as slow/regular/fast.
- `shake = hypot(dx - dx_smooth, dy - dy_smooth) * sample_fps` — the high-frequency
  residual of the translation (this is the unsteadiness the shot-grammar guide
  calls "trajectory residual").

Store `speed` and `shake` on each trajectory row (unsmoothed `dx/dy/scale/roll_deg`
as measured).

### 3. Phases (tile the whole clip, no gaps)

Classify each sample: `still` when `speed_score < 0.05`, else `moving`. Build
maximal runs; merge any run shorter than `0.4 s` into the previous run (the first
run merges forward). Then, for each `moving` run:

- Compute its mean rates: `rate_dx = mean(dx) * sample_fps`, `rate_dy`,
  `rate_scale = mean(log(scale)) * sample_fps`, `rate_roll = mean(roll_deg) * sample_fps`.
- `ramp_up` = the leading part of the run while `speed_score < 0.6 * run_median`
  and we are in the first `0.5 s` of the run (cut at the first sample that reaches
  the band, or at 0.5 s, whichever comes first); `ramp_down` is symmetric at the
  tail; whatever is left in the middle is `steady_move`. If the whole run is short
  (≤ `1.0 s`) or nothing reaches the band, the whole run is one `irregular` phase.
- A `moving` run is `irregular` (one phase, no ramp split) when its mean `shake`
  exceeds `0.08` or its label (step 4) is `complex_motion`.
- `still` runs are `still` phases (label `static`, speed `slow`, steadiness from
  shake).

Phase types: `still`, `ramp_up`, `steady_move`, `ramp_down`, `irregular`.
Each phase carries `start_s`, `end_s`, `dominant_motion` (the label of its samples),
`speed` and `steadiness` words. Phases must tile `[0, duration_s]` exactly:
`start_s` of the first is `0.0`, `end_s` of the last is `duration_s`, each phase
starts where the previous ended. A clip with no valid trajectory rows becomes one
`irregular` phase covering the whole clip with a warning.

### 4. CameraBench label of a window

From the mean rates of a window (a phase or a move):

- component scores (all in per-second units):
  - pan: `|rate_dx|`
  - tilt: `|rate_dy|`
  - roll: `|rate_roll| / 45.0`
  - dolly: `|rate_scale| / 0.25`
- If the highest score is below `0.05` → label `static`, `semantic_check: false`.
- If the top two scores are within a factor of 2 of each other **and** the second
  is also above `0.05` → label `complex_motion`, `semantic_check: true`.
- Else the dominant component with its sign, where **the label names the camera
  move**, inverting the image motion (the flow measures how the picture moves, the
  label says what the camera did):
  - `rate_dx < 0` → `pan_right`, `rate_dx > 0` → `pan_left`
  - `rate_dy > 0` → `tilt_up`, `rate_dy < 0` → `tilt_down` (image y grows downward)
  - `rate_roll < 0` → `roll_cw`, `rate_roll > 0` → `roll_ccw` (positive roll_deg is
    image-clockwise, so camera-clockwise is negative)
  - `rate_scale > 0` → `dolly_in`, `rate_scale < 0` → `dolly_out`, and
    `semantic_check: true` (2D flow cannot separate dolly from zoom; the vision
    step decides). Pan/tilt/roll labels get `semantic_check: false`.

### 5. Moves

A move is a usable span built from the phases:

- `steady_start_s` / `steady_end_s` = bounds of a `steady_move` phase. Only steady
  phases with length `>= 1.5 s` produce a move (shorter ones stay in `phases` but
  have no move).
- Adjacent steady phases with the same label separated by less than `0.5 s` of ramp
  are one move (steady range spans the whole middle).
- `start_s` = start of the ramp_up phase in front of the steady part (or
  `steady_start_s` when there is no ramp); `end_s` is symmetric with ramp_down.
- `handle_in_s = steady_start_s - 0.25` but never before `start_s`;
  `handle_out_s = steady_end_s + 0.25` but never after `end_s`. These are the
  recommended cut points: a cut never starts mid-acceleration.
- `label`, `semantic_check` from step 4 on the steady window; `speed` and
  `steadiness` words from the steady window's mean `speed_score` and mean `shake`.
- `confidence` in `[0, 1]`: start at `0.5`, add `0.2` when the steady length is at
  least `3 s`, add `0.1` when mean inlier ratio of the steady window is `>= 0.7`,
  add `0.1` when mean shake of the steady window is below `0.03`, subtract `0.2`
  when `semantic_check` is true; clamp to `[0, 1]`.
- `mean_dx`, `mean_dy`, `mean_scale`, `mean_roll_deg`: the steady window's means of
  the per-sample values (not rates).
- `id` = `m1`, `m2`, … in time order. `notes` = `""` unless something needs saying
  (e.g. "invalid trajectory rows: 12").

### 6. Words for speed and steadiness

- speed from `speed_score`: `< 0.15` → `slow`, `<= 0.6` → `regular`, else `fast`.
- steadiness from mean `shake`: if the label is `static` and shake `< 0.01` →
  `static`; shake `< 0.01` → `no_shaking`; `< 0.03` → `minimal_shaking`;
  `< 0.08` → `unsteady`; else `very_unsteady`.

## Output contract — `DIR/motion/<stem>.json`

Must pass `common.schema.validate_or_raise(data, "motion")` (authoritative schema:
`schemas/motion.schema.json`, read it). Required top-level keys: `file`, `path`,
`generated_at`, `duration_s`, `fps_source`, `analysis`, `trajectory`, `phases`,
`moves`, `warnings`. `analysis.method` = `"lucas_kanade_affine_partial_ransac"`,
`analysis.source` = `"optical_flow"` (gyro is a later upgrade, not in this task),
`analysis.sample_fps`, `analysis.width`, `analysis.frames_analyzed`.

Structure the module so the math is testable without video I/O:

- `compute_trajectory(prev_gray, next_gray)` or similar pure step
- `smooth_series(values, window=5)`
- `speed_and_shake(rows, sample_fps)`
- `label_window(rows)` → `(label, semantic_check)`
- `speed_word(score)`, `steadiness_word(shake)`
- `segment_phases(rows, duration_s, sample_fps)` → phase dicts
- `build_moves(phases, rows, sample_fps)` → move dicts
- `analyze_clip(path, sample_fps, width)` → full record
- `main(argv=None)` → exit code

## Skill — `.claude/skills/yt-motion/SKILL.md`

Style of the existing skills (frontmatter `name`, `description`, `compatibility`,
then steps from the repo root). `name: yt-motion`. The `description` must mention
camera motion analysis, motion phases, CameraBench move labels (pan, tilt, roll,
dolly, speed, steadiness, handles) and trigger phrases such as "analizează mișcarea
camerei", "ce mișcări sunt în clip", "camera motion", "găsește mișcările
stabile". `compatibility`: repo root, repo `.venv`, CPU only (no GPU), reads clips
read-only, writes `motion/<stem>.json` in the workspace, about 1.5x real time per
clip at default settings.

## Tests — `tests/test_motion.py`

`tests/conftest.py` already puts `scripts/` on `sys.path`. Split tests into pure
unit tests (fabricated trajectory rows) and one or two end-to-end runs on tiny
synthesised videos. Cover at least:

1. `--help` exits 0 and lists `--workspace`, `--force`, `--sample-fps`, `--json`.
2. **Label classification** (direct calls to `label_window`): a steady negative
   `dx` window → `pan_right`, `semantic_check` False; positive `dx` → `pan_left`;
   positive `dy` → `tilt_up`; negative `dy` → `tilt_down`; negative `roll_deg` →
   `roll_cw`; positive `roll_deg` → `roll_ccw`; positive `log(scale)` → `dolly_in`
   with `semantic_check` True; near-zero everything → `static`; mixed equal
   components → `complex_motion`.
3. **Words:** `speed_word` and `steadiness_word` at the documented thresholds.
4. **Phases** on a fabricated series (list of row dicts with `t`, `dx`, `dy`,
   `scale`, `roll_deg`, `inlier_ratio`): a still → slow-move → still take becomes
   `still`, `ramp_up`/`steady_move`/`ramp_down` (or `steady_move` alone when the
   ramps are not detectable), `still`; phases tile the duration with no gaps and no
   overlaps; a merged short blip does not become its own phase.
5. **Moves:** steady part `>= 1.5 s` yields a move with
   `start_s <= steady_start_s < steady_end_s <= end_s` and
   `handle_in_s < steady_start_s < steady_end_s < handle_out_s`; a steady part of
   `1.0 s` yields no move; ids `m1`, `m2` in time order.
6. **End-to-end still clip:** generate 3 s of static noise with numpy + OpenCV
   `VideoWriter` (or ffmpeg from a noise image) at 64x48, run the CLI → output
   validates, label `static`, phases tile the clip.
7. **End-to-end synthetic pan:** build a 640x360 numpy noise image and write 5 s of
   frames where a 320x180 window slides horizontally at a constant rate (first
   `0.5 s` stationary, then a constant pan, then stationary again) at 25 fps →
   the CLI finds one move labelled `pan_right` or `pan_left` (whichever matches
   your sign convention — assert on the label actually produced by the documented
   image-motion inversion: sliding the crop window to the right moves the image
   content left, so the camera label is `pan_right`), with `steady_start_s` inside
   the constant-pan part. Keep the noise high-contrast so `goodFeaturesToTrack`
   finds points. If the encoder makes this flaky, generate an `.mp4` with
   `cv2.VideoWriter` using `mp4v` and skip the test with a clear message when the
   writer cannot open — but try hard first: this is the only real-video check.
8. **Folder mode, skip and `--force`:** a folder of two clips is processed into
   `motion/<stem>.json` files; a second run skips both (summary says so); a third
   run with `--force` rewrites them.
9. **Errors:** missing input → exit 1 and `[error]` on stderr; a non-video file as
   input → exit 1.

## Acceptance checks (the architect will run these)

```bash
cd /root/projects/yt-pipeline
.venv/bin/python -m pytest -q                       # all green
.venv/bin/python scripts/motion.py --help
.venv/bin/python scripts/motion.py ~/media/yt-test/proxy --workspace ~/media/yt-test/workspace --json
```

Expected on the real folder (21 DJI Mimo proxies, 1280x720 25 fps, 688.9 s total,
read-only source):

- exit 0, `"analyzed": 21`, output in `~/media/yt-test/workspace/motion/`;
- every JSON validates against the `motion` schema;
- the longest clip (`...0072...`, 175.9 s) finishes in well under 5 minutes;
- a whole-folder run stays under about 20 minutes on this 4-core machine;
- `phases` tile each clip; `moves` are plausible for action-camera footage (some
  `static` holds, some pans/tilts/dollies, head and tail often `irregular`);
- source files untouched (mtimes unchanged).

## Out of scope

Gyro-based motion (the proxies carry none; `analysis.source` is `optical_flow`),
semantic moves (orbit, tracking, reveal — those are `describe` in D1b), quality
defects (that is `scripts/quality.py`), PySceneDetect/TransNetV2, LLM calls,
schema changes, README/ROADMAP/DECISIONS updates, git commits.
