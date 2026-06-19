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
| `shorts_generator.py` | Generates 9:16 shorts — main active script |
| `analyze_srt.py` | Main video metadata + per-short metadata (`--shorts-config`) |
| `translate_srt.py` | RO→EN translation — pass `.mp4` as 2nd arg to save in video folder |
| `correct_srt.py` | Find/replace corrections from `corrections.txt` |
| `shorts_config.yaml` | Local config (git-ignored) — segments, optional `x_offset` per segment |

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
cd ~/Proiecte-AI/YouTube/yt-pipeline

# Generate shorts (reads shorts_config.yaml automatically)
.venv/bin/python shorts_generator.py --video ~/Videos/[Clip]/Export/video/[Clip].mp4

# Re-render only (skip WhisperX, reuse existing words JSON)
.venv/bin/python shorts_generator.py --video ... --skip-alignment

# Main video metadata
python analyze_srt.py subtitles/[Clip]_RO.srt video/[Clip].mp4

# Per-short metadata (after generating shorts)
python analyze_srt.py subtitles/[Clip]_RO.srt video/[Clip].mp4 --shorts-config shorts_config.yaml

# Translate SRT (saves next to video)
python translate_srt.py subtitles/[Clip]_RO.srt video/[Clip].mp4
```

## shorts_config.yaml format

```yaml
video: "MyClip.mp4"
audio: "MyClip.mp3"
srt:   "MyClip_RO.srt"

segments:
  - name: "Hook"
    start: "00:00:00"
    end: "00:00:54"
    # no x_offset → auto face detection

  - name: "Piesa"
    start: "00:05:06"
    end: "00:05:46"
    x_offset: 800   # manual override, skips face detection
```
