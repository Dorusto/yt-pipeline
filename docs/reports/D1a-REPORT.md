# D1a report — ingest, motion, quality (CPU only)

Branch `d1a-motion-quality`. Scope: the two PR #4 budget fixes, three standalone
analysis tools (`ingest`, `motion`, `quality`) with skills, schemas and tests,
and one run over all 21 test clips (688.7 s of 720p25 DJI proxies, read-only) into
`~/media/yt-test/workspace`. No LLM cost in this phase (D1a is CPU only).

**D1a-2** (2026-10-13) is the post-checkpoint round: a ground-truth eval tool and
the fixes from [`D1a-CHECKPOINT.md`](D1a-CHECKPOINT.md), each judged by that eval.
That section comes first; the original D1a report follows as the pre-checkpoint
record.

---

# D1a-2 — eval-driven fixes after Doru's checkpoint

Ground truth: [`eval/ground_truth_boat_2026-10-12.yaml`](../../eval/ground_truth_boat_2026-10-12.yaml)
(6 clips Doru filmed). Scorer: `scripts/eval_labels.py` (`yt-eval` skill,
`schemas/eval.schema.json`). Scores are 0..1, higher is better; a component the
ground truth does not ask for is `-`, never 0.

## Ground-truth eval: before / after

| Stage | move | role | horizon | events | overall |
|---|---|---|---|---|---|
| **Baseline** (tools as reviewed at the checkpoint) | 0.38 | 0.00 | 0.34 | 0.00 | **0.26** |
| After D1a-2-02 audit fixes (setup/teardown, slivers, `move · static`, ledger flock) | 0.38 | 0.00 | 0.38 | 0.00 | 0.27 |
| After D1a-2-03 platform bob + `travel_*` labels | 0.42 | 0.00 | 0.38 | 0.00 | 0.30 |
| After D1a-2-04 parallax, pan vs truck, `orbit_candidate` | 0.53 | 0.00 | 0.38 | 0.00 | 0.39 |
| After D1a-2-05 horizon from the sea/sky line | 0.53 | 0.00 | 0.97 | 0.00 | 0.44 |
| After D1a-2-06 scene activity + events | 0.53 | 0.00 | 0.97 | 1.00 | 0.52 |
| After D1a-2-07 `accidental` for junk takes | 0.53 | 1.00 | 0.97 | 1.00 | **0.69** |

Final per-clip table (`.venv/bin/python scripts/eval_labels.py --ground-truth
eval/ground_truth_boat_2026-10-12.yaml --workspace ~/media/yt-test/workspace`):

```
clip    move  role  horizon  events  overall
0051    -     1.00  -        -       1.00
0067    0.00  -     0.97     -       0.48
0072    0.39  -     -        -       0.39
0081    0.26  -     -        -       0.26
0083    0.98  -     -        1.00    0.99
0091    1.00  -     -        -       1.00
TOTAL   0.53  1.00  0.97     1.00    0.69
```

Test suite grew 188 → **261 passed** (`.venv/bin/python -m pytest -q`, offline,
no GPU, no API key). Motion + quality re-run over all 21 clips after every fix;
sources untouched.

## Works (verified in D1a-2)

- **`scripts/eval_labels.py`** (+ `yt-eval` skill, `schemas/eval.schema.json`) —
  scores interval overlap of expected vs predicted move/role (full credit for an
  exact label, 0.5 for a geometric look-alike such as `dolly_in` for
  `travel_forward`), horizon false positives and event recall within ±3 s. The
  baseline above was recorded before any fix; every fix moved the score.
