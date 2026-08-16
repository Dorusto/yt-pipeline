# ROADMAP — yt-pipeline

## Current status

Pipeline fully functional for end-to-end clip processing. Tested on Lenea clip (June 2026): produced 4 Shorts with karaoke subtitles, per-short metadata, and main video metadata. Ready for the next clip.

**New (2026-08-16):** rough-cut assembly from raw footage — `kdenlive_from_fragments.py` + `srt_from_fragments.py`. Tested end-to-end on the Dolomiti clip (30 fragments, 9 source files, retimed subtitle import) — see `ARCHITECTURE.md` and `DECISIONS.md` for how it works and why several non-obvious choices were made.

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

## Milestone 2 — Single Whisper run 🔲 ← highest value next

WhisperX forced alignment is the slowest step (~30s per segment). If word timestamps were saved on the first Whisper run, this entire step disappears.

- [ ] `transcribe.py` — run `whisper` once, save both `.srt` and `_words.json` simultaneously
- [ ] `shorts_generator.py` — if `_words.json` already exists for a segment, skip WhisperX entirely
- [ ] Benefit: alignment step goes from ~30s to ~0s per segment

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

## Milestone 6 — Chapter-based clip splitting 🔲

Split a long recording into clips using chapter timestamps — no AI selection needed.

- [ ] Download source video with `yt-dlp`
- [ ] Parse chapter timestamps (manual input or from video description)
- [ ] Cut clips with `ffmpeg` at chapter boundaries

## Milestone 7 — Auto-detect highlight moments 🔲

Reuses most of Milestone 6's code. Claude decides what's worth clipping instead of manual chapter marks.

- [ ] Full transcript of the long source video
- [ ] Claude prompt: identify top moments (hooks, striking numbers, surprising statements)
- [ ] Output: timestamps per moment, feeds into the same cutting pipeline as Milestone 6

## Control / confirmation interface — undecided

No control/confirmation interface (approve-before-upload, edit title before publish, etc.) has been built or committed to. Decide only if an actual need appears once Milestones 5-7 exist — don't build preventively. If needed, extend the existing Majordom Telegram bot rather than building a separate one.

---

## Before next clip — action items

- [ ] **Segment names: no spaces** — `5 reguli AI` created `5 reguli AI_metadata.txt`. Use `5-reguli-AI` or `ReguliAI`. Affects filenames only, title in metadata can be anything.
- [ ] **`corrections.txt` is incomplete** — "înrăbdare" was not caught. Add every missed Whisper error after each clip so the list grows over time.
- [ ] **`youtube_url` workflow** — add URL to `shorts_config.yaml` after the main video is published, then re-run `analyze_srt.py --shorts-config` to refresh all short metadata with the real link.

## Known issues

- Face detection (Haar cascades) reliable on frontal face, well-lit — use manual `x_offset` otherwise
- `correct_srt.py` misses errors not in `corrections.txt` — always review SRT manually
- Segment names become filenames — avoid spaces (use `-` or `_`)
- `analyze_srt.py` requires absolute paths
