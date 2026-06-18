# ARCHITECTURE — yt-pipeline

## Overview

CLI pipeline for YouTube clip processing: transcription → correction → translation → metadata → 9:16 shorts.

```
Export/
  video/      MyClip.mp4           ← input video (1920×1080)
  audio/      MyClip.mp3           ← input audio (Whisper / WhisperX)
  subtitles/
    MyClip.srt                     ← Whisper raw output
    MyClip_RO.srt                  ← manually corrected
    MyClip_EN.srt                  ← translated
  auto/
    MyClip_words.json              ← word timestamps (Whisper or WhisperX)
    Hook_karaoke.ass               ← generated ASS per segment
  shorts/
    Short1-Hook.mp4                ← final output 608×1080
  metadata/
    video_metadata.txt             ← title, description, tags, chapters
```

---

## Scripts

| Script | Input | Output | Tool |
|:---|:---|:---|:---|
| `transcribe.py` *(planned)* | `.mp3` | `.srt` + `_words.json` | Whisper turbo |
| `correct_srt.py` | `.srt` | `_RO.srt` | DeepSeek API |
| `translate_srt.py` | `_RO.srt` | `_EN.srt` | DeepSeek API |
| `analyze_srt.py` | `_RO.srt` + `.mp4` | `video_metadata.txt` | DeepSeek API |
| `shorts_generator.py` | `.mp4` + `.mp3` + `_RO.srt` | `Short[N]-[Name].mp4` | WhisperX + ffmpeg |
| `pipeline.py` *(planned)* | config + Export folder | everything | orchestrator |

---

## Tech stack

| Component | Tool | Notes |
|:---|:---|:---|
| Transcription | `openai-whisper turbo` | installed via pipx |
| Forced alignment | `whisperx` | aligns corrected text to audio |
| Face detection | `opencv` Haar cascades | one static offset per segment, no tracking |
| Subtitle render | `pysubs2` → ASS | PlayRes 608×1080, Arial Bold |
| Video render | `ffmpeg h264_nvenc` | RTX 4070 Laptop, preset p4, cq 23 |
| AI correction / translation / metadata | DeepSeek API | openai-compatible client |
| Package management | `uv` | isolated venv per project |

---

## Design decisions

→ see `DECISIONS.md`

---

## Planned pipeline flow

```
[Export clip]
      ↓
transcribe.py ──→ MyClip.srt + _words.json
      ↓
[Manual SRT review & correction]
      ↓
correct_srt.py ──→ MyClip_RO.srt
      ↓
translate_srt.py ──→ MyClip_EN.srt
      ↓
analyze_srt.py ──→ video_metadata.txt
      ↓
shorts_generator.py ──→ Short[N].mp4
```