- **Audit fixes (PR #7 external audit)** — `scripts/common/llm.py` spend ledger is
  now safe across concurrent processes (`fcntl.flock` sidecar lock, re-read before
  the budget check; a 4-process race test drops nothing without the lock); in
  `quality`, `setup`/`teardown` only appear before the first / after the last
  content segment, sub-second slivers merge away and every segment asserts
  `end_s > start_s`, and role `move` can no longer carry label `static`. The
  report's `1280x72025 fps` typo is fixed.
- **Platform bob + travel** (`motion`) — periodic 1.5–8 s tilt oscillation is
  detected, subtracted before labelling and reported as `platform_bob`
  `{period_s, amplitude}` instead of fake `tilt_up`/`tilt_down` moves; sustained
  smooth scale flow becomes `travel_forward`/`travel_backward` with
  `platform: vehicle`, keeping the CameraBench label in `label_geometric`.
- **Parallax** (`motion`) — a homography residual per sample pair separates
  rotation from translation with depth: lateral moves at `parallax >= 0.5` become
  `truck_right`/`truck_left`, and the same signal with a quiet centre proposes
  `orbit_candidate` (D1b confirms). Verified: 0072 `02:45–02:55` is now
  `truck_right` (was `pan_right`), 0091 and 0081's opening are `orbit_candidate`.
- **Horizon** (`quality`) — measured only from long (≥ 25 % of width),
  near-horizontal (≤ 15°), angle-clustered (MAD ≤ 3°) lines; absolute tilt vs true
  horizontal, `null` when no such line exists, defect only above 5°. 0067
  `00:00–00:30` reports **level** (−0.45°, no defect) — the checkpoint's
  acceptance — and the horizon score went 0.38 → 0.97.
- **Scene activity + events** (`motion`) — residual local flow after removing the
  global camera motion, measured on the scene band so foreground wake does not
  dominate, aggregated into per-second `scene_activity`; `events` derived per
  floor-level active span. 0083 (locked off, another boat entering and
  approaching) reports `subject_enters` 00:08, `activity_peak` 00:28,
  `activity_end` 00:35 against ground truth 00:10 / 00:32 / 00:38; neighbours
  0082/0084 report none. A later fix made one event set per active span (0072 had
  14 duplicates before it).
- **`accidental`** (`quality`) — a run of unstable segments (raw shake ≥ 0.1 or
  unsteady) with no stable move or hold becomes `accidental` when it lasts ≥ 5 s
  or ≥ 70 % of the clip; ≥ 70 % coverage marks the whole take. 0051 is now
  `accidental` end to end and none of the other 20 clips changed role.

## Mocked / not verified

- **0067 move labels are still wrong** (move score 0.00): the travel_backward /
  pan_left / pan_right ground truth is not produced. The measured scale flow is
  almost flat there and the pans never became moves; needs either gyro or the
  D1b vision step. The horizon detector works on the same clip.
- **0072 `01:46–02:43` is `complex_motion`, ground truth says `travel_forward`**
  ("leaving the tunnel"): the geometric signal in that span is weak (mean scale
  rate ≈ 0), so the current travel rule (sustained scale flow) cannot see it.
- **0081 opening horizon false positives** (−9.6°/−10.9°): the shot is a boat
  interior with no horizon line at all; long canopy/seat edges at 6–12° pass every
  per-frame 2D filter we tried (verified on the actual frames). Documented in the
  `yt-quality` skill; the D1b vision step is expected to veto such segments.
- **`orbit_candidate` and `travel_*` semantics** — geometric proposals only; the
  vision bake-off in D1b is the arbiter.
- **Gyro/telemetry path** — still optical flow only; the raw `djmd` streams are
  untouched for the later `telemetry-parser` upgrade.

## Doru, check this

- `.venv/bin/python scripts/eval_labels.py --ground-truth eval/ground_truth_boat_2026-10-12.yaml --workspace ~/media/yt-test/workspace`
  → expect `TOTAL 0.53 1.00 0.97 1.00 0.69` (was 0.26 before the fixes).
- The six timelines below — do they now match what you filmed? Especially 0051
  (`accidental` everywhere) and 0083 (events at 00:08/00:28/00:35 vs your
  00:10/00:32/00:38).
- 0091 and 0081's opening are labelled `orbit_candidate` (as `pan_left`
  geometrically) — is that the right call for those two shots?
- 0067 is the one clip whose move labels are still wrong (`move 0.00`) — see the
  timeline; tell us whether travel_backward 00:00–00:30 is even visible in the
  image motion or whether it needs gyro/vision.
- `git log --oneline master..d1a-motion-quality` — the D1a-2 commits (eval, audit
  fixes, bob+travel, parallax, horizon, scene events, accidental).

## New timelines — the 6 ground-truth clips

Format: `MM:SS-MM:SS · role · motion label · overlapping moves (annotations) ·
defects (0..1) ; horizon`. `⏱` lines are detected scene events. `bob` is
`platform_bob` period; `parallax` is the homography residual score (0..1).

**clip 0051** — 22 s
- 00:00-00:01 · accidental · static · clean; horizon +0.2°
- 00:01-00:20 · accidental · complex_motion · tilt_down · shake 0.89; horizon -2.7°
- 00:20-00:21 · accidental · static · static, bob 3.7s · clean; horizon n/a

