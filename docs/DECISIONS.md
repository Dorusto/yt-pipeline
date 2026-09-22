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

## [2026-08-16] Hand-generate MLT/Kdenlive XML instead of pre-cutting clips with ffmpeg

**Context:** an earlier attempt (before this repo existed) pre-cut every selected moment into separate small files with ffmpeg and imported them into Kdenlive — the resulting project had hundreds of tiny clips and Kdenlive couldn't process the timeline (crashed / hung).

**Options:**
- A) Pre-cut every fragment to its own file with ffmpeg, import into Kdenlive
- B) Generate a `.kdenlive` project file directly (MLT XML) referencing the original source files with in/out points per fragment — no re-encoding, no extra files

**Decision:** B.

**Rationale:** Kdenlive/MLT natively supports multiple timeline entries against one source producer with different in/out — there was never a need to physically cut files. This also keeps fragment count low (dozens, not hundreds) since fragments are curated quote-level cuts, not every micro-pause. Validated with `melt -consumer xml project.kdenlive` before ever opening in the GUI, to catch XML errors cheaply.

---

## [2026-08-16] Video and audio must be on separate tracks, not one combined AV track

**Context:** first version put each fragment as a single `<entry>` on one V1 track, referencing an `avformat` chain that contains both video and audio streams. `melt` rendered this correctly with audio (verified: `melt project.kdenlive -consumer avformat:test.mp4` produced a file with an audio stream). Opened in Kdenlive: video played, but no sound.

**Root cause:** Kdenlive's UI strictly separates track *types* — a track shown as "video" (V1/V2) never routes audio to the mixer/monitor in the app, regardless of what the underlying MLT producer contains. This only affects Kdenlive's own playback/mixer; `melt`'s own render path plays/encodes audio from a combined producer just fine. Confirmed by inspecting a real hand-edited `.kdenlive` project: every clip with audio has a *separate* audio-track entry, even when the video-track entry references the exact same source file.

**Decision:** every fragment gets two entries — one on a video-only track (`hide="audio"` on the `<track>`) and one on an audio-only track (`hide="video"`), both referencing the same `<chain>` producer with different `hide` masks.

**Rationale:** this is not a workaround, it is how Kdenlive's UI model actually works — confirmed against a real project, not guessed. `melt`-level validation does not catch this class of bug (it validates MLT semantics, not Kdenlive UI behavior), so this required an actual open-in-Kdenlive test to discover.

---

## [2026-08-16] Explicit `video_index`/`audio_index` on chain producers

**Context:** DJI Action 4 `.MP4` files contain more than one video+audio pair — `ffprobe` shows stream 0 (h264, video), stream 1 (aac, audio), several `data` streams (GPS/telemetry), and a second video stream (mjpeg, likely an embedded thumbnail). Without explicit stream selection, first attempts produced no audio in the Kdenlive project bin thumbnail/preview.

**Decision:** always set `video_index="0"` and `audio_index="1"` explicitly on every `<chain>`, after confirming via `ffprobe -show_entries stream=index,codec_type` that this mapping is consistent across all source files in a shoot (checked 4 different files from different days before trusting it).

**Rationale:** relying on MLT's automatic stream selection is not safe for action-cam footage with extra metadata streams. Cheap to verify with `ffprobe` per-source before generating.

---

## [2026-08-16] `kdenlive:docproperties.version` is a required property, not optional metadata

**Context:** the first generated `.kdenlive` opened with an "Incorrect project file — Version of the project file cannot be read" dialog (non-fatal, Kdenlive opened it anyway, but it's a bad first impression / signals an incomplete file).

**Decision:** always set `<property name="kdenlive:docproperties.version">1.1</property>` (and `kdenlive:docproperties.kdenliveversion`, `kdenlive:documentnotesversion`) inside the `main_bin` playlist. Found by inspecting a real project file — this property is unrelated to the MLT framework's own `version="7.40.0"` attribute on the root `<mlt>` element, which is not what Kdenlive checks.

---

## [2026-08-16] Track order in `<tractor>` is the reverse of the on-screen order

**Context:** first version listed the video track before the audio track in the `<tractor>`'s `<track>` list; Kdenlive displayed audio *above* video (opposite of the intended V1-on-top-of-A1 layout).

**Decision:** the *last* `<track>` element in the tractor is the *topmost* track in Kdenlive's UI. Order used: A2, A1, V1, V2 (bottom to top in XML = bottom to top in UI, i.e. V2 last/topmost). Confirmed empirically (swap the order, re-test) and cross-checked against a real project's track ordering (audio tracks listed before video tracks in its main sequence tractor).

---

## [2026-08-16] Subtitle retiming as a plain `.srt` + native Kdenlive import, not hand-generated subtitle XML

**Context:** needed captions on the rough-cut timeline reflecting what's actually said (not just a debug label). Kdenlive has a native "Subtitles" track feature with its own internal representation.

**Options:**
- A) Reverse-engineer Kdenlive's native subtitle XML/JSON structure and inject it directly into the generated project
- B) Generate a plain, retimed `.srt` file as a separate output, imported manually via `Project → Subtitles → Import Subtitle File`

