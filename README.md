# yt-pipeline

CLI pipeline for processing YouTube clips: transcription → correction → translation → metadata → 9:16 shorts with karaoke subtitles.

---

## Workflow

```
1. Export clip (video + audio from editor)
2. whisper audio.mp3 --output_format srt
3. python correct_srt.py raw.srt
4. [Manual SRT review — fix remaining errors directly in the file]
5. python translate_srt.py RO.srt ~/Videos/Clip/Export/video/Clip.mp4
6. python analyze_srt.py RO.srt ~/Videos/Clip/Export/video/Clip.mp4
7. Edit shorts_config.yaml with chosen segments
8. .venv/bin/python shorts_generator.py --video ~/Videos/Clip/Export/video/Clip.mp4
9. python analyze_srt.py RO.srt ~/Videos/Clip/Export/video/Clip.mp4 --shorts-config shorts_config.yaml
10. [Upload main video → add youtube_url to shorts_config.yaml → re-run step 9]
```

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
