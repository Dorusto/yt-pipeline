# yt-pipeline

CLI pipeline for processing YouTube clips: transcription → correction → translation → metadata → 9:16 shorts with karaoke subtitles.

---

## Scripts

| Script | What it does |
|:---|:---|
| `correct_srt.py` | Applies a corrections list to fix common Whisper transcription errors |
| `translate_srt.py` | Translates SRT from Romanian to English via DeepSeek API |
| `analyze_srt.py` | Generates title, description, chapters and tags from SRT via DeepSeek API |
| `shorts_generator.py` | Generates 9:16 shorts with karaoke highlighting from video + corrected SRT |

---

## Requirements

- Python 3.10+
- [`uv`](https://github.com/astral-sh/uv)
- `ffmpeg` with nvenc support
- `openai-whisper` — transcription (install via pipx)
- `DEEPSEEK_API_KEY` in environment — for correction, translation and metadata

---

## Installation

```bash
git clone https://github.com/Dorusto/yt-pipeline.git
cd yt-pipeline
uv venv
uv pip install -r requirements.txt
```

---

## Workflow

```
[Export clip]
      ↓
whisper audio.mp3 --word_timestamps True --output_format all
      ↓
python correct_srt.py MyClip.srt
      ↓
[Manual SRT review]
      ↓
python translate_srt.py MyClip_corrected.srt
      ↓
python analyze_srt.py MyClip_corrected.srt MyClip.mp4
      ↓
python shorts_generator.py --video MyClip.mp4 --srt MyClip_corrected.srt
```

---

## shorts_generator.py

Generates 9:16 crops with karaoke subtitle highlighting from a 1920×1080 source.

**What it does:**
- Detects face position → computes 608×1080 crop offset automatically
- Runs WhisperX forced alignment on the corrected SRT → word-level timestamps
- Generates ASS karaoke file (active word = yellow, rest = white)
- Renders with ffmpeg + h264_nvenc (GPU)

**Usage:**
```bash
# Direct mode
.venv/bin/python shorts_generator.py \
  --video /path/to/video.mp4 \
  --audio /path/to/audio.mp3 \
  --srt   /path/to/corrected.srt

# Re-render only (skip alignment, use existing words JSON)
.venv/bin/python shorts_generator.py --video ... --skip-alignment
```

**Config** (`shorts_config.yaml` — git-ignored, copy from `shorts_config_example.yaml`):
```yaml
video: "MyClip.mp4"
audio: "MyClip.mp3"
srt:   "MyClip_RO.srt"

segments:
  - name: "Hook"
    start: "00:00:00"
    end: "00:00:54"
```

---

## correct_srt.py

Applies find/replace corrections from `corrections.txt` (one `wrong|correct` pair per line).

```bash
python correct_srt.py input.srt
python correct_srt.py input.srt output_corrected.srt
```

---

## translate_srt.py

Translates SRT preserving exact structure (numbers, timestamps, blank lines).

```bash
python translate_srt.py input_RO.srt
python translate_srt.py input_RO.srt output_EN.srt
```

Requires: `DEEPSEEK_API_KEY` environment variable.

---

## analyze_srt.py

Generates YouTube metadata from transcript.

```bash
python analyze_srt.py subtitles.srt video.mp4
```

Output: `video_metadata.txt` with title, description, chapters, tags.

Requires: `DEEPSEEK_API_KEY` environment variable.

---

## Tech stack

| Component | Tool |
|:---|:---|
| Transcription | `openai-whisper turbo` |
| Forced alignment | `whisperx` |
| Face detection | `opencv-python` (Haar cascades) |
| ASS subtitles | `pysubs2` |
| Video render | `ffmpeg h264_nvenc` |
| AI (correction / translation / metadata) | DeepSeek API |
