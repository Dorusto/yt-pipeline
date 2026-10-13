# D1a-05 — `scripts/quality.py`: measured defects and a role per segment

## Goal

One standalone CLI tool that measures footage quality per segment and labels each
segment of a clip with a **role** (`move`, `hold`, `transition_in`, `transition_out`,
`setup`, `teardown`, `accidental`) and a confidence, plus the measured defects
(`blur`, `shake`, `exposure`, `obstruction`, `horizon`). **Labels only — nothing is
ever deleted, cut or trimmed here.** Plus its agent skill and its tests. This is
the third CPU-only stage of D1a, after `ingest` and `motion`.

## Files to create or change (exact paths, nothing else)

- `scripts/quality.py` — create
- `.claude/skills/yt-quality/SKILL.md` — create
- `tests/test_quality.py` — create

Do not touch any other file. `scripts/ingest.py`, `scripts/motion.py` and the
schemas exist; read them for style and contracts but do not change them.

## Rules that apply (copied in full from CLAUDE.md, docs/VISION.md and docs/DECISIONS.md)

1. **Code and documentation → in English. Comments in code → none (self-documenting
   code).** A module docstring and one-line docstrings on public functions/classes
   are fine — same style as `scripts/motion.py`.
2. **Every new tool** is a standalone script in `scripts/` plus a skill in
   `.claude/skills/`, with `--help`, `--json` and re-run safety. No tool imports
   another tool; shared code only in `scripts/common/`.
3. **Files are the interface.** Stages talk through JSON/YAML on disk, validated
   against schemas in `schemas/`. Any stage can be re-run alone.
4. **Same contract for humans and agents.** Files and flags in, files out.
   `--help` with examples. `--json` prints a machine-readable summary. Safe to
   re-run: steps already done are skipped.
5. **Originals are read-only.** All outputs go to a project workspace. Never write
   into the source folder, never modify or delete a source file. The repo is
   public: never commit footage or frames.
6. **Core principle — label, never decide alone (docs/guides/shot-grammar.md §1):**
   "A segment is never thrown away by the analysis tools. They label it; the
   director (and Doru in the review UI) decides." Intent and defect are separate
   fields: motion blur during a whip pan is intent; motion blur on a static shot
   is a defect. `setup` / `teardown` / `accidental` are *likely* roles with a
   confidence, never a verdict. One file can hold several intentional shots.
7. **Definition of done (every task):** tests pass; ran end-to-end on sample media;
   outputs validated against `schemas/`; no source file modified.
8. **The build LXC has 4 CPU cores, no GPU.** Stay fast on CPU: sample frames, work
   at reduced resolution (default 320 px wide for measurements). No new
   dependencies (OpenCV + numpy already present). No torch, no network.
   **Do not re-install MediaPipe — OpenCV was chosen intentionally.**
9. **Skills live in the repo** in `.claude/skills/`, only portable frontmatter
   fields (`name`, `description`, `compatibility`), commands relative to the repo
   root.
10. **GitHub commits** are made by the architect, not by you. Do not commit or push.

## Interface

```bash
.venv/bin/python scripts/quality.py INPUT --workspace DIR [--motion-dir DIR] [--force] [--sample-fps 2] [--width 320] [--json]
.venv/bin/python scripts/quality.py --help
```

- **INPUT** (positional, required): one video file, or a folder of `.mp4`/`.mov`/
  `.m4v` files (non-recursive, case-insensitive, sorted by name) — same convention
  as `scripts/motion.py`.
- **`--workspace DIR`** (required): output root; writes `DIR/quality/<stem>.json`
  per clip. Never writes into the source folder.
- **`--motion-dir DIR`** (default `DIR/motion`): where to look for the matching
  `motion/<stem>.json`. When present it drives segmentation and carries the
  camera label and shake; when absent the tool still works on its own coarse
  segmentation and adds a warning.
