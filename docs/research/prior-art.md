---
title: yt-pipeline — Prior art research
date: 2026-10-12
tags: [yt-pipeline, research, video, ai]
---

# yt-pipeline — Prior art research (open source + market)

Goal: find what to **reuse**, what to **copy as a pattern**, and what to **avoid**, before building.
Sorted by relevance to our two modes: **A = action/travel montage**, **B = talking-head / YouTube**.

---

## 1. TL;DR — what changes in our plan

1. **Director = multi-agent loop, not one prompt.** CutClaw splits it into Screenwriter → Editor → Reviewer and validates quality before render. Adopt this.
2. **Scoring = multi-signal weighted formula**, not "LLM says so". ElevenClip combines vision (40%), audio energy (35%) and text (25%). Adopt, tune weights per mode.
3. **Every clip carries score + hook + reason** (SamurAIGPT). Makes the review UI fast and explainable.
4. **Reuse, don't rebuild:** auto-editor (silence/motion cuts), ClipsAI (speaker-aware 9:16 reframe), PySceneDetect, WhisperX, Lighthouse/TimeLens2 (text → moment search).
5. **VLM choice:** Qwen3-VL family has native timestamp grounding; 8B fits the 3090. Ask the model for timestamps directly instead of only captioning frames.
6. **Audience signal for Mode B:** YouTube's "Most replayed" heatmap on already-published videos is a free, real ground truth for picking shorts.
7. **Analyze once, edit many:** every serious project caches deconstruction; re-edits must be cheap.

---

## 2. Open-source projects

### 2.1 AI-Youtube-Shorts-Generator (SamurAIGPT / Anil-matcha) — Mode B
- **What:** Opus Clip alternative. Long YouTube video → ranked 9:16 shorts. MIT, ~5.3k stars.
- **Pipeline:** download (yt-dlp) → transcribe (faster-whisper, local or API) → **LLM classifies content type** (podcast, tutorial, vlog…) to tune the prompt → chunk videos > 30 min into 20-min windows with 60 s overlap → LLM ranks candidates 0–100 against a "virality framework" → **dedupe: >50% overlap keeps the higher score** → top-N → vertical crop (OpenCV face tracking + smoothing).
- **Virality criteria:** hooks, emotional peaks, strong opinions, revelations, conflict, quotable lines, story peaks, practical value.
- **Output per clip:** start/end, score, title, **hook sentence**, **one-line reason**. Full JSON dump (`--output-json`).
- **Take:** content-type detection, chunking + overlap, dedupe rule, score/hook/reason schema, JSON-first output, editable criteria file.
- **Avoid:** transcript-only — blind to visuals. Default path depends on a paid API (MuAPI); only the local mode is relevant.

### 2.2 ElevenClip AI (AMD hackathon 2026) — Mode B (+A)
- **What:** multimodal highlight clipper. MIT. Hackathon project; the HF Space currently shows a runtime error, so treat it as **design reference, not a dependency**.
- **Pipeline:** ffmpeg audio extract → **in parallel:** PySceneDetect (CPU) + Whisper (GPU) → **3 frames per scene at 20/50/80%** → Qwen2.5-VL-7B gets frames + transcript per scene, **all scenes sent concurrently to vLLM** → JSON per scene: excitement, tiktok_potential, face_bbox, emotion, action_type, humor, reason.
- **Scoring:** `final = 0.40·vision + 0.35·audio_energy(RMS, librosa) + 0.25·text_keywords`, with `vision = 0.5·excitement + 0.3·potential + 0.2·humor`. Top-N non-overlapping, min 30 s gap.
- **HRE mode:** AI writes a per-segment edit plan — zoom direction/speed (ffmpeg zoompan), caption mode (word/phrase/sentence), caption position, color/emphasis by energy, emoji.
- **Stack:** FastAPI + WebSocket progress, Next.js + Tailwind, pysubs2 (ASS captions, karaoke/pop/typewriter), vLLM. Dev mode runs with stubs when no GPU.
- **Take:** frame sampling rule, structured VLM output schema, weighted fusion formula, concurrent VLM calls via vLLM, WebSocket progress, stub mode, per-segment edit plan idea, ASS caption engine.
- **Note:** "ElevenLabs" is a different company (TTS / voice). Not needed for v1; possibly later for voice-over.

