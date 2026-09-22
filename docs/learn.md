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
- Kdenlive's clip-grouping ("linked" video+audio pair, `Ctrl+G`) is stored as `kdenlive:sequenceproperties.groups` (or `kdenlive:docproperties.groups` in older single-sequence projects) — a JSON array of `{"children": [{"data": "trackPos:framePos", "leaf": "clip", "type": "Leaf"}, ...], "type": "AVSplit"}`. `trackPos` is a **0-based track position**, counted bottom-to-top, *excluding* the invisible `black_track` — not a raw MLT producer/track id. `framePos` is a raw integer frame count, not a timecode string. Decoded with confidence from `src/timeline2/model/groupsmodel.cpp` (`GroupsModel::fromJson`, which resolves `trackPos` via `getTrackIndexFromPosition()`) and the official test fixture `tests/dataset/av.kdenlive` in the `KDE/kdenlive` source repo — both linked from `dev-docs/fileformat.md`, which documents the property's existence (not just a guess).
- This `groups` property only exists at the *sequence*-tractor level (the one carrying a `kdenlive:uuid` property), which is part of Kdenlive's "Generation 5" project structure (23.04+; each timeline track is its own 2-playlist mini-tractor, all wrapped in one sequence tractor — see `dev-docs/fileformat.md` in `KDE/kdenlive` for the generation history). The simpler flat single-tractor structure this generator otherwise prefers doesn't have that level. Kdenlive silently upgrades a flat file to Generation 5 on first open+save (confirmed: a 14 KB flat script output became a 172 KB nested file after one Kdenlive save) — that upgrade can't be relied on to remap a hand-written groups blob targeting the flat layout, so writing groups requires generating the full Generation 5 skeleton directly.
- MLT playlist entries are **frame-inclusive**: `frame_count = frame_out - frame_in + 1` (confirmed in `mlt_playlist.c`). When computing cumulative frame positions across fragments (e.g. for the groups property above), forgetting the `+1` per fragment under-counts every fragment after the first.
- To check whether a Python-side frame conversion actually matches MLT's own timecode parser (`lrint(fps * seconds)` in `mlt_property.c`, rounding-mode-dependent) for a given `in`/`out` timecode, don't guess — ask `melt` directly: `melt "avformat-novalidate:<file>" video_index=0 audio_index=1 in=<tc> out=<tc> -consumer xml:out.xml` and read the resulting `<producer in="..." out="...">` frame numbers back from `out.xml`.
- Subtitle retiming for a rough-cut assembled from fragments: don't reverse-engineer Kdenlive's native subtitle format — just emit a plain `.srt` with each source line's timestamp remapped to `sum(prior fragment durations) + (line_start - fragment_in)`, clipped to the fragment's own boundaries, and import it normally (`Project → Subtitles → Import Subtitle File`).

## Face detection (OpenCV)

- `detect_face_offset()` logs `face detected in X/Y sampled frames`. If X=0, face is not detected — use manual `x_offset` in config.
- In talking-head setups, the face is often close to center (1920×1080). The auto-detected offset may look identical to center if the difference is < ~100px.
- Add `x_offset: <int>` per segment in `shorts_config.yaml` to bypass face detection entirely.
- `detect_face_offset()` computes **one average offset for the whole segment**. If a segment is built from multiple edited shots (talking-head + b-roll cuts, common in curatoriere-video-brut projects), the average gets skewed by whichever shots have detectable faces, producing a bad crop on the shots that don't — even though the "no face at all" fallback (centered) works fine on its own. Symptom: crop looks off-center specifically on b-roll-only frames within an otherwise-fine segment. Found on Dolomiti (2026-09-06): all 5 segments were multi-shot, manual `x_offset: 656` (centered) on every segment was more reliable than trusting auto-detect per segment.

## Karaoke subtitles (WhisperX + ASS)

- **`WhisperX.align()` silently drops words it can't align** — `result["word_segments"]` only contains successfully-aligned words, with no marker for skipped ones. A word missing from forced alignment is a word missing from the karaoke captions, with no warning. Found on Dolomiti (2026-09-06): several words were missing from generated Shorts captions, present in every regenerated attempt because `--skip-alignment` reused the same (already incomplete) cached word JSON.
- **Root cause was actually upstream of WhisperX**, and affected the non-WhisperX fallback too: `parse_srt_for_alignment()` returns segment text copied straight from `event.plaintext`, which preserves the original SRT's line breaks as literal `\n` characters (from `--max_line_count 2` whisper formatting). Feeding a raw `\n` into `pysubs2.SSAEvent(text=...)` writes it as a literal newline in the `.ass` file — but ASS/SSA is line-based (one `Dialogue:` entry = one physical line), so the text after the embedded `\n` lands on an unprefixed orphan line and is silently ignored by the parser. This is why words were missing **even with WhisperX bypassed entirely** — the bug was in the text handed to it, not the alignment step. Fix: always `.replace("\n", " ").replace("\r", " ")` on SRT-derived text before it goes into an `SSAEvent`.
- Current approach (post-fix, 2026-09-06): skip WhisperX forced alignment entirely. Take the segment text straight from the corrected `.srt` (via `parse_srt_for_alignment`, newline-sanitized), split on whitespace, and interpolate each word's start/end evenly across the segment's known duration (`words_from_srt_segments()`). Loses frame-accurate per-word sync; gains a hard guarantee that no word can ever go missing, since the source is the already-human-corrected `.srt`, not a second (imperfect) transcription pass. Doru's call: correctness over precision here.
