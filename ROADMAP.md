# ROADMAP — yt-pipeline

## Current status
Pipeline partially functional. `shorts_generator.py` is in production (tested on Lenea clip). Other scripts exist but are not yet integrated into a unified pipeline.

---

## Milestone 1 — Repo unification (complete)
- [x] `shorts_generator.py` — 9:16 crop, WhisperX alignment, karaoke ASS, ffmpeg nvenc
- [x] GitHub repo renamed to `yt-pipeline`
- [x] `correct_srt.py`, `translate_srt.py`, `analyze_srt.py` added to repo
- [x] README updated with all scripts
- [x] Manual `x_offset` override per segment in `shorts_config.yaml`
- [x] SRT boundary trimming at both start and end of segment
- [x] Face detection logging (detected/sampled frames)
- [x] All output files saved next to video (not next to SRT)
- [x] `analyze_srt.py --shorts-config` — per-short metadata generation

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
- [x] `analyze_srt.py` generates `video_metadata.txt` (main video) and `{name}_metadata.txt` (per short)
- [ ] Optional: YouTube Data API auto-upload

---

## Known issues
- Face detection (Haar cascades) may miss or misplace face center — use manual `x_offset` per segment in config if needed
- `correct_srt.py` does not catch all Whisper errors — always review SRT manually before running shorts_generator
- SRT boundary trimming at sentence punctuation is an approximation — verify output on segments that straddle boundaries