### 2.3 CutClaw (GVCLab + Tencent ARC, paper arXiv 2603.29664) — Mode A ⭐ closest match
- **What:** agentic editing of **hours-long footage synced to music**, driven by one text instruction. ~1k stars. Streamlit UI + CLI.
- **Pipeline:** deconstruct footage into structured captions (sampled at 2 fps) + parse music (**downbeat, pitch, mel energy**) → **Screenwriter + Editor + Reviewer agents** produce `shot_plan` (story/structure) and `shot_point` (exact timestamps) → quality validation → render with crop ratio (e.g. 9:16).
- **Music segmentation:** beat segments 3–5 s; total shot count configurable.
- **Models:** any via LiteLLM — video model, audio model, agent model chosen separately. Local ASR option (whisper.cpp).
- **Their own roadmap (= gaps for us):** cheaper mode that reads only relevant footage, mixing talking-head with b-roll, Claude Code MCP support.
- **Take:** two-artifact plan (`shot_plan` → `shot_point`), the 3-agent loop with a reviewer, music features beyond beats, caching of deconstruction, LiteLLM-style model routing, dry-run before render.
- **Avoid:** cloud API default (slow, costly with many concurrent calls); demo targets movies, not action-cam.

### 2.4 FunClip (Alibaba ModelScope) — Mode B
- **What:** local ASR + LLM clipping tool, Gradio UI, ~5.5k stars.
- **Pattern:** two-stage LLM — (1) "smart paragraph selection" combining prompt + SRT, (2) extract timestamps and cut. Supports **speaker-based clipping** (CAM++ diarization) and multi-segment clipping, outputs SRT per clip.
- **Take:** clip by speaker, SRT output per clip, two-stage select→cut.
- **Avoid:** ASR is Chinese-first; we use WhisperX.

### 2.5 ClipsAI — Mode B (reframe)
- **What:** Python library, MIT. Transcript-based clip finding + **dynamic 16:9 → 9:16 resize that follows the current speaker**.
- **Reframe internals:** speaker diarization (Pyannote, needs HF token) + scene changes (PySceneDetect) + face detection (MTCNN / MediaPipe) + smoothing.
- **Known issue:** speaker-switch cuts can look glitchy in previews.
- **Take:** candidate **dependency** for reframe (ADR in M5); or copy the diarization+face+scene fusion.

### 2.6 auto-editor (WyattBlue) — Mode B (+A)
- **What:** mature CLI, **public domain**. Cuts by audio loudness **and/or motion**, with margins; can speed up instead of cutting; exports timelines to Premiere and Final Cut Pro.
- **Known pitfall:** cuts word endings if threshold/margin is wrong; users denoise first, then cut.
- **Take:** use as **dependency** for silence/motion pass (or replace our ffmpeg silence logic). Rule for us: denoise → cut → normalize, with margin ≥ 0.2–0.4 s. Motion threshold useful for dropping static action-cam footage.

### 2.7 Lighthouse (LINE / LY Corp) — search
- **What:** library for **video moment retrieval + highlight detection** (Moment-DETR, QD-DETR, CG-DETR…), plus audio moment retrieval. Apache 2.0. Gradio demo plots a saliency curve per second.
- **Take:** text query → moments + per-second saliency curve. Good for "find the serpentines at sunset" and as an extra signal in the score.
- **Caveat:** trained on QVHighlights (YouTube vlogs/news); test on action-cam footage before trusting.

