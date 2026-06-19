# DECISIONS — yt-pipeline

Format: **[Date] Title** — context, options considered, decision, rationale.

---

## [2026-06-18] WhisperX forced alignment instead of re-running Whisper

**Context:** `shorts_generator.py` was running Whisper a second time on the audio segment for word timestamps, independently of the first SRT-generating run.

**Options:**
- A) Save `_words.json` on the first Whisper run (simple, but text may contain transcription errors)
- B) WhisperX forced alignment on corrected SRT (correct text, separate run)

**Decision:** B — WhisperX forced alignment.

**Rationale:** The corrected SRT is the single source of truth. WhisperX aligns the correct text to audio precisely, eliminating transcription errors from karaoke highlights. One place to fix mistakes.

---

## [2026-06-18] OpenCV Haar cascades instead of MediaPipe for face detection

**Context:** MediaPipe 0.10 removed the `mp.solutions` API. Required a model download and new API.

**Options:**
- A) Downgrade MediaPipe to 0.9.x
- B) MediaPipe Tasks API (new) + model download
- C) OpenCV Haar cascades (built-in, no extra dependencies)

**Decision:** C — OpenCV Haar cascades.

**Rationale:** Zero overhead, already installed. Since we use one static offset per segment (not dynamic tracking), a more sophisticated model's accuracy gain doesn't justify the added complexity.

---

## [2026-06-18] Single static crop offset per segment (no dynamic tracking)

**Context:** Alternative was to track the face frame-by-frame.

**Decision:** One average offset computed from samples every 2 seconds.

**Rationale:** Dynamic tracking produces visible shakiness. A static offset is visually stable for talking-head videos where the speaker doesn't move much.

---

## [2026-06-18] uv instead of pip/virtualenv

**Context:** Dependency management for isolated per-project venv.

**Decision:** `uv` for all package management operations.

**Rationale:** 10–100x faster than pip. Torch + whisperx install significantly faster.

---

## [2026-06-19] Trim at sentence boundary for SRT entries spanning segment boundaries

**Context:** SRT entries covering the segment start or end time included text not spoken in the clip. WhisperX aligned all text in the entry, producing words outside the clip bounds in the karaoke output.

**Options:**
- A) Proportional trim (word count ≈ time fraction)
- B) Trim at nearest sentence-ending punctuation relative to the proportional estimate

**Decision:** B — sentence boundary, applied at both start and end.

**Rationale:** Option A left partial sentences (e.g. "Iar" at the end, or "Dacă eu pot..." at the start). Option B cuts cleanly at natural sentence breaks. Applied symmetrically: end-straddle trims backward to last punctuation before cut; start-straddle trims forward to first punctuation after cut.

---

## [2026-06-19] Manual `x_offset` override per segment

**Context:** Face detection via Haar cascades works on frontal faces but the auto-detected offset may not match the desired framing for every segment.

**Decision:** Add optional `x_offset` key per segment in `shorts_config.yaml`. If present, skip face detection entirely for that segment.

**Rationale:** Gives the creator precise control without disabling auto-detection globally. Segments without `x_offset` still auto-detect.

---

## [2026-06-19] Per-short metadata via `--shorts-config` in `analyze_srt.py`

**Context:** `analyze_srt.py` only generated metadata for the main video. Shorts need their own title, description, tags, and hashtags for upload.

**Options:**
- A) Separate script for per-short metadata
- B) `--shorts-config` flag on existing `analyze_srt.py`

**Decision:** B — extend `analyze_srt.py`.

**Rationale:** Reuses the existing DeepSeek client and SRT parsing. One script to call, one place to maintain. Output goes in `shorts/{name}_metadata.txt` next to each video file.
