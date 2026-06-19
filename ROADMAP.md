# ROADMAP — yt-pipeline

## Current status

Pipeline fully functional for end-to-end clip processing. Tested on Lenea clip (June 2026): produced 4 Shorts with karaoke subtitles, per-short metadata, and main video metadata. Ready for the next clip.

---

## Scripts — current state

| Script | Status | What it does |
|:---|:---|:---|
| `correct_srt.py` | ✅ done | Find/replace corrections on raw Whisper SRT |
| `translate_srt.py` | ✅ done | RO→EN translation via DeepSeek API, saves next to video |
| `analyze_srt.py` | ✅ done | Main video metadata + short candidates + per-short metadata (`--shorts-config`) |
| `shorts_generator.py` | ✅ done | 9:16 crop, WhisperX alignment, karaoke ASS, ffmpeg nvenc |
| `transcribe.py` | 🔲 planned | Single Whisper run → SRT + `_words.json` |
| `pipeline.py` | 🔲 planned | Orchestrator for all steps |

---

## Workflow (current — manual)

```
1. Export clip: video + audio from editor
2. whisper audio.mp3 --output_format srt   ← run manually via pipx
3. python correct_srt.py raw.srt           ← fix common errors
4. [Manual SRT review and fix]             ← critical — this is the source of truth
5. python translate_srt.py RO.srt video.mp4
6. python analyze_srt.py RO.srt video.mp4 ← main metadata + short candidates
7. Edit shorts_config.yaml with chosen segments
8. .venv/bin/python shorts_generator.py --video video.mp4
9. python analyze_srt.py RO.srt video.mp4 --shorts-config shorts_config.yaml
10. [Add youtube_url to config after main video upload]
11. Re-run step 9 to refresh all short metadata with the real link
```

---

## Milestone 1 — Repo unification ✅

- [x] `shorts_generator.py` — 9:16 crop, WhisperX alignment, karaoke ASS, ffmpeg nvenc
- [x] GitHub repo named `yt-pipeline`
- [x] All scripts in repo: `correct_srt.py`, `translate_srt.py`, `analyze_srt.py`
- [x] README with complete workflow
- [x] Manual `x_offset` override per segment
- [x] SRT boundary trimming at both start and end
- [x] Face detection logging
- [x] Audio optional — extracted from video if not provided
- [x] All output next to video (not next to SRT)
- [x] `analyze_srt.py --shorts-config` — per-short metadata
- [x] `youtube_url` in config auto-fills short descriptions
- [x] Short candidates saved to `{basename}_shorts_candidates.txt`

## Milestone 2 — Single Whisper run 🔲

- [ ] `transcribe.py` — run Whisper once, save both SRT and `_words.json`
- [ ] `shorts_generator.py` reads existing `_words.json` if present (skip WhisperX)

## Milestone 3 — `pipeline.py` orchestrator 🔲

- [ ] Single command: transcribe → correct → translate → analyze → shorts → metadata
- [ ] `--from-step N` to resume from a specific step
- [ ] Unified config covering all steps

## Milestone 4 — Shorts quality 🔲

- [ ] Font and highlight color configurable from config
- [ ] `LINE_WORDS` configurable per project
- [ ] Quick preview (first 10s) before full render

## Milestone 5 — Upload 🔲

- [ ] Optional: YouTube Data API auto-upload with scheduled publish date

---

## Known issues

- Face detection (Haar cascades) reliable on frontal face, well-lit — use manual `x_offset` otherwise
- `correct_srt.py` misses errors not in `corrections.txt` — always review SRT manually
- Segment names become filenames — avoid spaces (use `-` or `_`)
- `analyze_srt.py` requires absolute paths