**clip 0067** — 55 s (ground truth: travel_backward, pan_left, pan_right — not detected, see Mocked)
- 00:00-00:35 · move · complex_motion · complex_motion, parallax 0.60 · obstruction 0.27; horizon -0.4°
- 00:35-00:43 · hold · static · clean; horizon -0.4°
- 00:43-00:45 · move · pan_right · shake 1.00; horizon +2.3°
- 00:45-00:54 · hold · static · obstruction 0.29; horizon -1.8°
- 00:54-00:54 · move · pan_left · clean; horizon n/a
- ⏱ 00:22 · subject_enters · activity 0.0000 above floor
- ⏱ 00:27 · activity_rise · rising activity, 0.0004 above start
- ⏱ 00:31 · activity_peak · peak 0.0025 above floor
- ⏱ 00:34 · subject_leaves · activity falls to -0.0002 above floor
- ⏱ 00:43 · subject_enters · activity 0.0117 above floor
- ⏱ 00:45 · activity_peak · peak 0.0128 above floor

**clip 0072** — 176 s
- 00:00-00:08 · move · tilt_down · dolly_out, parallax 1.00, bob 1.6s; tilt_down, parallax 0.71, bob 1.6s · clean; horizon +1.7°
- 00:08-00:10 · hold · static · obstruction 0.25; horizon +0.7°
- 00:10-00:18 · move · dolly_out · complex_motion, parallax 0.99, bob 3.3s; dolly_out, parallax 1.00, bob 3.3s · shake 0.24; horizon +0.6°
- 00:18-00:20 · hold · static · shake 0.61, horizon 0.40; horizon +6.0°
- 00:20-00:25 · move · dolly_in · dolly_in, bob 3.3s; tilt_up, parallax 0.52, bob 3.3s · clean; horizon +1.0°
- 00:25-00:28 · hold · static · obstruction 0.29; horizon +3.9°
- 00:28-02:42 · move · travel_forward · complex_motion, bob 1.5s; complex_motion, parallax 0.52, bob 1.5s; tilt_down, bob 1.5s; travel_forward (as dolly_in), vehicle, parallax 0.57, bob 1.5s · clean; horizon -0.5°
- 02:42-02:45 · hold · static · shake 0.60; horizon -1.4°
- 02:45-02:55 · move · truck_right · truck_right (as pan_right), parallax 0.72 · clean; horizon n/a
- ⏱ 00:00 · activity_start · activity 0.0030 above floor
- ⏱ 00:19 · activity_peak · peak 0.0160 above floor
- ⏱ 01:11 · subject_leaves · activity falls to 0.0004 above floor
- ⏱ 01:22 · subject_enters · activity 0.0001 above floor
- ⏱ 01:25 · activity_peak · peak 0.0131 above floor
- ⏱ 01:27 · subject_leaves · activity falls to 0.0005 above floor
- ⏱ 01:31 · activity_start · activity 0.0001 above floor
- ⏱ 01:44 · activity_peak · peak 0.0164 above floor
- ⏱ 01:47 · subject_leaves · activity falls to -0.0006 above floor
- ⏱ 02:25 · subject_enters · activity 0.0001 above floor
- ⏱ 02:45 · activity_peak · peak 0.0065 above floor

**clip 0081** — 108 s
- 00:00-00:05 · move · orbit_candidate · orbit_candidate (as pan_left), parallax 0.78, bob 2.0s · horizon 0.64 defect; horizon -9.6°
- 00:05-00:09 · hold · static · static, bob 2.0s · horizon 0.73 defect; horizon -10.9°
- 00:09-00:11 · move · complex_motion · clean; horizon -3.6°
- 00:11-00:20 · hold · static · clean; horizon -2.6°
- 00:20-00:24 · move · dolly_in · dolly_in, bob 2.0s · clean; horizon -4.1°
- 00:24-00:30 · hold · static · static, bob 2.0s · clean; horizon -1.8°
- 00:30-00:34 · move · pan_right · pan_right, bob 2.0s · clean; horizon -0.4°
- 00:34-00:35 · hold · static · obstruction 0.26; horizon n/a
- 00:35-00:41 · move · dolly_in · dolly_in, parallax 0.60, bob 2.0s · obstruction 0.27; horizon -0.2°
- 00:41-00:44 · hold · static · static, parallax 0.52, bob 2.0s · clean; horizon -0.4°
- 00:44-00:50 · move · dolly_out · dolly_out, bob 2.0s · clean; horizon -0.3°
- 00:50-00:51 · hold · static · static, bob 2.0s · clean; horizon -0.6°
- 00:51-01:04 · move · dolly_in · dolly_in, parallax 0.54, bob 2.0s · clean; horizon -0.0°
- 01:04-01:14 · hold · static · static, bob 2.0s · clean; horizon +0.6°
- 01:14-01:48 · move · complex_motion · complex_motion, bob 2.0s; complex_motion, parallax 0.82, bob 2.0s · clean; horizon +2.0°
- ⏱ 00:00 · activity_start · activity 0.0029 above floor
- ⏱ 00:03 · activity_peak · peak 0.0068 above floor
- ⏱ 00:06 · subject_leaves · activity falls to 0.0001 above floor