### 2.8 Twitch / livestream clippers — concept only
- autoclipper (FastAPI + Celery + React): uses **audience-retention data, chat spikes, timestamped comments**.
- clippy: chat spike detection → LLM classifies the moment → auto-clip, with an **LLM budget cap**.
- **Take:** audience signals beat model guesses when they exist; budget cap on LLM spend is a good guardrail.

### 2.9 Oh My Cassette — agent integration pattern
- Claude Code / Codex / OpenCode plugin + local MCP server: chat brief → agent watches clips, picks shots, beat-syncs, renders. Editing runs on their cloud.
- **Take:** expose our pipeline as **MCP tools / Claude Code skill** later, so Doru can edit from chat ("make a 30 s reel from ./footage").

---

## 3. Models worth evaluating (ADRs in M1)

| Need | Candidates | Note |
|---|---|---|
| Visual understanding | **Qwen3-VL 8B** (dense), Qwen3-VL 30B-A3B (MoE, quantized), Qwen2.5-VL 7B (fallback) | Qwen3-VL has explicit textual timestamp alignment for temporal grounding; 8B fits 24 GB |
| Text → moment | **TimeLens2-8B** (built on Qwen3-VL-8B, returns `[start,end]` intervals), Lighthouse | Natural-language search in footage |
| ASR | WhisperX (word-level), faster-whisper | Already in Doru's stack |
| Shots | PySceneDetect | Used by ElevenClip, ClipsAI |
| Music | librosa (beats), downbeat + energy à la CutClaw | |
| Embeddings | SigLIP / CLIP | dedup + search |
| Director | Claude (default), local LLM via LiteLLM-style router | |

---

## 4. Market leaders — features that matter (recap)

- **Opus Clip:** sortable 0–100 virality score, object/speaker-tracking reframe, animated captions, AI hook, B-roll.
- **Descript:** text-based editing, Underlord agent (filler words, bad takes, B-roll, social clips), Studio Sound, titles/descriptions/timestamps.
- **GoPro Quik / DJI Mimo:** best-shot pick, beat sync, themes, speed tool, frame grab, event grouping.
- **BIGVU:** brief-driven ("what should the clip communicate") instead of a virality number.
- Independent testing of Opus Clip: a large share of AI picks get discarded → **review loop speed > first-pick accuracy**.

---

## 5. Our differentiators (nobody does all of these)

1. **Both modes in one engine**, sharing one cached index.
2. **Action-cam aware:** quality filters (blur/shake/exposure/wind), chronology, optional GPS.
3. **Local-first on own GPUs**, no per-minute pricing.
4. **Kdenlive round-trip** via the existing native `.kdenlive` generator (`kdenlive_from_fragments.py`) — AI draft, human polish.
5. **Explainable picks:** score + reason per clip, and a reviewer agent that critiques before render.
6. **Own audience signal:** YouTube "Most replayed" + own analytics feed back into scoring for shorts.
7. **Agent-native:** CLI → web UI → MCP / Claude Code skill.

---

## Sources
- https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator
- https://huggingface.co/spaces/lablab-ai-amd-developer-hackathon/ElevenClip-AI
- https://github.com/GVCLab/CutClaw
- https://github.com/modelscope/FunClip
- https://github.com/ClipsAI/clipsai — https://clipsai.com/references/resize
- https://github.com/WyattBlue/auto-editor
- https://github.com/line/lighthouse
- https://github.com/topics/highlight-detection
- https://github.com/hamzaskewl/clippy
- https://glama.ai/mcp/servers/Cassette-Editor/oh-my-cassette
- https://github.com/QwenLM/Qwen3-VL — https://arxiv.org/pdf/2511.21631v1
- https://featherless.ai/models/MCG-NJU/TimeLens2-8B
- https://www.opus.pro/mcp/inspiration/clip-the-exact-moments-your-viewers-rewatch-most
- https://techsy.io/en/blog/opusclip-vs-vizard
- https://bigvu.tv/blog/opus-clip-tested-2026-where-ai-wins-40-percent-discard/
