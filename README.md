# yt-pipeline

CLI pipeline for processing YouTube clips: transcription → correction → translation → metadata → 9:16 shorts with karaoke subtitles.

---

## Workflow

```
1. Export final video from editor (.mp4)
2. whisper Clip.mp4 --language Romanian --model turbo --output_format srt \
       --word_timestamps True --max_line_width 42 --max_line_count 2 \
       --output_dir /path/to/export/
3. python correct_srt.py raw.srt Clip_RO.srt
4. [Manual SRT review — fix remaining errors directly in the file]
5. python translate_srt.py Clip_RO.srt Clip_EN.srt
6. python analyze_srt.py Clip_RO.srt Clip.mp4
   → outputs: Clip_video_metadata.txt + Clip_shorts_candidates.txt
   → interactive: choose which shorts to cut → ffmpeg cuts them automatically
7. Edit shorts_config.yaml with chosen segments (from candidates file)
8. .venv/bin/python shorts_generator.py --video Clip.mp4
9. [Upload main video → add youtube_url to shorts_config.yaml]
10. python analyze_srt.py Clip_RO.srt Clip.mp4 --shorts-config shorts_config.yaml
    → generates per-short metadata.txt with video link filled in
```

Note: Whisper accepts `.mp4` directly — no separate audio export needed.

All outputs (metadata, translated SRT, short candidates) are saved next to the video file.

---

## Scripts

| Script | Input | Output |
|:---|:---|:---|
| `correct_srt.py` | `raw.srt` | `raw_corectat.srt` |
| `translate_srt.py` | `_RO.srt` + `video.mp4` | `_EN.srt` next to video |
| `analyze_srt.py` | `_RO.srt` + `video.mp4` | `video_metadata.txt` + `shorts_candidates.txt` |
| `analyze_srt.py --shorts-config` | config yaml | `{name}_metadata.txt` in `shorts/` |
| `shorts_generator.py` | `video.mp4` + config | `Short{N}-{name}.mp4` + karaoke ASS |

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
