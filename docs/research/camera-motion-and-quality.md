---
title: Camera motion, take segmentation and footage quality — research for D1
date: 2026-10-12
tags: [yt-pipeline, research, d1, motion, quality]
---

# Camera motion, take segmentation and footage quality

Why this exists: action-cam footage is mostly **continuous takes with no hard cuts**, full of camera moves (pan, push/pull, orbit…) wrapped in **setup and teardown** (press record → move into position → the real move → move back → stop). A good editor keeps the steady middle of each move and throws away the rest, then picks move length by the pacing of the edit. DJI Mimo / LightCut do a crude version of this; we do it properly, with separate tools.

## 1. What the market does

- **DJI LightCut** (maintenance ended September 2026, users pointed to Mimo): scene recognition (aerial, outdoor, cycling, diving, skiing, travel, party, food, daily life) and "smart trimming" that prefers **smooth camera movements, people and smiling faces**. Nothing is published about rejecting bad footage or about move types.
- **GoPro Quik / DJI Mimo**: best-shot pick + beat sync + templates. Move type and junk trimming are not exposed.
- **Opus Clip / Descript**: talking-head only, no camera-motion awareness.

Gap: nobody exposes *which* move happened, *where it is usable*, and *why a segment was rejected*. That is our differentiator.

## 2. A shared vocabulary for camera motion — CameraBench

CameraBench (NeurIPS 2025, ~3,000 videos annotated with cinematographers) defines the taxonomy we adopt as the label set:

- **Motion type:** no-motion, minor-motion, simple-motion, complex-motion
- **Steadiness:** static, no-shaking, minimal-shaking, unsteady, very-unsteady
- **Translation:** dolly in/out, pedestal up/down, truck left/right
- **Rotation:** pan left/right, tilt up/down, roll CW/CCW
- **Intrinsic:** zoom in/out
- **Object-centric:** arc (orbit around a subject), tail/lead/side/aerial tracking, tilt/pan/arc tracking, framing grows/shrinks
- **Other:** speed (slow/regular/fast), dolly-zoom, motion-blur, scene movement (static/mostly-static/dynamic)

Key finding that decides our architecture: **geometric methods (structure-from-motion, optical flow) do well on geometric primitives (pan, tilt, roll, dolly, zoom, shake) but fail on semantic ones; vision-language models are the opposite** (orbit, tracking, "reveal"). So we use both: cheap geometry on CPU for every frame, a VLM only where semantics matter.

They released Qwen2.5-VL models fine-tuned for camera motion (`chancharikm/qwen2.5-vl-7b-cam-motion`, also 32B/72B), used as yes/no scorers per label. The 7B fits the RTX 3090 — a strong local option for orbit/tracking later.

Links: paper arXiv 2504.15376, code github.com/sy77777en/CameraBench, dataset huggingface.co/datasets/syCen/CameraBench.

## 3. Measuring motion — the geometric part (CPU, OpenCV)

Standard approach, no ML needed:

1. Track features between consecutive frames (sparse Lucas–Kanade on good-features-to-track, at reduced resolution, e.g. 320 px wide, sampled at 10–15 fps).
2. Fit a global transform per frame pair with RANSAC (`estimateAffinePartial2D`, or a homography): gives **dx, dy (pan/tilt or truck/pedestal), scale (dolly/zoom), rotation (roll)**, plus inlier ratio (how much of the frame moves together).
3. Build the camera **trajectory** over time; smooth it.
4. **Shake** = energy of the high-frequency residual (trajectory minus smoothed trajectory). **Speed** = magnitude of the smoothed velocity.
5. Classify each window into the CameraBench geometric labels from the dominant component and its sign.

Limits: dolly vs zoom and orbit vs pan+truck are ambiguous from 2D flow alone → those go to the semantic step.

**Better source when available — the gyro.** Gyroflow reads the IMU data that the DJI Osmo Action 4 writes into its MP4, but only when in-camera stabilization (RockSteady) is off and FOV is Wide; stabilization on means no gyro data is recorded. Files exported through the Mimo app (`dji_mimo_*_video.mp4`) most likely lose it. Practical consequence: for future shoots, keep the original SD-card files; `ingest` should detect whether gyro data is present and `motion` should prefer it over optical flow.

## 4. Splitting continuous takes — motion phases

Hard cuts: PySceneDetect (content detector) is enough for edited material; TransNetV2 (neural shot-boundary detector) is the upgrade if PySceneDetect misses soft transitions. For raw action-cam takes hard cuts are rare, so the real segmentation is **by motion phase**:

- From the smoothed velocity and acceleration curves, split each take into phases: **still → ramp-up → steady move → ramp-down → still**, and also split where the dominant motion type changes (pan becomes tilt, orbit stops).
- A **usable move** = a steady phase of one dominant motion, low shake, minimum length (e.g. ≥ 1.5 s).
- **Handles:** keep 5–10 frames of ramp at each end so a cut does not start mid-acceleration.
- **Setup / teardown (likely, not certain):** short, irregular, fast or high-shake phases at the head and tail of a take are labelled `setup` / `teardown`. A clean, fast, single-direction rotation at the head or tail is labelled `transition_in` / `transition_out` instead — it may be half of a whip-pan or roll transition. Nothing is deleted; roles and defects are labels (see `docs/guides/shot-grammar.md`, section 1).

## 5. Rejecting bad footage — quality signals

Per segment, all cheap on CPU:

| Signal | Method | Rejects |
|---|---|---|
| Blur | variance of the Laplacian on sampled frames (relative to the clip's own median, not an absolute threshold) | out of focus, motion blur |
| Shake | high-frequency trajectory residual (section 3) | unsteady handheld, bumps |
| Exposure | luminance histogram: % clipped highs / crushed lows, mean | blown sky, dark frames |
| Obstruction | large low-contrast or static region across frames | water drops, finger, lens flare, pocket |
| Too fast | speed above a threshold with blur | whip moves that are not intended |
| Duplicates | frame embeddings (later) | ten near-identical takes |

Learned score (optional, later): **DOVER-Mobile** (~10M parameters, runs on CPU, reported ~1.4 s per video) gives separate technical and aesthetic quality; useful to rank among the "keep" segments, not to decide keep/discard.

Defects are measured values, kept separate from intent: blur during a whip pan is intended, blur on a hold is a defect. Nothing is deleted — the review UI shows "setup? blur 0.21" and the owner or the director decides.

## 6. Pacing — how long a move should be on screen

Editing grammar the director (D2) will apply, recorded here so motion output carries what it needs:

- Relaxed / contemplative: long holds, 4–8 s or more per move; energetic: 1–3 s.
- With music: segment boundaries on beats or bars; segment length = whole beats.
- Cut *within* the steady phase, never during ramps; prefer cutting on motion (pan right into pan right reads as continuous).
- Speed ramps and effects come later (P1).

So `motion` must output, per usable move: start/end of the steady phase, the handles, the dominant type, speed and steadiness — the director then picks how much of it to use.

## 7. Resulting tool split for D1 (each standalone, own schema, own skill)

| Tool | Input | Output | Needs |
|---|---|---|---|
| `ingest` | folder of clips | `ingest.json`: per clip duration, fps, resolution, streams, creation time, gyro present | ffprobe |
| `motion` | one clip | `motion/<clip>.json`: per-frame trajectory summary, phases, labelled moves with steady range + handles | OpenCV (CPU) |
| `quality` | one clip (+ its motion file if present) | `quality/<clip>.json`: per segment measured defects (blur, exposure, shake, obstruction, horizon) and a role (`move`, `hold`, `transition_in/out`, `setup`, `teardown`, `accidental`) with confidence | OpenCV (CPU) |
| `describe` | clip + kept segments | `describe/<clip>.json`: VLM description, semantic motion (orbit, tracking, reveal), subject, scene | vision model via `scripts/common/llm.py` |
| `index` | the files above | `shotlog.json`: one row per segment, merged, scored | none |

Order matters for cost: `describe` skips segments labelled `setup`, `teardown` or `accidental` with high confidence, so obvious junk never costs a vision call; uncertain ones are still described.

Each tool improves on its own later: `motion` can swap optical flow for gyro data; `describe` can swap the cloud model for the CameraBench 7B on the 3090; `quality` can add DOVER — without touching the others, because they only meet in the JSON files.

## Sources

- CameraBench — https://arxiv.org/abs/2504.15376, https://linzhiqiu.github.io/papers/camerabench, https://github.com/sy77777en/CameraBench
- DOVER — https://github.com/vqassessment/DOVER
- DJI LightCut — https://www.dji.com/lightcut
- Gyroflow and DJI cameras — https://gurubase.io/g/gyroflow/using-dji-cameras-with-gyroflow-for-stabilization, https://github.com/gyroflow/gyroflow
- TransNetV2 — https://github.com/soCzech/TransNetV2