**clip 0083** — 39 s (locked-off starboard; ground truth: hold + 3 events)
- 00:00-00:38 · hold · static · clean; horizon +0.1°
- ⏱ 00:08 · subject_enters · activity 0.0001 above floor
- ⏱ 00:28 · activity_peak · peak 0.0050 above floor
- ⏱ 00:35 · activity_end · activity falls to 0.0010 above floor

**clip 0091** — 16 s (ground truth: orbit 00:05–00:12)
- 00:00-00:02 · hold · static · static, parallax 1.00 · shake 0.37, horizon 0.48; horizon -7.2°
- 00:02-00:15 · move · orbit_candidate · orbit_candidate (as pan_left), parallax 0.61, bob 3.4s; tilt_up, parallax 0.98, bob 3.4s · shake 0.44; horizon +3.8°
- ⏱ 00:00 · activity_start · activity 0.0124 above floor
- ⏱ 00:00 · activity_peak · peak 0.0124 above floor
- ⏱ 00:04 · subject_leaves · activity falls to -0.0006 above floor
- ⏱ 00:12 · subject_enters · activity 0.0205 above floor
- ⏱ 00:14 · activity_peak · peak 0.0250 above floor

---

# D1a first round (pre-checkpoint, 2026-10-12) — historical record

The sections below describe the outputs as they were when Doru reviewed them at
the checkpoint. The per-clip timelines at the end are superseded by the D1a-2
timelines above for the six ground-truth clips; the `move · static` line for clip
0080 is the contradiction the PR #7 audit flagged — D1a-2 forbids role `move` on
a `static` label, so a re-run cannot produce it again.

## Works

