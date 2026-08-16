# yt-pipeline

CLI pipeline for processing YouTube clips: transcription → correction → translation → metadata → 9:16 shorts with karaoke subtitles.

---

## Workflow

```
1. Export final video from editor (.mp4)
2. whisper Clip.mp4 --language Romanian --model turbo --output_format srt \
       --word_timestamps True --max_line_width 42 --max_line_count 2 \
       --output_dir /path/to/export/
3. python scripts/correct_srt.py raw.srt Clip_RO.srt
4. [Manual SRT review — fix remaining errors directly in the file]
5. python scripts/translate_srt.py Clip_RO.srt Clip_EN.srt
6. python scripts/analyze_srt.py Clip_RO.srt Clip.mp4
   → outputs: Clip_video_metadata.txt + Clip_shorts_candidates.txt
   → interactive: choose which shorts to cut → ffmpeg cuts them automatically
7. Edit scripts/shorts_config.yaml with chosen segments (from candidates file)
8. .venv/bin/python scripts/shorts_generator.py --video Clip.mp4
9. [Upload main video → add youtube_url to scripts/shorts_config.yaml]
10. python scripts/analyze_srt.py Clip_RO.srt Clip.mp4 --shorts-config scripts/shorts_config.yaml
    → generates per-short metadata.txt with video link filled in
```

Note: Whisper accepts `.mp4` directly — no separate audio export needed.

All outputs (metadata, translated SRT, short candidates) are saved next to the video file.

---

## Scripts

All scripts live in `scripts/` (see paths above for exact commands).

| Script | Input | Output |
|:---|:---|:---|
| `correct_srt.py` | `raw.srt` | `raw_corectat.srt` |
| `translate_srt.py` | `_RO.srt` + `video.mp4` | `_EN.srt` next to video |
| `analyze_srt.py` | `_RO.srt` + `video.mp4` | `video_metadata.txt` + `shorts_candidates.txt` |
| `analyze_srt.py --shorts-config` | config yaml | `{name}_metadata.txt` in `shorts/` |
| `shorts_generator.py` | `video.mp4` + config | `Short{N}-{name}.mp4` + karaoke ASS |
| `kdenlive_from_fragments.py` | `fragments.yaml` | `.kdenlive` rough-cut project |
| `srt_from_fragments.py` | `fragments.yaml` + raw SRTs | retimed `.srt` for the rough cut |

---

## Rough-cut assembly (raw footage → Kdenlive project)

For narrative clips with a lot of raw, uncut footage (multi-day trips, vlogs), scrubbing through every source file to find the moments already selected in the written structure doc wastes time. These two scripts skip that: given a list of (source file, in, out) fragments already identified (from raw transcripts, matched to quotes), they generate a ready-to-open Kdenlive project with those fragments placed on the timeline, plus a subtitle file retimed to match.

```
1. Identify fragments (source video + in/out timecode + label), e.g. by grepping raw
   Whisper SRTs for the quotes already chosen in the video structure doc
2. Write them to fragments.yaml (see format below)
3. python3 scripts/kdenlive_from_fragments.py fragments.yaml
   → generates the .kdenlive project (validated with `melt` automatically)
4. python3 scripts/srt_from_fragments.py fragments.yaml transcripts_raw/ output.srt
   → generates a subtitle file retimed to the new (cut) timeline
5. Open the .kdenlive project, Project → Subtitles → Import Subtitle File → output.srt
6. Continue editing normally (trim, add music/transitions) from this starting point
```

**fragments.yaml format:**

```yaml
source_dir: /path/to/raw/clips
output: /path/to/Project.kdenlive
fragments:
  - file: DJI_..._D.MP4
    in: "00:11:16.320"
    out: "00:11:50.680"
    label: beat5-descriptive-label
  - file: ...
    in: "..."
    out: "..."
    label: ...
```

Fragments are placed on the timeline in the order they appear in the YAML. Each unique source file gets one producer (`<chain>`); fragments from the same file reuse it with different in/out.

**Why this isn't part of the main correct→translate→analyze→shorts pipeline:** it operates on *raw, uncut* footage before an editing decision has been made, not on the *final exported* video. It's a pre-editing step, used once per clip to skip manual scrubbing — not a repeated per-clip pipeline stage like the scripts above.

See `docs/ARCHITECTURE.md` for how the generated Kdenlive/MLT XML is structured, and `docs/DECISIONS.md` for why several non-obvious choices were made (video/audio track split, explicit stream indices, subtitle approach).

---

## Installation

```bash
git clone https://github.com/Dorusto/yt-pipeline.git
cd yt-pipeline
uv venv
uv pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu124
```

Requires: `ffmpeg` with nvenc, `openai-whisper` via pipx, `DEEPSEEK_API_KEY` in environment.

---

## shorts_config.yaml

```yaml
video: "MyClip.mp4"
srt:   "MyClip_RO.srt"
youtube_url: "https://youtu.be/..."   # optional — fills short descriptions

segments:
  - name: "Hook"
    start: "00:00:00"
    end:   "00:00:54"
    # no x_offset → auto face detection

  - name: "Delegarea"
    start: "00:05:06"
    end:   "00:05:46"
    x_offset: 800   # manual crop override (pixels from left, 0–1312)
```

Copy from `shorts_config_example.yaml`. File is git-ignored.

---

## Tech stack

| Component | Tool |
|:---|:---|
| Transcription | `openai-whisper turbo` (pipx) |
| Forced alignment | `whisperx` (wav2vec2 Romanian) |
| Face detection | OpenCV Haar cascades |
| ASS subtitles | `pysubs2` |
| Video render | `ffmpeg h264_nvenc` |
| AI (metadata / translation) | DeepSeek API |
| Package management | `uv` |
