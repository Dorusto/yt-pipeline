# ARCHITECTURE — yt-pipeline

## Overview

CLI pipeline for YouTube clip processing: transcription → correction → translation → metadata → 9:16 shorts.

```
Export/
  video/
    MyClip.mp4                     ← input video (1920×1080)
    MyClip_RO_video_metadata.txt   ← main video: title, description, chapters, tags
    MyClip_EN.srt                  ← translated subtitle (for future EN shorts)
    auto/
      {name}_words.json            ← word timestamps per segment (WhisperX)
      {name}_karaoke.ass           ← karaoke ASS per segment
    shorts/
      Short1-Hook.mp4              ← final 608×1080 short
      Hook_metadata.txt            ← title, description, tags for this short
  audio/
    MyClip.mp3                     ← input audio
  subtitles/
    MyClip.srt                     ← Whisper raw output
    MyClip_RO.srt                  ← manually corrected (single source of truth)
```

---

## Scripts

| Script | Input | Output | Tool |
|:---|:---|:---|:---|
| `transcribe.py` *(planned)* | `.mp3` | `.srt` + `_words.json` | Whisper turbo |
| `correct_srt.py` | `.srt` | `_RO.srt` | DeepSeek API |
| `translate_srt.py` | `_RO.srt` | `_EN.srt` | DeepSeek API |
| `analyze_srt.py` | `_RO.srt` + `.mp4` | `video_metadata.txt` + `{name}_metadata.txt` per short | DeepSeek API |
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

## Rough-cut assembly: how the generated `.kdenlive` file works

A `.kdenlive` file is plain MLT XML (the same format the `melt` CLI tool consumes — Kdenlive is a GUI on top of the MLT framework). Structure used by `kdenlive_from_fragments.py`:

```
<mlt>
 <profile .../>                       one profile block, matches source fps/resolution
 <chain id="chain1" resource="clip1.mp4" .../>   one <chain> per unique source file
 <chain id="chain2" resource="clip2.mp4" .../>
 <playlist id="main_bin">              project bin — lists every chain once
 <playlist id="playlist_a2"/>          empty A2 (matches default Kdenlive layout)
 <playlist id="playlist_audio">        A1 — one <entry> per fragment (audio side)
 <playlist id="playlist0">             V1 — one <entry> per fragment (video side)
 <playlist id="playlist_v2"/>          empty V2
 <tractor id="tractor0">               the sequence: track order = XML order
  <track producer="playlist_a2" hide="video"/>
  <track producer="playlist_audio" hide="video"/>
  <track producer="playlist0" hide="audio"/>
  <track producer="playlist_v2" hide="audio"/>
 </tractor>
</mlt>
```

Each fragment becomes one `<entry in=".." out=".." producer="chainN">` — reusing the same `<chain>` for every fragment cut from that file (Kdenlive/MLT natively supports multiple entries with different in/out against one producer, no need for one producer per cut).

**Video and audio are two separate tracks referencing the same chains**, not one combined AV track — see `DECISIONS.md` for why this is required (not optional) for Kdenlive to play sound.

## Subtitle retiming (`srt_from_fragments.py`)

Independent of the `.kdenlive` file — reads the same `fragments.yaml`, finds every raw-SRT line whose time range overlaps a used fragment, clips it to the fragment boundary, and remaps it to `cumulative_duration_of_prior_fragments + (line_start - fragment_in)`. Output is a plain `.srt`, imported into Kdenlive the normal way (`Project → Subtitles → Import Subtitle File`) — no attempt to hand-generate Kdenlive's native subtitle XML structure (see `DECISIONS.md`).

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
      ↓
analyze_srt.py --shorts-config ──→ {name}_metadata.txt per short
```