- **`--force`**: re-analyse even when the output exists and validates; without it
  such clips are skipped and counted as `skipped`.
- **`--sample-fps`** (default `2`): how many frames per second are decoded for the
  measurements (blur/exposure/obstruction). Skip the rest with `grab()`.
- **`--width`** (default `320`): measurement width in pixels.
- **`--json`**: one object on stdout, e.g.
  `{"clips": 21, "analyzed": 21, "skipped": 0, "output_dir": "/abs/workspace/quality"}`.
  Otherwise a short human summary.
- **Exit codes:** `0` success; `1` on any error with one `[error] ...` line on
  stderr. Never prompt. Atomic writes (temp file + `os.replace`), schema-validated
  before the write.

## Algorithm (fixed — implement this, not a variant)

### 1. Measurements (per sampled frame, at `--sample-fps` and `--width`)

On the gray version of each sampled frame:

- **blur**: variance of `cv2.Laplacian(gray, CV_64F)` (whole frame).
- **exposure**: `mean_luma` (mean of gray), `clip_high_pct` = percent of pixels
  `>= 250`, `clip_low_pct` = percent of pixels `<= 5`.
- **obstruction**: fraction of the frame that is large, flat and static —
  `cv2.blur(gray, 9)` local standard deviation below `6` marks "flat" pixels;
  a flat pixel also counts only if it barely changed since the previous sampled
  frame (`|diff| <= 3`). The fraction is the obstruction score of the frame.
- **frame-diff**: mean absolute difference to the previous sampled frame (used
  for the no-motion-file fallback and the accidental rule).

