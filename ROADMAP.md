# ROADMAP — yt-pipeline

## Current status
Pipeline partially functional. `shorts_generator.py` is in production (tested on Lenea clip). Other scripts exist but are not yet integrated into a unified pipeline.

---

## Milestone 1 — Repo unification (in progress)
- [x] `shorts_generator.py` — 9:16 crop, WhisperX alignment, karaoke ASS, ffmpeg nvenc
- [x] GitHub repo (to be renamed `yt-pipeline`)
- [ ] Move `correct_srt.py`, `translate_srt.py`, `analyze_srt.py` into repo
- [ ] Rename repo → `yt-pipeline`
- [ ] Update README with all scripts

## Milestone 2 — Single Whisper run
- [ ] `transcribe.py` — run Whisper once, save both SRT and `_words.json` simultaneously
- [ ] `shorts_generator.py` reads existing `_words.json` if present (skip WhisperX if fresh)

## Milestone 3 — `pipeline.py` orchestrator
- [ ] Single script orchestrating all steps:
  1. `transcribe` → SRT + words JSON
  2. `correct` → corrected SRT (manual review prompt)
  3. `translate` → EN SRT
  4. `analyze` → title, description, chapters, tags
  5. `shorts` → render all segments from config
- [ ] Unified `pipeline_config.yaml`
- [ ] `--from-step N` flag to resume from a specific step

## Milestone 4 — Shorts quality
- [ ] Verify and improve face detection (Haar cascades → better model if needed)
- [ ] Font customizable from config (size, highlight color, font family)
- [ ] `LINE_WORDS` configurable per project
- [ ] Quick preview (10s) before full render

## Milestone 5 — Upload & metadata
- [ ] Integrate `analyze_srt.py` into pipeline → auto `video_metadata.txt`
- [ ] Optional: YouTube Data API auto-upload

---

## Known issues
- Face detection (Haar cascades) may miss or misplace face center — verify per clip
- `correct_srt.py` does not catch all Whisper errors (e.g. "înrăbdare" → "nerăbdare")
- SRT entries straddling segment boundary are trimmed at nearest sentence end (approximation)
