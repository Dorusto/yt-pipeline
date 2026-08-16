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
- `youtube_url` in config auto-fills all short descriptions. Add it once after the main video is published, then re-run `analyze_srt.py --shorts-config` to refresh metadata.
- `x_offset` per segment overrides face detection — use when auto-detect picks wrong crop.
- **Segment names become filenames** (`Short1-{name}.mp4`) — avoid spaces and special characters. Use `-` or `_`. The display title lives in the metadata file, not in the name.

## Growing `corrections.txt`

Every time a Whisper error slips through `correct_srt.py` undetected, add it to `corrections.txt` immediately. The file is git-ignored (personal vocabulary), but it compounds in value over time. After the Lenea clip: add `înrăbdare|nerăbdare`.

## SRT boundary trimming

- When a segment start/end cuts through an SRT entry, WhisperX force-aligns ALL text in that entry — including words outside the clip.
- **End straddling**: trim at the last sentence boundary (`.`, `!`, `?`) before the proportional cut point. Words after the boundary are dropped.
- **Start straddling**: trim at the first sentence boundary AFTER the proportional cut point. Words before the boundary are dropped.
- Always fix errors in the SRT file directly — it is the single source of truth. WhisperX propagates the corrected text automatically.

## Kdenlive / MLT project generation

- `melt -consumer xml project.kdenlive` only validates that MLT can parse and re-serialize the file — it does NOT catch Kdenlive-UI-specific bugs (missing audio routing, wrong track order, missing version property all passed this check while being broken in the actual app). Only opening in Kdenlive itself catches those.
- DJI Action 4 (and likely other action cams) `.MP4` files carry extra `data` streams (GPS/telemetry) and a second `mjpeg` video stream (embedded thumbnail) alongside the real video/audio pair. Always set `video_index`/`audio_index` explicitly on the chain — don't rely on MLT's auto-detection. Verify once per shoot with `ffprobe -show_entries stream=index,codec_type -of csv=p=0 file.mp4`.
- A combined AV producer on a single Kdenlive video track plays fine when rendered via `melt` directly, but Kdenlive's own mixer never routes audio from a track typed "video" — audio needs its own dedicated track (`hide="video"` on that `<track>`), even though it's the exact same source file/chain.
- `<property name="kdenlive:docproperties.version">1.1</property>` (inside the `main_bin` playlist) is required or Kdenlive shows an "Incorrect project file" warning on open. This is separate from the MLT framework's own `version="7.40.0"` attribute on `<mlt>`.
- Track stacking order in the UI is the *reverse* of the `<track>` element order inside `<tractor>` — the last track in the XML is the topmost in Kdenlive.
- Default Kdenlive project layout is 4 tracks: V2, V1, A1, A2 (top to bottom) — include empty V2/A2 playlists even if unused, so the project matches what a human-created Kdenlive project looks like.
- Kdenlive's clip-grouping ("linked" video+audio pair, `Ctrl+G`) is stored as an undocumented JSON blob (`kdenlive:sequenceproperties.groups`) with track-index/frame-position encoding — not worth reverse-engineering for a convenience feature when manual grouping is a two-click fix.
- Subtitle retiming for a rough-cut assembled from fragments: don't reverse-engineer Kdenlive's native subtitle format — just emit a plain `.srt` with each source line's timestamp remapped to `sum(prior fragment durations) + (line_start - fragment_in)`, clipped to the fragment's own boundaries, and import it normally (`Project → Subtitles → Import Subtitle File`).

## Face detection (OpenCV)

- `detect_face_offset()` logs `face detected in X/Y sampled frames`. If X=0, face is not detected — use manual `x_offset` in config.
- In talking-head setups, the face is often close to center (1920×1080). The auto-detected offset may look identical to center if the difference is < ~100px.
- Add `x_offset: <int>` per segment in `shorts_config.yaml` to bypass face detection entirely.
