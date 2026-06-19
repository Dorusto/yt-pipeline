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
- `correct_srt.py` does not catch all errors — always review SRT manually and fix directly in the file

## Path handling

- `analyze_srt.py` and `translate_srt.py` require absolute paths (or paths with `~`). Relative paths from a different directory than the video folder will fail silently or create spurious directories.
- `shorts_generator.py` uses `find_file()` to locate SRT/audio relative to the video — only `--video` needs to be absolute.
- Running `analyze_srt.py` with a non-existent video path creates a `video/shorts/` folder in the current directory. Clean it up manually.

## Workflow order

```
1. correct_srt.py  → fix Whisper errors (+ manual review of SRT)
2. translate_srt.py → RO→EN subtitle (optional, for EN shorts later)
3. analyze_srt.py  → main video metadata + shorts candidates list
4. shorts_generator.py → render 9:16 shorts (one or all segments)
5. analyze_srt.py --shorts-config → per-short metadata with YouTube URL
```

Always fix text errors in the corrected SRT file — `shorts_generator.py` propagates them automatically via WhisperX forced alignment.

## Config design

- All segment info lives in `shorts_config.yaml` (git-ignored). Keep all segments there permanently — useful for re-generating metadata or re-rendering.
- `youtube_url` in config auto-fills all short descriptions. Add it once after the main video is published.
- `x_offset` per segment overrides face detection — use when auto-detect picks wrong crop.
- Segment names become filenames (`Short1-{name}.mp4`) — avoid spaces and special characters.

## SRT boundary trimming

- When a segment start/end cuts through an SRT entry, WhisperX force-aligns ALL text in that entry — including words outside the clip.
- **End straddling**: trim at the last sentence boundary (`.`, `!`, `?`) before the proportional cut point. Words after the boundary are dropped.
- **Start straddling**: trim at the first sentence boundary AFTER the proportional cut point. Words before the boundary are dropped.
- Always fix errors in the SRT file directly — it is the single source of truth. WhisperX propagates the corrected text automatically.

## Face detection (OpenCV)

- `detect_face_offset()` logs `face detected in X/Y sampled frames`. If X=0, face is not detected — use manual `x_offset` in config.
- In talking-head setups, the face is often close to center (1920×1080). The auto-detected offset may look identical to center if the difference is < ~100px.
- Add `x_offset: <int>` per segment in `shorts_config.yaml` to bypass face detection entirely.
