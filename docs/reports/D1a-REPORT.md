# D1a report — ingest, motion, quality (CPU only)

Branch `d1a-motion-quality`. Scope: the two PR #4 budget fixes, three standalone
analysis tools (`ingest`, `motion`, `quality`) with skills, schemas and tests,
and one run over all 21 test clips (688.7 s of 720p25 DJI proxies, read-only) into
`~/media/yt-test/workspace`. No LLM cost in this phase (D1a is CPU only).

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
  (1280x72025 fps, gyro absent — proxies lost the metadata stream) **and** on the
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