**Decision:** B.

**Rationale:** the video/audio track split above already required one real Kdenlive-open-and-check cycle to get right, purely from undocumented UI behavior; the native subtitle structure is a similar unknown. `srt_from_fragments.py` reuses the exact manual import step already established in this project's normal S6 workflow (see root `CLAUDE.md` → "Pasul 5 — Import în KDenLive"), so there's no new failure surface, no new Kdenlive-internals reverse-engineering, and it's immediately usable.

---

## [2026-08-16] Declined: automatic video+audio clip grouping

**Context:** with video/audio split onto separate tracks, dragging or trimming one fragment doesn't move its audio counterpart along with it (no visible link between the pair) — user asked whether this can be automated ("if it's not much work").

**Investigation:** Kdenlive stores clip groups as `kdenlive:sequenceproperties.groups`, a JSON tree (`{"children": [{"data": "trackIndex:framePosition:-1", "leaf": "clip", "type": "Leaf"}, ...], "type": "AVSplit"}`) attached to the *sequence*-level producer (a further layer of nesting not otherwise needed by this generator). The `trackIndex`/`framePosition` encoding is undocumented and wasn't reverse-engineered with confidence.

**Decision:** not implemented. Grouping stays a manual step in Kdenlive (select both clips, `Ctrl+G`).

**Rationale:** unlike the video/audio split (confirmed necessary — no sound without it) or the version property (confirmed necessary — warning dialog without it), this is a convenience feature, not a correctness fix. `melt` validation cannot check whether a hand-written groups JSON is well-formed for Kdenlive's purposes (groups aren't part of MLT playback) — every attempt would need a real open-and-drag test in the GUI, same slow feedback loop as the audio bug, for a problem the user already has a trivial two-click manual fix for.

**Superseded 2026-08-18** — see below. Manual trimming without grouping turned out to be a real recurring cost (not just a one-off convenience), and the encoding was decoded with confidence from Kdenlive's own source/test fixtures, so this was revisited and implemented.

---

## [2026-08-18] Implemented native AVSplit clip grouping (reverses the 2026-08-16 decline)

**Context:** manual re-linking of every video/audio pair after generation was costing real edit time on multi-fragment rough-cuts (e.g. 35 fragments on the Dolomiti project) — trimming one clip without its pair caused audio drift, exactly the failure mode the 2026-08-16 entry accepted as a manual step. Revisited with a real investigation instead of stopping at "undocumented."

**Investigation:**
- Kdenlive's own dev docs (`dev-docs/fileformat.md` in `KDE/kdenlive`) document `kdenlive:docproperties.groups` (older single-sequence projects) / `kdenlive:sequenceproperties.groups` (Generation 5, Kdenlive 23.04+, what 26.04.3 uses) as a real, intentional JSON property — not purely a reverse-engineering exercise.
- The official test fixture `tests/dataset/av.kdenlive` in the Kdenlive source repo is a minimal, canonical example of one AVSplit group and was used as the structural reference.
- `src/timeline2/model/groupsmodel.cpp` (`GroupsModel::fromJson`) is the authoritative decoder: for a `"data": "trackPos:framePos"` leaf, `trackPos` is resolved via `getTrackIndexFromPosition(trackPos)` — i.e. it's a **0-based track position** (bottom to top, excluding the invisible `black_track`), not a raw internal MLT id. `framePos` is a raw integer frame count, not a timecode string.
- This property only exists at the *sequence*-tractor level (the one carrying `kdenlive:uuid`), which only exists in Kdenlive's "Generation 5" nested project structure (each timeline track wrapped in its own 2-playlist mini-tractor, all wrapped in one sequence tractor). The generator's older flat single-tractor structure doesn't have this level at all — Kdenlive silently upgrades a flat file to Generation 5 on first open+save (confirmed empirically: the script's raw 14 KB output became a 172 KB nested-structure file after one Kdenlive save), and that upgrade cannot be relied on to preserve or correctly remap a hand-written groups blob targeting the old flat layout.
- Playlist entry length is **frame-inclusive**: confirmed directly in `mlt_playlist.c` (`frame_count = frame_out - frame_in + 1`), so cumulative frame positions for later fragments must add 1 per fragment, not just `out - in`.
- Frame-conversion correctness (Python's `round()` vs MLT's internal `lrint()`-based timecode parser) was cross-checked by literally asking `melt` to parse each fragment's `in`/`out` timecode and echo back the resulting frame numbers (`melt "avformat-novalidate:<file>" in=<tc> out=<tc> -consumer xml:out.xml`), rather than trusting Python's rounding to match MLT's C rounding blind.