- **Budget fixes (PR #4 review)** — `scripts/common/llm.py`: an OpenRouter response
  without `usage.cost` now raises `MissingUsageCost` instead of counting as free;
  every call is appended to a per-project spend ledger (`llm_spend.json` in the
  workspace, via `YT_SPEND_LEDGER`, `spend_ledger` in `config.yaml` or
  `YT_WORKSPACE`) so `llm_budget_usd` caps a whole job across processes. Verified
  with a two-process mock run: second process starts at the ledger total and the
  third call is refused (`.venv/bin/python -m pytest tests/test_llm.py -q` → 40
  passed).
- **`scripts/ingest.py`** — folder scan: duration, fps, resolution, every stream,
  creation time (metadata, else filename) and gyro presence (DJI `djmd` metadata
  stream, single-sample timecode tracks ignored). Verified on all 21 proxies
  (1280x720, 25 fps, gyro absent — proxies lost the metadata stream) **and** on the
  raw folder (21/21 gyro present, `stream 2: codec_tag=djmd handler='DJI meta'`),
  output valid against `schemas/ingest.schema.json`.
- **`scripts/motion.py`** — Lucas-Kanade optical flow at 320 px / 10 fps with a
  partial-affine RANSAC fit: trajectory, motion phases (still / ramps / steady),
  moves labelled with the CameraBench vocabulary, speed, steadiness, steady range
  and cut handles. Whole 21-clip folder in **50 s** on 4 CPU cores. Labels checked
  against actual frame motion (image-shift sign of whips at t≈84/93/104 in clip
  0072 matches `pan_right` / `pan_left` / `pan_right`).
- **`scripts/quality.py`** — measured defects (blur, exposure, shake, obstruction,
  horizon) and a role per segment (`move`, `hold`, `transition_in/out`, `setup`,
  `teardown`, `accidental`) with confidence and evidence. Labels only, never
  deletes: all 688.7 s stay covered by 104 segments. Horizon is measured from
  Canny+Hough edge angles and was cross-checked on real frames (10° measured vs
  11.5° reported on clip 0067, -9° vs -10° on clip 0080).
- **Contracts and skills** — `schemas/ingest.schema.json`, `motion.schema.json`,
  `quality.schema.json` with valid-instance fixtures; skills `yt-ingest`,
  `yt-motion`, `yt-quality`. Test suite: **188 passed** (`.venv/bin/python -m pytest
  -q`), offline, no GPU, no API key.
- **Runtimes on this machine** (4 cores, no GPU): ingest 3 s, motion 50 s, quality
  40 s for the full folder — well inside the "keep analysis fast" budget.

## Mocked / not verified

- **Gyro-based motion** — `motion` uses optical flow only; the proxies carry no
  IMU. The raw files do (`djmd`, seen via `ingest`) and the gyro path is a later
  upgrade.
- **Semantic moves** (orbit, tracking, reveal) and the dolly-vs-zoom split —
  flagged `semantic_check` for the D1b vision step, not decided here.
- **`accidental` / `transition_in` / `transition_out` roles** — logic and synthetic
  tests exist, but the 21 test clips produced none (no junk takes, no clean whip
  transitions at take heads/tails in this set).
- **`OpenRouterClient` against the live API** — still no key on the build machine;
  the missing-`usage.cost` raise and the ledger are covered by injected fakes and
  the mock backend.

## Doru, check this

- `.venv/bin/python -m pytest -q` → expect `188 passed`.
- `cat ~/media/yt-test/workspace/ingest.json | head -40` → 21 clips, all gyro
  `present: false` (proxies) — correct per the filming checklist (RockSteady-off
  originals keep gyro, these proxies did not).
- The per-clip timelines below — do the roles match what you filmed? This is the
  D1a checkpoint: `move` / `hold` / `setup` / `teardown` calls are labels for you
  to confirm, not decisions.
- `ls ~/media/yt-test/workspace/{motion,quality}` → one JSON per clip each;
  `scripts/motion.py` + `scripts/quality.py` re-run skips them (`--force`
  re-analyses).
- `git log --oneline master..d1a-motion-quality` → 6 commits (budget fixes,
  schemas, ingest, motion ×2 rounds, quality ×2 rounds).

## Per-clip timelines

Format: `MM:SS-MM:SS · role · motion label · defects (0..1 severity)`. One line
per segment; segments tile the whole clip — nothing is deleted.

**clip 0051** — 22 s
- 00:00-00:01 · hold · static · clean
- 00:01-00:02 · setup · pan_right · clean
- 00:02-00:04 · move · tilt_down · clean
- 00:04-00:05 · setup · complex_motion · exposure 0.06, shake 1.00
- 00:05-00:17 · move · complex_motion · shake 0.98
- 00:17-00:20 · teardown · complex_motion · shake 1.00
- 00:20-00:22 · move · tilt_up · clean

**clip 0056** — 31 s
- 00:00-00:05 · hold · static · horizon 0.70
- 00:05-00:06 · move · dolly_out · blur 0.01
- 00:06-00:07 · hold · static · blur 0.05
- 00:07-00:31 · move · pan_right · shake 0.28

**clip 0057** — 14 s
- 00:00-00:06 · move · tilt_down · clean
- 00:06-00:09 · hold · static · clean
- 00:09-00:13 · move · complex_motion · clean
- 00:13-00:14 · teardown · pan_left · clean

**clip 0058** — 4 s
- 00:00-00:01 · setup · tilt_up · clean
- 00:01-00:04 · move · complex_motion · clean

**clip 0060** — 30 s
- 00:00-00:11 · move · tilt_up · shake 0.64
- 00:11-00:13 · hold · static · clean
- 00:13-00:14 · move · pan_left · shake 1.00
- 00:14-00:16 · hold · static · horizon 0.65
- 00:16-00:17 · move · pan_right · exposure 0.10, shake 1.00
- 00:17-00:20 · hold · static · clean
- 00:20-00:27 · move · pan_left · clean
- 00:27-00:30 · hold · static · horizon 0.58

**clip 0061** — 14 s
- 00:00-00:01 · setup · tilt_up · clean
- 00:01-00:02 · hold · static · horizon 0.80
- 00:02-00:10 · move · pan_right · clean
- 00:10-00:13 · hold · static · obstruction 0.25
- 00:13-00:13 · teardown · pan_right · shake 0.21
- 00:13-00:14 · move · pan_right · clean

**clip 0063** — 9 s
- 00:00-00:04 · move · dolly_out · obstruction 0.25
- 00:04-00:09 · hold · static · obstruction 0.30

**clip 0064** — 7 s
- 00:00-00:00 · move · pan_left · clean
- 00:00-00:04 · setup · pan_left · shake 0.34
- 00:04-00:04 · move · pan_left · clean
- 00:04-00:06 · hold · static · clean
- 00:06-00:07 · teardown · complex_motion · shake 1.00

**clip 0065** — 20 s
- 00:00-00:18 · hold · static · clean
- 00:18-00:20 · move · complex_motion · clean

**clip 0066** — 16 s
- 00:00-00:01 · hold · static · clean
- 00:01-00:03 · move · tilt_up · clean
- 00:03-00:16 · hold · static · clean

**clip 0067** — 55 s
- 00:00-00:30 · hold · static · obstruction 0.29
- 00:30-00:35 · move · complex_motion · clean
- 00:35-00:44 · hold · static · horizon 1.00
- 00:44-00:46 · move · pan_right · shake 1.00
- 00:46-00:54 · hold · static · obstruction 0.32, horizon 0.91
- 00:54-00:55 · move · pan_left · clean

**clip 0071** — 10 s
- 00:00-00:01 · hold · static · clean
- 00:01-00:09 · move · pan_left · clean
- 00:09-00:10 · teardown · tilt_up · clean

**clip 0072** — 176 s
- 00:00-00:09 · move · tilt_down · clean
- 00:09-00:11 · hold · static · obstruction 0.25, horizon 0.50
- 00:11-00:13 · move · complex_motion · shake 0.20
- 00:13-00:16 · hold · static · horizon 0.80
- 00:16-00:26 · move · dolly_out · shake 0.29
- 00:26-00:28 · hold · static · obstruction 0.29, horizon 0.37
- 00:28-00:30 · move · dolly_in · obstruction 0.33
- 00:30-00:31 · hold · static · obstruction 0.34, horizon 0.45
- 00:31-02:43 · move · dolly_in · clean
- 02:43-02:46 · hold · static · shake 0.60, horizon 0.47
- 02:46-02:47 · move · tilt_up · shake 0.41
- 02:47-02:53 · hold · static · horizon 1.00
- 02:53-02:56 · move · pan_right · clean

**clip 0077** — 17 s
- 00:00-00:05 · move · pan_right · clean
- 00:05-00:08 · hold · static · clean
- 00:08-00:10 · move · pan_right · clean
- 00:10-00:13 · hold · static · horizon 0.47
- 00:13-00:14 · move · pan_right · clean
- 00:14-00:17 · hold · static · horizon 0.64

**clip 0080** — 35 s
- 00:00-00:14 · move · dolly_in · clean
- 00:14-00:15 · hold · static · horizon 0.82
- 00:15-00:17 · move · tilt_up · clean
- 00:17-00:18 · hold · static · clean
- 00:18-00:29 · move · tilt_down · clean
- 00:29-00:32 · hold · static · horizon 1.00
- 00:32-00:34 · move · static · horizon 1.00
- 00:34-00:35 · teardown · tilt_up · clean

**clip 0081** — 108 s
- 00:00-00:12 · move · pan_left · clean
- 00:12-00:13 · hold · static · horizon 0.47
- 00:13-00:16 · move · tilt_up · clean
- 00:16-00:19 · hold · static · clean
- 00:19-00:20 · move · tilt_down · clean
- 00:20-00:22 · hold · static · horizon 0.45
- 00:22-00:52 · move · tilt_down · clean
- 00:52-01:00 · hold · static · clean
- 01:00-01:48 · move · complex_motion · clean

**clip 0082** — 30 s
- 00:00-00:02 · move · tilt_down · clean
- 00:02-00:03 · hold · static · horizon 0.40
- 00:03-00:05 · move · pan_right · clean
- 00:05-00:08 · hold · static · horizon 0.84
- 00:08-00:30 · move · complex_motion · clean

**clip 0083** — 39 s
- 00:00-00:00 · setup · static · clean
- 00:00-00:39 · hold · static · clean

**clip 0084** — 19 s
- 00:00-00:16 · move · complex_motion · clean
- 00:16-00:17 · teardown · pan_right · clean
- 00:17-00:19 · hold · static · horizon 0.38

**clip 0086** — 17 s
- 00:00-00:02 · hold · static · horizon 0.47
- 00:02-00:03 · move · tilt_up · clean
- 00:03-00:17 · hold · static · clean

**clip 0091** — 16 s
- 00:00-00:03 · setup · static · shake 0.37, horizon 1.00
- 00:03-00:13 · move · pan_left · clean
- 00:13-00:16 · teardown · complex_motion · shake 1.00

