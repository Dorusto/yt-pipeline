# D2-8 — frame mosaic for describe + Shorts subject tracking

Branch `d2-8-mosaic-reframe`, 2026-10-15. OpenCV-first backlog rows: frame
mosaic for describe (PART 1) and Shorts subject tracking (PART 2). Principle
applied throughout: OpenCV measures everything it can for free on every frame;
the vision model is a thin, cheap layer of judgement on top.

## Works

- **`describe.py --frame-mode mosaic` (default)** — frames picked at OpenCV
  scene changes and `motion/<stem>.json` phase boundaries (edge anchors + even
  fill), tiled 6–9 per mosaic with burned-in timestamps, one model image per
  group. `--frame-mode legacy` keeps the old fixed-position separate frames.
  44 tests in `tests/test_describe.py`; mock end-to-end on a synthetic
  workspace; a real mosaic of clip 0081 read back by the vision model (all six
  timestamps correct). Prompt `prompts/describe_segment.md` teaches the grid and
  keys beats off `long: yes|no`.
- **Old vs new measured on the Kotor eval set**
  (`eval/ground_truth_boat_2026-10-12.yaml`, 6 clips = 6.92 min, GLM 5.3 Flash,
  `describe --force` → `index.py` → `eval_labels.py --labels shotlog`):

  | | legacy (old) | mosaic (new) |
  |---|---|---|
  | move | 0.678 | **0.681** |
  | role | 0.996 | 0.996 |
  | horizon | 0.970 | 0.970 |
  | events F1 | 0.667 | 0.667 |
  | model images | 134 | **35** |
  | frames shown | 134 | **216** |
  | content beats | 12 | **15** |
  | **vision cost / minute of footage** | **0.00526 USD/min** (0.03643 USD) | **0.00447 USD/min** (0.03098 USD) |

  The mosaic is not worse on any component and is 15 % cheaper with 61 % more
  frames shown. Workspaces: `~/media/yt-test/ws-d28-legacy/` and
  `~/media/yt-test/ws-d28-mosaic/` (outside the repo).
- **`scripts/reframe.py` (+ `yt-reframe` skill, `schemas/reframe.schema.json`)** —
  crop path JSON (time -> x, y) for any aspect (9:16 default): YuNet face
  detection per sample, TrackerMIL between detections, saliency fallback
  (spectral residual + motion, wake band down-weighted, continuity gate, hold
  instead of chasing waves), EMA smoothing with a speed clamp. Model downloads
  to `~/.cache/yt-pipeline/models/face_detection_yunet_2026may.onnx` (224 KB,
  MIT), never into git. 7 tests; end-to-end on a synthetic clip and on Kotor
  media. Detector/tracker/fallback chosen on measured numbers — see the DECISIONS
  entry "D2-8 reframe stack".
- **`shorts_generator.py` moving crop** — per segment without `x_offset` it runs
  `reframe.py` and renders the crop as a piecewise-linear ffmpeg expression;
  `x_offset` in `shorts_config.yaml` still pins a fixed crop. The Haar path is
  gone (OpenCV 5.0 removed `cv2.CascadeClassifier`, so it was already broken).
  Verified on a 12 s 1080p excerpt of clip 0081: `Short1` moving crop (25 points,
  every one `face`), `Short2` fixed `x_offset: 800`, both 608x1080.
- **Visual checks (vision model on sampled output frames):** the 0081 short —
  subject (man on the boat) with the face fully visible in all four sampled
  tiles; the 0083 boat clip — the motorboat stays inside the crop in all four
  sampled tiles (after weighting motion over appearance in the saliency map).
- Suite: **858 passed** (`-m "not needs_melt and not needs_footage"`), no
  regressions. Sources untouched (originals read-only, md5-checked in earlier
  phases; this phase wrote only into `/tmp` and `~/media/yt-test/ws-d28-*`).

## Mocked / not verified

- **The coder subagent could not run this phase** — its OpenRouter account is out
  of quota (empty replies twice, then "quota is exhausted"). The architect
  implemented D2-8-01/02/03 itself, as in the 2026-10-15 DECISIONS precedent.
- **Object-detector fallback not shipped**: NanoDet (3.6 MB, Apache-2.0) does not
  load on this OpenCV 5.0.0 pip build (DNN graph engine reshape assert, no ONNX
  Runtime in the wheel). Saliency is the fallback; the detector stays the P1
  upgrade. VitTrack measured worse than TrackerMIL on the Kotor orbit clip.
- Long-short stress: the moving-crop expression is capped at 40 keyframes; only
  6 s and 3 s renders were exercised, not a multi-minute short.
- Framing checks are single vision calls on 4-tile mosaics, not a systematic
  framing eval over the whole eval set.

## the owner, check this

1. Numbers: `~/projects/yt-pipeline/.venv/bin/python scripts/eval_labels.py --ground-truth eval/ground_truth_boat_2026-10-12.yaml --workspace ~/media/yt-test/ws-d28-mosaic --labels shotlog` (mosaic) vs the same with `ws-d28-legacy` (old).
2. Watch a moving-crop short: `.venv/bin/python scripts/shorts_generator.py --video ~/media/yt-test/raw/dji_mimo_20261012_124338_0081_1791817211091_video.mp4 --yes` with a `shorts_config.yaml` segment without `x_offset`, then play `shorts/Short1-*.mp4`.
3. Reframe on one of your own clips: `.venv/bin/python scripts/reframe.py --video <clip.mp4> --json` and look at `points[]` in the JSON.
4. The mosaic in the report's numbers: 35 images instead of 134 for the same six clips — confirm the descriptions still read right in `~/media/yt-test/ws-d28-mosaic/describe/`.
5. The coder's OpenRouter account is overdrawn (VIP.aaa, balance −2.16 USDT) — top it up before the next phase or the architect keeps building alone.