Per segment (mean of its frames; also compare to the clip's own median):

- `laplacian_rel` = segment mean Laplacian variance / clip median Laplacian
  variance (this is the blur reference — relative to the clip, never absolute).
- `clip_high_pct`, `clip_low_pct`, `mean_luma`, `obstruction` = segment means.
- `shake` = mean `shake` of the overlapping motion trajectory rows (0.0 when
  there is no motion file).
- `horizon_deg` = the segment's mean roll offset relative to the clip's median
  cumulative roll (from the motion trajectory: cumulative sum of `roll_deg` per
  row, minus its clip median). `0.0` without a motion file.

### 2. Segments (tile the whole clip, no gaps)

**With a motion file:** start from its `phases` (they already tile the clip) and
merge consecutive phases that get the same role (step 3) into one segment. The
merged segment's `motion_label` is the label of the phase (or overlapping move)
with the longest duration inside it.

**Without a motion file:** split the clip into runs of "still" vs "moving" from
the sampled frame-diff series (threshold `2.0` gray levels; merge runs shorter
than `0.5 s` into the previous), each run one segment, `motion_label` null, and
add the warning `no motion file; segments are coarse`.

Either way: segments tile `[0, duration_s]` exactly (first starts at `0.0`, last
ends at `duration_s`, each starts where the previous ended) and carry `id`
(`s1`, `s2`, … in time order).

### 3. Role of a segment (with confidence and evidence)

Position windows: `head` = segments that start before `min(5.0, 0.2 * duration_s)`,
`tail` = segments that end after `duration_s - min(5.0, 0.2 * duration_s)`; a
segment in both (very short clip) is `head` only. `middle` = everything else.
`rotation` labels are `roll_cw`, `roll_ccw`, `pan_left`, `pan_right`.

Decide in this order (first match wins):

1. **`accidental`** — the segment is `>= 2.0 s` and (`obstruction >= 0.6` or
   (`laplacian_rel <= 0.2` and (`mean_luma <= 25` or `mean_luma >= 235`)) or
   (`frame-diff mean <= 0.5` and `laplacian_rel <= 0.25`)). Evidence names the
   trigger. Confidence `0.55` (+ `0.15` when the trigger is obstruction or the
   frame is almost uniform). This is the pocket / ground / sky-for-minutes case.
2. **`transition_in`** — `head`, motion type is a `steady_move` or `irregular`
   phase whose `speed` is `fast`, label is a `rotation` or `complex_motion`,
   `steadiness` is `no_shaking`/`minimal_shaking`, and the segment is `<= 3.0 s`.
   Evidence: "fast clean rotation at take head". Confidence `0.5` (+ `0.1` when
   the label is a pure rotation).
3. **`transition_out`** — same rule on `tail` ("fast clean rotation at take tail").
4. **`setup`** — `head`, the segment is `<= 4.0 s` and (phase type `irregular` or
   `steadiness` is `unsteady`/`very_unsteady` or `speed` is `fast`). Evidence:
   "short irregular motion at the head of the take". Confidence `0.5`
   (`0.65` when two of the three triggers fire). *Likely role, not a verdict.*
5. **`teardown`** — same rule on `tail`.
6. **`move`** — the segment overlaps a motion `moves[]` entry (steady range), or
   its phase type is `steady_move`/`ramp_up`/`ramp_down`. Confidence = the
   overlapping move's `confidence` when there is one, else `0.5`. Evidence:
   "steady <label> with handles" when a move exists, else "motion phase <type>".
7. **`hold`** — the segment is a `still` phase (or still run) of `>= 1.0 s`.
   Confidence `0.7` when it is in the `middle` and clean (`laplacian_rel >= 0.5`
   and no exposure defect), `0.55` at `head`/`tail`, `0.4` when it carries
   defects. Evidence: "still, clean" / "still at take head" / "still, <defects>".
8. Fallback: **`move`** with confidence `0.4` and evidence "unclassified motion".

Add the defects (step 4) to every segment regardless of role. `role_evidence` is
a list of short human-readable strings. Keep every segment in the output — never
drop one.

### 4. Defects (measured values, only when they cross their threshold)

Severity `value` is `0..1`, higher is worse; `detail` is a short human-readable
measurement.

| name | reported when | value |
|---|---|---|
| `blur` | `laplacian_rel < 0.5` | `min(1, (0.5 - laplacian_rel) / 0.5)` |
| `exposure` | `clip_high_pct > 5` or `clip_low_pct > 10` or `mean_luma` outside `[40, 200]` | the worst offending fraction, normalised to `0..1` (e.g. `clip_high_pct / 100` capped at 1, `clip_low_pct / 100`, or `distance of mean_luma from the [40,200] band / 100`) |
| `shake` | mean shake `>= 0.03` | `min(1, shake / 0.15)` |
| `obstruction` | mean obstruction `>= 0.25` | `min(1, obstruction)` |
| `horizon` | `\|horizon_deg\| >= 1.5` **and** the segment role is `hold` or its label is `static` | `min(1, abs(horizon_deg) / 5.0)` |

A `blur` defect on a segment whose role is `move` and whose motion `speed` is
`fast` is **intent, not defect**: still report it, but add the evidence string
`motion blur during a fast move (intent)` on the segment and keep the defect
entry (the director decides). Do the same for `shake` on a `fast` `move`.

`metrics` always carries the raw numbers (`laplacian_rel`, `clip_high_pct`,
`clip_low_pct`, `mean_luma`, `shake`, `obstruction`, `horizon_deg`), even when no
defect is reported.

## Output contract — `DIR/quality/<stem>.json`

Must pass `common.schema.validate_or_raise(data, "quality")` (authoritative:
`schemas/quality.schema.json`, read it). Required: `file`, `path`, `generated_at`,
`duration_s`, `segments` (min 1 item). Optional but expected: `motion_file`
(absolute path of the motion JSON used, or `null`), `warnings`.

Structure the module so the math is testable without video I/O:

- `measure_frame(gray, prev_gray)` → the per-frame numbers
- `segment_metrics(frames, duration_s, ...)` or similar aggregation
- `assign_role(segment_facts)` → `(role, confidence, evidence)` where
  `segment_facts` is a plain dict (position, phase type, label, speed, steadiness,
  metrics, overlap with a move) — this is what the unit tests drive
- `build_defects(metrics)` → the defect list
- `segments_from_motion(motion_data, ...)` and `segments_from_frames(...)`
- `analyze_clip(path, motion_path, sample_fps, width)` → the full record
- `main(argv=None)` → exit code

## Skill — `.claude/skills/yt-quality/SKILL.md`

Style of the existing skills. `name: yt-quality`. `description` must mention
measured defects (blur, exposure, shake, obstruction, horizon) and roles (move,
hold, transition in/out, setup, teardown, accidental) with confidence, and that
the tool only labels and never deletes; trigger phrases such as "verifică
calitatea footage-ului", "care cadre sunt mișcate sau blurate", "quality
check", "etichetează setup și teardown". `compatibility`: repo root, repo
`.venv`, CPU only, reads clips read-only, writes `quality/<stem>.json`, prefers a
matching `motion/<stem>.json` in the workspace.

## Tests — `tests/test_quality.py`

Cover at least:

1. `--help` exits 0 and lists `--workspace`, `--motion-dir`, `--force`, `--json`.
2. **`assign_role` unit tests** on fabricated facts (a dict per case): head +
   fast clean rotation + `<= 3 s` → `transition_in`; tail equivalent →
   `transition_out`; head + short + irregular/unsteady → `setup`; tail →
   `teardown`; middle steady move overlapping a move → `move`; middle clean
   still `>= 1 s` → `hold`; obstruction `0.8` over `>= 2 s` → `accidental`;
   fallback → `move` with confidence `0.4`.
3. **`build_defects`:** `laplacian_rel 0.3` → blur defect with value `0.4`;
   `clip_high_pct 30` → exposure; shake `0.05` → shake; obstruction `0.4` →
   obstruction; `horizon_deg 3.0` on a hold → horizon; a clean middle segment →
   empty defect list but complete `metrics`.
4. **Intent vs defect:** blur on a fast `move` still lists the defect and adds
   the `intent` evidence string.
5. **End-to-end synthetic clips** (numpy/cv2, same technique as
   `tests/test_motion.py`): a solid mid-gray still clip (low Laplacian but not
   the accidental rule — use a noisy gray so `laplacian_rel` stays near 1) →
   output validates, segments tile, role `hold`; a pure black clip of `3 s` →
   `accidental` or at least an `exposure` defect; a clip with a fabricated motion
   JSON next to it in the workspace (roles/labels planted) → the output
   `motion_file` is set and the roles follow the motion phases.
6. **Folder mode, skip and `--force`** like the other tools.
7. **Errors:** missing input → exit 1 with `[error]`; unreadable motion JSON →
   warning + fallback segmentation (not a crash).

## Acceptance checks (the architect will run these)

```bash
cd /root/projects/yt-pipeline
.venv/bin/python -m pytest -q
.venv/bin/python scripts/quality.py --help
.venv/bin/python scripts/quality.py ~/media/yt-test/proxy --workspace ~/media/yt-test/workspace --json
```

Expected on the real folder (21 DJI Mimo proxies, 688.9 s, after
`scripts/motion.py` has already written `~/media/yt-test/workspace/motion/`):

- exit 0, `"analyzed": 21`, outputs in `~/media/yt-test/workspace/quality/`;
- every JSON validates against the `quality` schema and segments tile the clip;
- every clip keeps **all** its duration in labelled segments (nothing deleted);
- a plausible mix of roles across the set: plenty of `move` and `hold`, some
  `setup`/`teardown` at take heads/tails, occasional `transition_in`/`out`,
  `accidental` only where the footage really is junk;
- the whole-folder run stays under about 5 minutes on this 4-core machine.

## Out of scope

Deleting or trimming anything, scoring or ranking, LLM/vision calls, DOVER,
wind/audio defects, duplicate detection, schema changes, README/ROADMAP/DECISIONS
updates, git commits.