**Decision:** the generator now writes the full Generation 5 structure (per-track mini-tractors + one sequence tractor with `kdenlive:uuid`, matching what Kdenlive itself writes) and computes `kdenlive:sequenceproperties.groups` (one `AVSplit` entry per fragment, track positions 1=A1/2=V1, frame-inclusive cumulative positions) so every video/audio pair is grouped automatically on generation — no manual `Ctrl+G` per fragment.

**Open loose end:** small synthetic test files (2–3 fragments) reproducibly showed only the *last* AV group failing to link, across multiple attempts including with distinct (non-reused) source files, with no load-time Kdenlive error output. The same code applied to the real 35-fragment Dolomiti project worked correctly (per user confirmation) — root cause of the small-test-only failure was not identified. Noted here in case it resurfaces; not currently blocking real use.

---

## [2026-06-19] Per-short metadata via `--shorts-config` in `analyze_srt.py`

**Context:** `analyze_srt.py` only generated metadata for the main video. Shorts need their own title, description, tags, and hashtags for upload.

**Options:**
- A) Separate script for per-short metadata
- B) `--shorts-config` flag on existing `analyze_srt.py`

**Decision:** B — extend `analyze_srt.py`.

**Rationale:** Reuses the existing DeepSeek client and SRT parsing. One script to call, one place to maintain. Output goes in `shorts/{name}_metadata.txt` next to each video file.

---

## [2026-09-06] Moved `analyze_srt.py` off DeepSeek-direct to OpenRouter; scoped down its responsibility

**Context:** DeepSeek-direct API returned `402 Insufficient Balance` when running `analyze_srt.py` on the Dolomiti project. DeepSeek-direct was already retired project-wide on 2026-09-02 (no more top-ups — see `delegate-by-complexity` skill decisions) in favor of OpenRouter; this script hadn't been migrated yet.

