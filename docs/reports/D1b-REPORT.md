# D1b — describe + index → shotlog, vision bake-off

Date: 2026-10-13. Branch `d1b-describe-index`. **⛔ Doru checkpoint.**

Scope delivered: `motion`/`quality` emit confidence per label and `uncertain`
when signals disagree or large self-moving regions dominate; `describe`
adjudicates every uncertain segment behind a `VisionModel` interface; vision
bake-off DeepSeek V4.1 Flash vs GLM 5.3 Flash on 5 varied clips; visual
similarity between segments (image embeddings, CPU); `index` → `shotlog.json`
with the fused content score; director update (content score, no near
duplicates, pacing shot lengths); re-rendered recap with the S1 brief.

## Recap comparison (S1 vs D1b)

Brief: *about 90 seconds, chronological, calm start, more energy towards the
end, ambient sound only*. Source: the same 21 DJI proxies.

| | S1 (`recap_720p.mp4`) | D1b (`recap_d1b_720p.mp4`) |
|---|---|---|
| Length / slots | 84.9 s / 18 slots | **81.2 s / 28 slots** |
| Selection signal | labels only (role, steadiness, defects) | **shotlog fused score** 0.55 vision + 0.45 labels, verdict wins on uncertain |
| Descriptions | none (`description: null` everywhere) | **GLM description on every slot** |
| Best moments | cave 0081, tunnel 0072, orbit 0091, approaching boat 0083 **missing** | **tunnel (v13, hero, 0.90) and cave (v16–v23, heroes) present**; 0091 orbit (v28) and 0083 (v26) in but not flagged hero |
| Near-duplicates | several, sometimes after a gap | **0 adjacent similar pairs** (MobileNet pairs ≥ 0.90 refused) |
| Shot lengths | uneven (2.1–12 s) | calm 5–6 s → energy 2.5 s, all within 2–12 s and inside the steady ranges |
| Wake shot 0067 | missing (labelled `hold · static`) | **present as `travel_backward by verdict`** (v8) |
| LLM spend | 0.00 USD | **0.13 USD** of the 3.00 USD phase budget |

Eval on the six ground-truth clips is unchanged (0.66 overall — move 0.53,
role 1.00, horizon 0.97, events 0.67): D1b added flags and verdicts, it did
not retune the geometry.

## Works (verified in D1b)

- **`motion`/`quality` confidence + `uncertain`** — per-sample radial flow vs
  affine scale, residual share for self-moving regions, the parallax band;
  `uncertain_reasons` ∈ scale_vs_radial_flow / parallax_vs_rotation /
  self_moving_regions / mixed_motion_signals (+ role_signals_disagree /
  motion_uncertain on quality). Verified by 11 + 8 unit tests and on the real
  21 clips: 0067's wake phase 0–30 s is `uncertain` with both wake reasons,
  0083's locked-off hold stays confident (`.venv/bin/python -m pytest -q`
  320 green at that point).
- **`describe` + `VisionModel`** — `scripts/common/vision.py` (openrouter +
  mock, budget + spend ledger), `prompts/describe_segment.md`,
  `schemas/describe.schema.json`, `scripts/describe.py` (3 frames per segment
  in time order with timestamps + the motion label, structured JSON, a verdict
  for every uncertain segment, skip on confident accidental/setup/teardown,
  frames never on disk). 18 tool tests + 13 interface tests; real runs on all
  21 clips (GLM, 88 segments, 2 flaky answers recovered by the parse retry).
- **Vision bake-off** — `docs/reports/D1b-vision-bakeoff.md`: GLM 32/32 parse
  rate vs DeepSeek 30/32, 9x cheaper (0.0066 vs 0.0590 USD / 32 segments),
  correct verdicts on the wake and the two 0081 cave adjudications, hero flags
  on the tunnel and cave. **Default vision model = `z-ai/glm-5.3-flash`**
  (DECISIONS 2026-10-13).
- **`similarity`** — MobileNetV2 ONNX through `cv2.dnn` (no new dependency,
  14 MB model cached outside the repo) with a histogram backend as the offline
  fallback. Same-scene vs cross-scene gap measured at 0.232 vs 0.122
  (DECISIONS). 10 tests; real run: 90 segments, 109 pairs at ≥ 0.90.
- **`index` → `shotlog.json`** — fused score 0.55 vision + 0.45 labels
  (missing vision gets a neutral 0.5 prior), hook + reason per shot, verdict
  wins on uncertain (`label_source: "verdict"`), `similar_to`, `eligible`,
  overlap suppression. 25 tests; real run on the 21-clip folder: top shots are
  the cave and tunnel heroes (0080/s1 0.91, 0072/s7 0.90, 0081/s7 0.86).
- **Director update** — ranks by the shotlog score, fills `description`, no
  two similar shots (never back to back; `--allow-similar` relaxes to
  adjacency only), pacing target lengths (`--calm-slot-s` 6.0 →
  `--energy-slot-s` 2.5) clamped to min/max inside the steady range. 410
  tests green; `edit.json` validates; 0 adjacent similar pairs verified on the
  real edit.
- **Re-render** — `/root/media/yt-test/workspace/recap_d1b_720p.mp4`, 81.2 s,
  1280x720, 28 slots, ambient audio. Frames at 3/20/40/55/70/79 s checked:
  wake + cruise ship opener, tunnel exit (t=40, missing in S1), cave approach
  (t=55, missing in S1), cliff pass (t=70).

## Mocked / not verified

- **Audio and text components of the shotlog score** — schema slots exist with
  weight 0; they fill at D2 (RMS/wind) and D4 (transcript).
- **Local vision backend** (Qwen3.8 on the 3090) — `VisionModel` accepts it,
  never run here (no GPU on the build LXC).
- **`--allow-similar` in a real edit** — unit-tested; the checkpoint recap runs
  with the default (no two similar).
- **DeepSeek as `vision_fallback`** — measured in the bake-off at 8000 output
  tokens; kept as fallback only.
- **Per-mode score weights** (vision/audio/text per content type) — D2 router
  scope; today one weight pair for everything.

## Doru, check this

1. Watch `~/media/yt-test/workspace/recap_d1b_720p.mp4` (81 s) next to
   `recap_720p.mp4` (S1, 85 s) — are the cave and tunnel moments now where you
   wanted them, and is the pacing (long calm open, 2.5 s energy tail) right?
2. `.venv/bin/python scripts/index.py --workspace ~/media/yt-test/workspace --force --json`
   then look at `~/media/yt-test/workspace/shotlog.json` — top shots by
   `score`: do you agree with the top 10? 0083's approaching boat and 0091's
   orbit are not flagged hero by GLM (interest 48/60) — wrong or honest?
3. `~/media/yt-test/workspace/describe/dji_mimo_20261012_115636_0067_*.json`
   segment s1 — the verdict is `travel_backward` ("the shores converge toward
   a vanishing point; the wake is not camera flow"). This is the case that
   motivated the whole uncertainty work — is the description right?
4. `docs/reports/D1b-vision-bakeoff.md` — the GLM vs DeepSeek side-by-side and
   the decision to make GLM the default. DeepSeek fails on the wake segment;
   agree?
5. `.venv/bin/python scripts/eval_labels.py --ground-truth eval/ground_truth_boat_2026-10-12.yaml --workspace ~/media/yt-test/workspace`
   — unchanged at 0.66. The known issues list (0067 move 0.00, 0081 move 0.26,
   0072 events) is the D2/D3 backlog unless you want geometry fixed first.
