# CLAUDE.md — yt-pipeline

Context and rules for Claude Code when working on this project.

## What this project does

CLI pipeline for YouTube clip processing: transcription → correction → translation → metadata → 9:16 shorts with karaoke subtitles.

## Key files

| File | Role |
|:---|:---|
| `ROADMAP.md` | Milestones and status |
| `ARCHITECTURE.md` | System design, flow, stack |
| `DECISIONS.md` | Technical decision log — read before proposing alternatives |
| `shorts_generator.py` | Main active script |
| `shorts_config.yaml` | Local config (git-ignored) |

## Working rules

1. **Read `DECISIONS.md`** before proposing an alternative approach — it may have already been considered and rejected.
2. **Update `DECISIONS.md`** after any significant architectural decision.
3. **Update `ROADMAP.md`** after completing a milestone or changing priorities.
4. **GitHub commits** → backdate after 18:00, non-round times (e.g. 19:43:17). Repo is public.
5. **Code and documentation** → in English. Comments in code → none (self-documenting code).
6. **`--skip-alignment`** → use only when the JSON is already correct and only ASS + video re-render is needed.

## Do not

- Re-install MediaPipe — OpenCV was chosen intentionally (see DECISIONS.md).
- Run Whisper separately for word timestamps — WhisperX forced alignment is the source of truth.

## File structure

```
Export/
  video/      ← .mp4 input
  audio/      ← .mp3 input
  subtitles/  ← SRT files (raw, corrected RO, translated EN)
  auto/       ← _words.json, _karaoke.ass (auto-generated)
  shorts/     ← final output (generated next to video/)
```

## How to run

```bash
cd ~/Proiecte-AI/YouTube/shorts-generator
.venv/bin/python shorts_generator.py \
  --video ~/Videos/[Clip]/Export/video/[Clip].mp4 \
  --audio ~/Videos/[Clip]/Export/audio/[Clip].mp3 \
  --srt   ~/Videos/[Clip]/Export/subtitles/[Clip]_RO.srt
```