Separately, the same run exposed a design problem: `analyze_srt.py` generated 8 Shorts candidates and a generic video title/chapters from the raw transcript alone. It has no knowledge of narrative decisions already made during the coaching/editing process (which moments are pre-selected as Shorts, which segments were cut from the final edit, the video's actual angle). One candidate referenced a "yoga" scene that had already been removed from the final cut.

**Decision:**
- `client` now points at `https://openrouter.ai/api/v1` with `OPENROUTER_API_KEY`, model `deepseek/deepseek-v4-flash` (both call sites).
- `analyze_srt.py`'s metadata + Shorts-candidate generation is **deprecated, not called anymore** for the main video-metadata/Shorts workflow. Main video metadata and Shorts descriptions are now written directly by Claude from the project's own decided structure (`03_structura_video.md`, `11_plan_montaj.md` → `## Markere Shorts`), not generated blind from the transcript.
- Transcription pipeline (`whisper` → `correct_srt.py` → `translate_srt.py`) is unaffected and remains the source of RO/EN subtitles.
- Same DeepSeek-direct → OpenRouter migration also applied to `scripts/daily_summary_srt.py` (same repo) and to `~/Proiecte-AI/YouTube/Translate/translate_srt.py` (the standalone copy the vault's `CLAUDE.md` post-export pipeline actually calls — see note below).

**Found but not resolved now — two parallel copies of the same tools exist:** `~/Proiecte-AI/YouTube/Correct-Transcript/`, `~/Proiecte-AI/YouTube/Translate/`, `~/Proiecte-AI/YouTube/Create shorts+metadata/` (older, standalone scripts — what the vault's `10_PROJECTS/20_creatie-continut/CLAUDE.md` pipeline actually points to and what ran on the Dolomiti clip) vs. this repo's `scripts/` (newer, consolidated, what this README documents). They've drifted — e.g. the standalone `Create shorts+metadata/analyze_srt.py` still points at the dead DeepSeek-direct API and was not touched by this fix. Needs a real decision (retire the standalone copies and repoint the vault pipeline at this repo, or vice versa) — flagged for a dedicated session, not decided under publish-day time pressure.

**Rationale:** A generic transcript-analysis model can't see decisions already made in a human+Claude coaching session (what's cut, what's the angle, which quotes are pre-selected for Shorts) — its output was consistently wrong in ways that needed manual correction anyway, making the automation net-negative for this step. Rather than trying to feed it more context to compensate (scope creep), the responsibility moved to where the context already lives.

**Not done now (deferred):** consolidating `whisper` + `correct_srt.py` + `translate_srt.py` into a single script, and a proper Shorts-metadata method fed by pre-decided markers instead of raw-transcript guessing. Both are real follow-ups, scoped for a dedicated session — not built under publish-day time pressure.

---

## [2026-09-22] `translate_srt.py` migrated to OpenRouter by changing only the connection

**Context:** `scripts/translate_srt.py` in this repo still used `DEEPSEEK_API_KEY` and `api.deepseek.com`, retired on 2026-09-02. The 2026-09-06 migration covered `analyze_srt.py`, `daily_summary_srt.py` and the standalone `~/Proiecte-AI/YouTube/Translate/translate_srt.py`, but not this copy. The standalone copy has no `.mp4` second-argument convention (save the translation next to the video); this copy does, and `CLAUDE.md` and `README.md` document it.

**Options:**
- A) Copy the standalone file over this one (loses the `.mp4` convention)
- B) Change only the API key variable, base URL and model in this file

**Decision:** B — `OPENROUTER_API_KEY`, `https://openrouter.ai/api/v1`, model `deepseek/deepseek-v4-flash`.

**Rationale:** Same connection as every other script in the repo, smallest possible diff, and the documented `.mp4` convention keeps working.

---

## [2026-09-22] `.mp4` check in `translate_srt.py` ignores letter case

**Context:** The second argument was treated as a video only if it ended in lowercase `.mp4`. Anything else was treated as the output path. Passing `Clip.MP4` (the extension DJI cameras write) made the script open the video for writing and replace it with subtitle text, after the API calls had already been paid for.

**Decision:** `sys.argv[2].lower().endswith(".mp4")`.

**Rationale:** One-line fix that removes the data-loss case for every user of the script, instead of only warning about it in a skill.

---

## [2026-09-22] `analyze_srt.py` is an optional, user-confirmed step inside `yt-transcript`, not a skill of its own

**Context:** Both of its uses (main video metadata and Shorts candidates, per-short metadata) have been deprecated since 2026-09-06, because a transcript-only model cannot see decisions already made about the clip. Packaging the scripts as skills raised the question of whether to include it.

**Options:**
- A) A standalone skill
- B) Leave it out entirely
- C) An optional step in `yt-transcript` that the agent may only run after asking the user

**Decision:** C. The agent explains the deprecation, offers the alternative (metadata written from the clip's own structure), and runs the script only after an explicit yes. The command uses `< /dev/null` so the script's interactive cut prompt answers "none" (that prompt would cut square 1080x1080 clips, not 9:16 Shorts).

**Rationale:** The user keeps the choice, a new agent does not use the deprecated path by default, and the script is still documented for the cases where its output is wanted.

---

## [2026-09-22] Agent skills live in the repo, in `.claude/skills/`, independent of any vault or personal setup

**Context:** The pipeline scripts needed to be drivable from natural language. The requirement is that a fresh agent (Claude Code or opencode) started from a clone of this repo works from the first run, without a personal vault or user-level skills, and that skills change together with the scripts.

**Options:**
- A) User-level `~/.claude/skills/` (not versioned, not part of the repo)
- B) Repo `.claude/skills/`, read by both Claude Code and opencode
- C) Repo as the source plus a manual copy into the user-level folder

**Decision:** B. Three skills by workflow stage: `yt-transcript` (`correct_srt.py`, `translate_srt.py`, optional `analyze_srt.py`), `yt-shorts` (`shorts_generator.py`), `yt-rough-cut` (`daily_summary_srt.py`, `kdenlive_from_fragments.py`, `srt_from_fragments.py`). Only portable frontmatter fields (`name`, `description`, `compatibility`). Commands are relative to the repo root; no vault paths and no references to personal skills. Supporting files: `scripts/corrections_example.txt` (because `corrections.txt` is git-ignored and missing on a fresh clone), `examples/sample_RO.srt`, `docs/AGENT_SETUP.md`, and rule 7 in `CLAUDE.md` (update the skill in the same change as the script).

**Rationale:** Both tools search `.claude/skills/` from the working directory up to the git root, so one set of files serves both, and it is versioned with the code it describes. The cost: the agent must be started inside the repo (or with `--add-dir` in Claude Code). Skills describe what the scripts do today, including gotchas found while reading the code, not what older documentation claims.
