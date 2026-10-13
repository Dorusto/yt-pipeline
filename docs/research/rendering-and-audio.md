---
title: Rendering, web editing and audio generation — research for D2–D6
date: 2026-10-13
tags: [yt-pipeline, research, rendering, web-editor, audio, music]
---

# Rendering, web editing and audio generation

Why this exists: Doru wants to drop the hard dependency on Kdenlive (it ties him to the PC), preview and lightly adjust an edit from the browser, add story graphics automatically (vacations, tutorials, talking-head), and make music and sound effects without paid services and without ComfyUI. This file records the options and the choice. Decision entry: `docs/DECISIONS.md`, 2026-10-13, *`edit.json` is the edit list*.

## 1. Programmatic video engines (graphics, animations, final render)

| Engine | License | How it renders | Browser preview | Fit |
|---|---|---|---|---|
| **Remotion** | Remotion License (source-available). Free for individuals and companies up to 3 people; paid above. The ready-made Timeline component is a paid add-on. | React → headless Chromium → frames → ffmpeg; CPU-bound by default | `@remotion/player` (free) | Hasan's MIT kit (`hassancs91/claude-youtube-editor`: 37 shots, motion kit, screencast engine) is written for it. Not OSI open source. |
| **HyperFrames** (HeyGen) | Apache-2.0, no render fees or revenue thresholds | HTML/CSS + seekable animations (`data-*` timing, tracks, sub-compositions) → headless Chrome + FFmpeg, deterministic | preview loop exists; a drop-in player component is not documented | Built for agents: 21 installable skills, a router skill, and `/remotion-to-hyperframes` to port Remotion compositions. New (public in 2026) — maturity to verify. |
| **Revideo** | MIT | Canvas engine (fork of Motion Canvas), Node `renderVideo()` for server rendering | React `<Player/>` | Closest MIT match to Remotion's features, but small community and the repo moved organisations (`midrender/revideo`); maintenance to verify before choosing. |
| **Motion Canvas** | MIT | Canvas engine with a browser editor; CLI rendering, no documented server API | editor only | Made for hand-animated explainers, not for an automated pipeline. Excluded. |
| **ffmpeg** (direct) | LGPL/GPL | filter graphs from a cut list | — | Fastest for plain cuts, crossfades, speed, captions (ASS), audio mix. No rich graphics. |

**Conclusion.**
- Plain montage (cuts, transitions, music, captions) renders with **ffmpeg straight from `edit.json`** on the server. No browser engine needed, so Mode A does not depend on any of the engines above.
- Graphics and story elements (titles, maps, counters, browser/terminal mockups, charts driven by what is said) go through a **graphics engine behind one interface**. Candidates for the bake-off at the start of D4: Remotion (reuse Hasan's kit, free for Doru) vs HyperFrames (Apache-2.0, agent-native). Revideo stays a fallback if its maintenance checks out.
- Remotion's licence is acceptable today (single creator). The interface keeps the exit open if the channel ever outgrows it.

## 2. Web editor — OpenCut as a candidate

**OpenCut** (`OpenCut-app/OpenCut`, MIT, ~64k stars): a CapCut-style editor in the browser (Next.js, Rust/wgpu compositor compiled to WASM), self-hostable.

- The production editor ("classic", releases v0.1–v0.3 in 2026: masks, keyframes, per-clip effects) lives in a separate `opencut-classic` repo.
- The default branch is a **ground-up rewrite at scaffold level**, with outside contributions paused. Its roadmap lists an *Editor API* (programmatic control of projects and renders) and headless batch rendering, neither shipped.
- No documented project import format today. No mobile app.

**Status: candidate, not chosen.** We cannot write an edit from the director into OpenCut yet. Re-evaluate when the Editor API ships: an `export_opencut` adapter (`edit.json` → OpenCut project) would then give Doru a full manual editor in the browser without building one.

**Meanwhile (D3):** our own light web editor, phone first: play the edit from 720p proxies, reorder / trim / swap a slot, approve / reject / regenerate, then the server renders the final at full quality. Not a general timeline editor — anything heavier goes to the Kdenlive export, or to OpenCut later.

## 3. Music, sound effects and audio clean-up — without ComfyUI

ComfyUI is a Python program: each node loads a model and passes tensors to the next node. We do the same with **standalone Python tools**, one per job, that load the model with its own inference code (or `diffusers` / `transformers`), write a file, and exit. Files on disk replace the wires between nodes. What ComfyUI gives on top is a graph UI and automatic VRAM management; we replace the latter with one rule: **one model loaded at a time, freed when the tool exits** (fits the 24 GB of the RTX 3090).

| Tool | Job | Backends (config, not code) | License status |
|---|---|---|---|
| `audio_clean` | noise and wind reduction, loudness to −14 LUFS | ffmpeg `arnndn` (RNNoise model, CPU) + `loudnorm`; DeepFilterNet later if needed | free; no GPU |
| `music` | background music from a mood/brief + target length + BPM | **YuE2** (full songs, vocals + accompaniment), **ACE-Step 1.5** (MIT; tested in ComfyUI, quality judged weak for songs, kept for instrumentals), **Stable Audio Open** (short instrumental loops) | YuE2 outputs publishable and monetisable (checked in the vault); ACE-Step MIT; Stable Audio Open free under $1M yearly revenue |
| `sfx` | whoosh, pop, click, ambience at transitions and on keywords | 1) own catalogued library (freesound.org CC0), 2) generation with Stable Audio Open (text → sound) to fill gaps, saved back into the library | CC0 / Stable Audio licence |
| `beats` | beats, downbeats, energy of the chosen track | librosa (CPU) | ISC |

**Excluded:** MusicGen and AudioGen (Meta) — weights CC-BY-NC, no monetised use. MMAudio (video → sound) — code MIT but checkpoints CC-BY-NC. HunyuanVideo-Foley — licence excludes the EU, UK and South Korea, outputs included. Revisit if licences change.

Each tool has `--help`, `--json`, skips work already done (cache key = prompt + seed + model + length), and runs on the CPU with a mock backend for tests on the build LXC. The GPU backends run on the 3090 near MVP, as already decided for vision.

## Sources

- Remotion vs Motion Canvas vs Revideo (2026) — https://www.pkgpulse.com/guides/remotion-vs-motion-canvas-vs-revideo-programmatic-video-2026
- Revideo — https://github.com/midrender/revideo, https://docs.re.video/api/renderer/renderVideo
- HyperFrames — https://github.com/heygen-com/hyperframes, https://themenonlab.blog/blog/heygen-hyperframes-video-as-code
- OpenCut — https://github.com/OpenCut-app/OpenCut, https://mer.vin/2026/07/opencut-explained-open-source-capcut-alternative-classic-editor-vs-ground-up-rewrite/
- Video-to-audio licences (MMAudio, HunyuanVideo-Foley) — https://www.creativeainews.com/articles/how-to-add-sound-effects-to-ai-video-2026/
- Music model licences — Doru's vault, `70_ai-youtube-automation-pipeline/NOTE_claude-code-remotion.md` and `20_TODO.md` (chunk 5)
