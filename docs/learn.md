# Learnings — yt-pipeline

Non-obvious findings: gotchas, surprising behaviors, debugging notes.

---

## WhisperX

- `whisperx.load_align_model(language_code="ro")` downloads the model on first run (~500MB). Cached afterwards.
- Timestamps from WhisperX are relative to the audio passed in (0-based). If you extract a segment with ffmpeg first, timestamps are already correct.
- `return_char_alignments=False` is important for performance — otherwise it returns character-level alignments too.
- Install with `--extra-index-url https://download.pytorch.org/whl/cu124` for CUDA 12.4 (compatible with CUDA 13.3 on this system).

## ffmpeg

- `-ss` before `-i` = fast seek (keyframe-accurate). Sufficient for talking-head video.
- `h264_nvenc preset p4, cq 23` = good quality/speed balance on RTX 4070 Laptop (~17–19x realtime).
- The `ass=` filter in ffmpeg requires `PlayResX/PlayResY` in the ASS file — without it libass assumes 384×288 and the font renders ~3x too large.

## pysubs2 / ASS format

- Colors in ASS are `&HBBGGRR` (not RGB). Yellow = `&H00FFFF` (B=00, G=FF, R=FF).
- `{\c&H00FFFF&}` sets the primary color. `{\c&HFFFFFF&}` resets to white.
- `alignment=2` in SSAStyle = bottom center (numpad layout).

## OpenCV Haar cascades

- `haarcascade_frontalface_default.xml` is bundled in `cv2.data.haarcascades` — no download needed.
- `minSize=(60, 60)` removes false positives on noisy frames.
- Sampling every 2s is sufficient for talking-head videos. More frequent = slower with no real gain.

## Known Whisper errors on Romanian

- "înrăbdare" → "nerăbdare" (wrong prefix — Whisper mishears "ne" as "în")
- Hyphenated words ("task-ul", "n-a", "să-ți") are sometimes split into separate tokens
- `merge_hyphenated()` in `shorts_generator.py` handles hyphen splits automatically
