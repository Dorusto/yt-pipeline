# DECISIONS — yt-pipeline

Format: **[Date] Title** — context, options considered, decision, rationale.

---

## [2026-10-21] Mode B real folder layout and nearest-timestamp pairing

**Context:** The Second Brain recordings live in a `camera/` + `ecran/` folder pair, not the flat folder `mode_b.py` assumed, and the OBS screen recording starts 1-2 s before the camera, so exact name matching pairs only 24 of the 31 sessions. The Whisper JSONs sit in a `json/` subfolder.

**Options:**
- A) Keep the flat layout and require the user to rename files so the stems match exactly
- B) Accept both layouts and pair by the nearest name timestamp within a tolerance, falling back to the exact stem

**Decision:** B — detect the subfolder layout (`camera/` plus `ecran/` or `screen/`, names configurable under `mode_b:`), pair each camera with the screen whose parsed name timestamp is nearest within `pair_tolerance_s` (default 10 s) assigned greedily one-to-one, fall back to the exact stem, and write the measured `camera_stamp - screen_stamp` to the manifest as `sync_offset_s`. Unpaired cameras run camera-only; unpaired screens are warnings. `find_words` also searches `FOLDER/<words_dir>` (default `json`).

**Why not:** A) forces manual renames on every recording and loses the real offset; the schema already carries `sync_offset_s`, so the measured value belongs in the manifest.

**Consequence:** `mode_b.py` reads a `mode_b:` mapping from the same YAML it passes to `rough_cut.py` (which ignores unknown keys); the `--json` summary gains a `pairing` object. Words found outside `<out-dir>/words` are copied into it (the manifest keeps the found path), so `chapters.py` sees every session. No schema change. The reference check pairs 27 of 31 sessions (the old exact-name rule paired 24).

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

---

## [2026-10-12] AI Director: build in this repo, as independent tools, exporting through the existing Kdenlive generator

**Context:** DJI Mimo's auto-edit picks shots by template and beat, without understanding the footage. The goal is an editor that understands raw footage (vision + audio + speech), takes a brief in plain language and produces an editable timeline, for both action/travel footage and talking-head videos. Research in `docs/research/prior-art.md`, plan in `docs/VISION.md`.

**Options:**
- A) A new repo with a new package structure and OpenTimelineIO export
- B) This repo: new stages as standalone scripts + skills, the director writing `fragments.yaml` for `kdenlive_from_fragments.py`
- C) Adopt an existing open-source project (CutClaw, AI-Youtube-Shorts-Generator) and extend it

**Decision:** B.

**Rationale:** The Kdenlive generator already solves the hard, undocumented parts (Generation 5 structure, AV groups, DJI stream indices) and was proven on the 35-fragment Dolomiti project; OpenTimelineIO would mean re-learning them. `fragments.yaml` is already an edit decision list. The existing scripts already follow the "independent tool + skill" pattern that lets both a human and an agent drive each step. CutClaw is the closest match for Mode A but is cloud-API-first and movie-oriented; its agent loop and music-aware planning are copied as patterns instead.

---

## [2026-10-12] Models via OpenRouter; vision starts in the cloud, decided by a bake-off

**Context:** The AI Director needs a planning model, a coding model for the agents that build it, and a vision model for footage analysis. The RTX 3090 server could host a local model.

**Options:**
- A) Local Qwen3.8 27B on the 3090 for coding and vision
- B) Cloud models through OpenRouter for everything, local vision later if needed
- C) Claude for everything

**Decision:** B. Architect MiMo V2.6 Pro, coder DeepSeek V4.1 Flash (GLM 5.3 Flash as fallback). Vision: DeepSeek V4.1 Flash or GLM 5.3 Flash (both accept images), chosen in D1 by running both on the same ~10 test clips. Local Qwen on the 3090 is wired in only if cloud vision quality is weak or footage must stay local (family footage).

**Rationale:** A local 27B coder scores well below DeepSeek V4.1 Flash on agentic coding benchmarks and has a 32–64K practical context on 24 GB, which costs review time — the scarcest resource. The 3090 at full load for a month of evenings costs more in electricity than the whole build on DeepSeek (estimated $5–15). Claude for everything would cost roughly 10x. Vision for the whole Dolomiti trip is estimated at $2–3 in the cloud. Keeping the backend a config value means the bake-off result, not a guess, decides.

---

## [2026-10-12] D0 foundation: `scripts/common/`, `schemas/`, local pytest, and a CPU-only render path

**Context:** Phase D0 of the AI Director needs shared ground before any stage is built: one place for code used by more than one tool, machine-checkable interfaces, and the ability to run and test everything on the GPU-less build LXC. Several existing scripts assumed a GPU or imported `torch`/`whisperx` at startup, so they could not run or be tested there.

**Options:**
- A) No shared package; duplicate helpers per script; skip automated tests until D1
- B) `scripts/common/` package for shared code, `schemas/` for JSON Schemas, a `pytest` suite with tiny fixtures, and make the existing scripts CPU-safe
- C) A full framework/package layout now (src layout, plugin registry, OpenTimelineIO)

**Decision:** B.

**Rationale:** Matches `docs/VISION.md` principle 1 ("shared code only in `scripts/common/`") and principle 3 ("validate against schemas in `schemas/`") without inventing structure the later phases have not earned yet. A minimal `common/` (`notify`, `ffmpeg_tools`, `schema`) plus one schema for `fragments.yaml` covers what D1 actually touches. C is over-engineering for three small functions and would slow review.

---

## [2026-10-12] ffmpeg encoder is chosen at runtime with a CPU fallback

**Context:** `shorts_generator.py` hard-coded `h264_nvenc` and `DEVICE = "cuda" if torch.cuda.is_available()`. The build LXC has no GPU. Worse, its ffmpeg build still *lists* `h264_nvenc` in `-encoders` (nvenc is compiled in) even though no GPU is present, so "encoder is available" cannot be decided from `-encoders` alone.

**Options:**
- A) Always `libx264`
- B) Prefer `h264_nvenc` when an NVIDIA GPU is present, else `libx264`, and retry with `libx264` if the chosen encoder fails at render time
- C) Probe the encoder with a real one-frame render before the full job

**Decision:** B, in `scripts/common/ffmpeg_tools.py`. GPU presence is detected with `nvidia-smi -L`. `h264_nvenc` is only chosen when both the GPU is present and ffmpeg lists the encoder. `YT_VIDEO_ENCODER` and `--encoder` force a choice. `render_short` retries once with `libx264` if the chosen encoder raises.

**Rationale:** A is a silent quality/speed regression on the real editing machine that has a GPU. C doubles the encode cost for a decision that `nvidia-smi` already answers. The runtime retry covers the case where `nvidia-smi` succeeds but the driver/ffmpeg combination still fails, without pre-rendering.

---

## [2026-10-12] `fragments.yaml` is validated against a JSON Schema before generation

**Context:** `kdenlive_from_fragments.py` and `srt_from_fragments.py` are the proven export path and the AI Director's target format. A malformed `fragments.yaml` (missing `out`, a timecode in the wrong shape, an empty list) currently fails late — after some `ffprobe` calls or not at all — which is exactly what blocks unattended runs.

**Options:**
- A) Leave validation to the scripts' existing KeyError/ffprobe failures
- B) Add `schemas/fragments.schema.json` and validate in `scripts/common/schema.py` before any work, with `jsonschema`
- C) Validate with a hand-written checker

**Decision:** B. `schemas/fragments.schema.json` requires `fragments[].file/in/out` and a `HH:MM:SS.mmm` timecode, and allows extra keys (`score`, `reason`, `slot`) so the director can annotate fragments without breaking the generators. Both generators call `load_config()` which validates first and exits 1 with a clear message.

**Rationale:** `jsonschema` is the standard, is pure Python (light on the LXC) and was added to `requirements.txt` by this change (it was not a dependency before). A hand-written checker (C) would drift from the schema file and give worse error messages. Allowing extra keys implements VISION principle 4 ("extra fields are allowed; the generator ignores unknown keys").

---

## [2026-10-12] Notifications go to ntfy via the standard library, not a new dependency

**Context:** The architect/coder workflow wants a short "phase report ready" or "blocked" ping (CLAUDE.md, phase report rule). The project already runs an ntfy server.

**Options:**
- A) Use `requests` (or the installed `openai`/`httpx` stack)
- B) `urllib.request` from the standard library
- C) A shell `curl` one-liner in the agent docs

**Decision:** B. `scripts/common/notify.py` with `scripts/notify.py` as the CLI. URL from `NTFY_URL`, default `http://10.10.1.61:8090/opencode-doru`. Message is the POST body; title/priority/tags are ntfy headers.

**Rationale:** The notification is a single POST and must work even on a bare machine, so a new runtime dependency (A) is not justified. C is not testable and hides the target. Putting the function in `common/` lets later stages and the phase-report ritual share it without tool-to-tool imports.

---

## [2026-10-12] Project config: `config.yaml.example` + git-ignored `config.yaml`, model id per role, per-run LLM budget

**Context:** VISION principle 6 makes model backends config, not code, and CLAUDE.md sets a per-run LLM budget cap. Neither had a file to live in. Later stages (vision bake-off in D1, director in D2) must be able to switch models and cap spend without code changes.

**Options:**
- A) Environment variables only (`YT_VISION_MODEL`, `YT_LLM_BUDGET_USD`, ...)
- B) One YAML config at the repo root: `config.yaml.example` committed, `config.yaml` git-ignored, loaded by `scripts/common/config.py`
- C) Reuse `scripts/shorts_config.yaml` for this too

**Decision:** B.

**Rationale:** A is scattered and undocumented at a glance; the per-role `models` map reads well in YAML. C mixes per-clip editing config with per-project model routing — different lifetimes. `config.yaml` holds no secrets (API keys stay in `OPENROUTER_API_KEY`), so an example file can be committed verbatim. Roles decided so far: `architect`, `coder`, `coder_fallback`, `vision`, `vision_fallback` (OpenRouter slugs from VISION section 6). The D1 bake-off changes `vision` in this file, not in code. Unknown extra keys are ignored so later phases can extend the file without breaking old checkouts.

---

## [2026-10-12] `scripts/common/llm.py`: OpenRouter client that charges `usage.cost` per call and refuses calls at the budget cap, with a mock backend

**Context:** The spend rule ("respect the per-run budget cap in config; stop and report when reached") needs one place that every later LLM call goes through. The build machine has no API key and no reliable network, so tests need an offline client.

**Options:**
- A) Trust each tool to count its own spend
- B) One shared client in `scripts/common/llm.py`: accumulate the USD cost from each response's `usage` block, raise `BudgetExceeded` before the next call when the cap is reached; `MockClient` for offline use, selected by `llm_backend` in `config.yaml`
- C) Pre-purchase a fixed credit at OpenRouter and rely on the account balance

**Decision:** B.

**Rationale:** A repeats the accounting in every tool and forgets one eventually. C caps spend at the account level, not per run, and errors arrive as HTTP 402 after the fact — not a clear stop. B matches "model backends are config" (`llm_backend`: `openrouter` or `mock`) and reuses the `openai` SDK connection every script already uses (DECISIONS 2026-09-22), so no new dependency. Cost is known only after a call, so the call that crosses the cap still returns its result; the *next* call raises `BudgetExceeded` with the spent and budget amounts in the message. Missing `usage.cost` charges `0.0` rather than failing — token counts are still reported.

---

## [2026-10-12] D1 split into D1a (motion + quality, CPU) and D1b (describe + index)

**Context:** The first D1 plan sent every scene-detected shot to a vision model. Doru's test clips are continuous action-cam takes of 2–5 minutes with no hard cuts, full of deliberate camera moves (orbit, push/pull, pans) wrapped in setup and teardown motion (moving into position after pressing record, moving back before stopping), plus blurred and shaky stretches. Scene detection alone sees one shot per take; a vision model looking at three stills cannot reliably tell a pan from an orbit or find where a move starts and ends. Research: `docs/research/camera-motion-and-quality.md`.

**Options:**
- A) Keep one D1: scene detection + fixed 5 s segments + vision descriptions
- B) Separate tools: `motion` (optical flow / gyro, motion phases, CameraBench labels), `quality` (measured defects plus a role per segment: move, hold, transition in/out, setup, teardown, accidental — labels, never deletions), then `describe` only on kept segments; checkpoint after the CPU-only part
- C) Use the CameraBench fine-tuned Qwen2.5-VL for all motion labels now

**Decision:** B, as D1a (ingest, motion, quality) and D1b (describe, index, bake-off), each with a Doru checkpoint. The analysis tools label and never delete: one file can hold several intentional shots, and fast motion at the head or tail of a take may be half of an in-camera transition (whip pan, roll) rather than junk — the director pairs them. Techniques the tools must know are catalogued in `docs/guides/shot-grammar.md`.

**Rationale:** CameraBench shows geometric methods are reliable for pan/tilt/roll/dolly/zoom/shake and VLMs for semantic moves (orbit, tracking); combining them is both more accurate and cheaper, because junk never reaches the paid vision step. D1a costs nothing to run and Doru can verify it from memory of what he filmed. C needs the 3090 and is kept as the later upgrade for semantic labels. Fixed 5 s segments (A) cut through the middle of moves and keep the junk.

---

## [2026-10-12] PR #4 review fixes: missing `usage.cost` raises, spend ledger caps the whole job

**Context:** the D0 review found two budget holes. A response without `usage.cost` was charged `0.0`, so the cap silently stopped counting; and `spent_usd` lived only in the process, so a job that chains several tool runs could spend `llm_budget_usd` per process.

**Options:**
- A) Warn and continue on a missing cost; keep per-process accounting
- B) Raise `MissingUsageCost` on the OpenRouter backend when `usage.cost` is absent, and persist every call to a per-project `llm_spend.json` ledger that the client reads at start-up so the cap spans processes
- C) Pre-pay a fixed OpenRouter credit and rely on the account balance

**Decision:** B, in `scripts/common/llm.py`. Ledger resolution order: explicit `ledger_path` → `YT_SPEND_LEDGER` → `spend_ledger` in `config.yaml` → `YT_WORKSPACE/llm_spend.json` → none (in-process only, as before).

**Rationale:** a missing cost must not make a call free — better to fail loudly than to under-count spend; the error names `usage.cost` and never leaks the API key. The ledger makes `llm_budget_usd` a job cap, which is what the CLAUDE.md spend rule means; atomic writes (`os.replace`) keep the ledger consistent when a run is interrupted. C caps at the account level and surfaces as HTTP 402 after the fact.

---

## [2026-10-12] D1a motion labels: CameraBench geometric vocabulary, image motion inverted, dolly flagged for semantics

**Context:** `motion` must name camera moves from 2D optical flow. Flow measures how the *picture* moves; the label must say what the *camera* did. Geometric methods cannot separate dolly from zoom (or orbit from pan+truck), per `docs/research/camera-motion-and-quality.md`.

**Decision:** the `motion.schema.json` label set is the CameraBench geometric vocabulary (`static`, `pan_left/right`, `tilt_up/down`, `roll_cw/ccw`, `dolly_in/out`, `zoom_in/out`, `truck/pedestal`, `complex_motion`). Labels invert image motion: image `dx < 0` → `pan_right`, image `dy > 0` → `tilt_up`, image `roll_deg < 0` → `roll_cw` (positive `roll_deg` is image-clockwise). Scale change is labelled `dolly_in/out` with `semantic_check: true` so the D1b vision step resolves dolly vs zoom; `zoom_*` and `truck/pedestal` stay in the vocabulary for the gyro/vision upgrades. The dominance contest uses the raw rate for dolly (`|log scale| per second`) instead of the `/0.25` speed normalisation, so a 5 %/s drift cannot outscore a real pan.

**Rationale:** this is the vocabulary the director and the shot-grammar guide speak, and the inversion keeps the naming consistent with filming language. Flagging only the truly ambiguous case keeps the paid vision step small.

---

## [2026-10-12] Motion segmentation: split on dominant-type change with hysteresis and per-channel significance floors

**Context:** the first D1a implementation labelled a moving run from its mean over the whole run. On real footage one `dolly_in` swallowed 157 s of a trip clip that contains fast whips and pans, and the dolly score activated below 1.25 %/s — under the parallax/stabilisation drift of walking footage — so 36 of 39 moves were dolly and none were pans. Verified against sliding windows and frame-level image shifts.

**Options:**
- A) Keep run-mean labels, retune thresholds only
- B) Label every sample from the smoothed series, split runs where the dominant component+sign changes (0.6 s hysteresis, 0.4 s short-run merge), and gate each channel with a significance floor (pan/tilt 0.03 fw/s, roll 5 deg/s, dolly 0.05 log/s **plus** 70 % sign-consistency in the window)
- C) Send everything to a neural shot/motion detector

**Decision:** B (implemented in `scripts/motion.py`, audit round 2).

**Rationale:** the research explicitly asks for splitting "where the dominant motion type changes"; the floors kill estimator noise without hiding real moves, and the dolly sign-consistency test is what separates a real push-in from drift. C costs GPU and model downloads for what geometry already answers for pan/tilt/roll. After the fix the 21-clip set yields 56 moves across all eight geometric labels, and clip 0072's whips appear as their own `pan_*` phases at the right times (checked against frame content).

---

## [2026-10-12] Quality horizon measured from edge angles, not from integrated flow roll

**Context:** `quality` needs "horizon tilt (roll offset on a static shot)" per segment. The first version cumulatively summed the per-pair `roll_deg` of the motion trajectory; the estimator drifts and the drift compounds, reporting holds at ±30° where the frames differ by ~1.5° (checked with Canny+Hough on the actual frames).

**Options:**
- A) Keep the integrated flow roll and accept the drift
- B) Measure the tilt directly per sampled frame: Canny + `HoughLines`, median line angle relative to horizontal; `horizon_deg` per segment = its frames' mean minus the clip's median
- C) Gyro-derived roll (needs the raw files and a gyro parser)

**Decision:** B, in `scripts/quality.py` (audit round 2). Fewer than 30 Hough lines → frame not measured; segments without measurements fall back to `0.0`.

**Rationale:** B is a real measurement of the picture, cheap on CPU, and needs no motion file; cross-checks on the test clips agree with independent edge measurements (11.5° reported vs 10° measured on clip 0067; -10° vs -9° on clip 0080). A is a random walk presented as a fact. C is the later upgrade and needs footage these proxies do not carry.

---

## [2026-10-13] Docstrings allowed, inline comments not

**Context:** Rule 5 said "Comments in code → none". The D0 and D1a code has a docstring on every public function. The external audit flagged the mismatch and asked Doru to decide.

**Decision:** Short docstrings on public functions and modules are allowed; inline comments stay out. CLAUDE.md rule 5 updated.

**Rationale:** The code is written and read by agents that did not write it; a one-line contract per function makes briefs and audits cheaper. Inline comments still tend to drift from the code, so the self-documenting-names rule stays for everything else.

---

## [2026-10-13] D1a not approved at checkpoint; ground-truth eval before more tuning

**Context:** Doru reviewed 6 clips against what he filmed (`eval/ground_truth_boat_2026-10-12.yaml`). The tools mislabel boat motion (wave bob as tilt, travel as dolly or complex), confuse pan with truck and orbit, report a tilted horizon on level shots, and see nothing inside a locked-off hold where another boat approaches. Details in `docs/reports/D1a-CHECKPOINT.md`.

**Decision:** A D1a-2 round before D1b: first an eval tool that scores the tools against ground-truth YAML, then platform motion, parallax (pan vs truck, orbit candidates), a horizon measured only on the sea/sky line, scene activity and events inside holds, and the `accidental` role, each judged by the eval score.

**Rationale:** Without ground truth the agents verified the horizon detector with the same kind of edge measurement and confirmed its own error. A small eval set from the person who filmed the footage turns "looks plausible" into a number every later change must improve, and it grows with each new shoot.

---

## [2026-10-13] D1a-2: ground-truth eval tool first, every fix judged by its score

**Context:** the D1a checkpoint rejected the labels on boat footage ("the tools work end to end, but the labels miss what matters") and listed seven fixes. Without a number, "looks plausible" had already confirmed the horizon detector's own error once.

**Options:**
- A) Fix the seven items directly and re-review by eye
- B) Build `scripts/eval_labels.py` first, record a baseline score against Doru's ground-truth YAML, then require every fix to move the score
- C) Reuse an existing video-metric framework

**Decision:** B. The scorer lives in `scripts/eval_labels.py` with `schemas/eval.schema.json` and the `yt-eval` skill. Scoring: interval overlap of expected vs predicted move/role with full credit for an exact label and 0.5 for a geometric look-alike (`dolly_in` for `travel_forward`, `pan_right` for `truck_right`, `pan_*` for `orbit`); horizon false positives against `horizon: level` truth (defect or |deg| > tol counts as a tilt claim, `null` abstains and never scores as success); event recall within ±3 s. `totals.overall` is the mean of the per-clip overalls. Baseline 0.26 → final 0.69 with every intermediate step recorded in `docs/reports/D1a-REPORT.md`.

**Rationale:** the eval set is small, lives in `eval/` (never with footage) and grows with each shoot; it turned seven subjective fixes into seven measurable ones. C has no ground-truth concept and would not have caught the horizon false positives.

---

## [2026-10-13] Platform bob is an annotation, travel is a semantic upgrade of dolly

**Context:** on a boat, wave bob (period 1.5–8 s) was split into `tilt_up`/`tilt_down` moves and long smooth travel on the platform was labelled `dolly_in`/`dolly_out` — both geometrically defensible and wrong in filming language.

**Options:**
- A) `platform_bob` as a motion label replacing the tilt labels on those spans
- B) `platform_bob` as a per-move annotation `{period_s, amplitude}` after subtracting the fitted oscillation from `dy`/`roll_deg` before labelling
- C) Wait for the gyro path to identify platform motion

**Decision:** B, plus upgrading sustained smooth scale runs (≥ 8 s, `no_shaking`/`minimal_shaking`, sign-consistent) from `dolly_in`/`dolly_out` to `travel_forward`/`travel_backward` with `platform: vehicle` and the CameraBench label kept in `label_geometric`. `amplitude` is the `dy` peak-to-peak as a fraction of frame height (no FOV guess in degrees).

**Rationale:** ground truth expects `hold` where the boat bobs (`0081 00:40–01:10`) and `travel_forward` where it enters the cave — a `platform_bob` *label* would fail both; an annotation keeps the underlying camera label and still reports the oscillation instead of fake tilts. Detection thresholds (`BOB_AMPLITUDE_MIN = 0.008`, autocorrelation floor, 4 s gap bridging, per-window sinusoid fits because the period drifts) were tuned so clip 0081 is detected and the clean locked-off clip 0083 and the 0091 orbit are not. C would leave the whole proxy set blind.

---

## [2026-10-13] Parallax from the homography residual: pan vs truck and orbit candidates

**Context:** ground truth distinguishes `truck_right` (lateral slide with parallax) from `pan_right` (rotation) and `orbit` (pan + strong parallax + subject centred); the geometric labels alone cannot.

**Options:**
- A) Leave pan/tilt/dolly as-is and push everything semantic to the D1b vision step
- B) Per-sample homography fit on the tracked matches: pure rotation fits, translation with depth does not; median reprojection residual normalised by analysis width → `parallax` score 0..1
- C) Gyro-derived translation (needs the raw files)

**Decision:** B. `parallax = min(1, residual_norm / 0.02)` on every move (median over the steady window). Lateral moves at `parallax >= 0.5` relabel to `truck_right`/`truck_left` (25–0.5 keeps `pan_*` — a wrong truck costs more than a missed one); the same signal plus a centred-subject test (centre-box residual ≤ 0.35 × periphery) proposes `orbit_candidate` with `semantic_check: true`. Precedence: `travel_*` > `orbit_candidate` > `truck_*` > `pan_*`.

**Rationale:** this is the same flow data the pipeline already computes (`cv2.findHomography`), so it costs no new dependency and no GPU; "candidate" keeps the D1b vision step as the semantic arbiter while removing the worst geometric confusions (0072 truck, 0091/0081 orbit).

---

## [2026-10-13] Horizon: only a dominant long near-horizontal line, else null

**Context:** the D1a detector took the median angle of *all* edges and normalised against the clip median; mountain slopes and wake lines became a 5–11° tilt with a `horizon` defect on level shots, and the original "cross-check" repeated the same error. The checkpoint demands `null` when there is no sea/sky line.

**Options:**
- A) Keep edge medians, only retune thresholds
- B) HoughLinesP candidates kept only when long (≥ 25 % of width), near-horizontal (≤ 15°) and angle-clustered (median absolute deviation ≤ 3°); median signed angle vs true horizontal; `null` otherwise; defect only above 5°
- C) Sky-vs-water texture test around the candidate line

**Decision:** B (an experimental y-intercept clustering criterion was tried and reverted: it made clip 0067 abstain and crashed the horizon score 0.97 → 0.00). The defect threshold `HORIZON_MIN = 5.0` matches the eval's default tolerance exactly, so a measured level horizon can never be a false positive there.

**Rationale:** B passed the acceptance (0067 `00:00–00:30` level at −0.45°, horizon score 0.38 → 0.97). C was measured on real frames and does not separate the cases (both sides of a sea line can be textured). Known limitation, documented in the `yt-quality` skill: when no horizon is in frame at all (0081's boat interior), long parallel non-horizon edges at 6–12° can still be read as horizon — indistinguishable per-frame in 2D; the D1b vision step is expected to veto such segments.

---

## [2026-10-13] Scene activity from residual local flow on the scene band; events per floor-level span

**Context:** a locked-off hold can contain events (0083: another boat enters and approaches) that the camera-motion labels rightly ignore. The checkpoint asks for per-second `scene_activity` and events (subject enters / approaches / leaves).

**Options:**
- A) Activity from raw frame difference (already computed for `static` detection)
- B) Residual local flow after removing the fitted global camera motion, measured on the upper 70 % of the frame (scene band), aggregated into one-second bins; events derived per floor-level active span
- C) Vision-model event detection (D1b)

**Decision:** B, in `scripts/motion.py` (it owns the flow). The residual is computed on all tracked matches, not just the RANSAC inliers — a moving subject *is* an outlier to the global fit, so an inlier-only residual cannot see it. Event types are `activity_start`/`activity_rise`/`activity_peak`/`activity_end`, renamed to `subject_enters`/`subject_approaches`/`subject_leaves` only when the pattern is clear-cut; one event set per floor-level active span (a later fix: spans that dipped below the high threshold but not the floor re-emitted identical events — 0072 had 14 duplicates). Acceptance met: 0083 reports starts/peaks/ends at 8/28/35 s against ground truth 10/32/38 s.

**Rationale:** A conflates camera motion with scene motion on a moving platform; C is the paid step and should see only what survives cheap geometry. The scene band keeps foreground wake from dominating boat footage (constant `SCENE_BAND_FRACTION = 0.7`, tuned on this set — revisit on walking footage).

---

## [2026-10-13] `accidental` from long chaotic runs and junk-take coverage

**Context:** clip 0051 ("unusable clip, makes no sense") was labelled hold/move/hold with a `complex_motion` middle. The checkpoint wants `accidental` for a whole clip or long run with very high shake and no stable move or hold.

**Options:**
- A) Per-segment shake thresholds only (extends the existing obstruction/uniform triggers)
- B) Run rule: maximal runs of unstable segments (raw shake ≥ 0.1 or `unsteady`/`very_unsteady`) that are not stable content (≥ 2 s, low shake, clean move/hold) become `accidental` when the run lasts ≥ 5 s or ≥ 70 % of the clip; ≥ 70 % run coverage marks the whole take
- C) Train a junk classifier

**Decision:** B. Whole-clip verdict: confidence 0.65 with `junk take: Xs of Ys has no stable move or hold`; run verdict: confidence 0.6. Healthy travel segments (shake 0.007–0.012) are stable content by construction; short shaky slivers (0060, 0064) stay under the run threshold.

**Rationale:** the thresholds are grounded in the measured shake distribution (0051's middle 0.134 vs 0.007–0.012 on real moves) and only *upgrade* roles to `accidental` — labels only, never deletions. A literal "no stable content anywhere in the run" reading was tried first and would have marked deliberate mid-take moves (0072's `dolly_out` at 00:10) accidental; the unstable-AND-not-stable formulation keeps every other clip untouched.

---

## [2026-10-13] PR #7 audit fixes: flock on the spend ledger and quality segment hygiene

**Context:** the external audit on PR #7 found four correctness issues: the spend ledger loses entries and checks the cap against a stale total under concurrency; `setup`/`teardown` fire mid-take because head/tail is a time window; sub-second slivers become segments (`end_s == start_s` allowed by the schema); and role `move` can carry label `static`.

**Options:**
- A) Fix each in place without changing contracts
- B) Fix in place and tighten the contracts: `fcntl.flock` on a sidecar lock with re-read before check/append; head/tail defined as before the first / after the last content segment (two-pass: content candidates identified position-independently); slivers merged into a neighbour with `end_s > start_s` asserted and `end_s` `exclusiveMinimum: 0` in the schema; a `static` label can never be role `move`
- C) Rewrite the quality segmenter

**Decision:** B. The lock file is a sidecar (`llm_spend.json.lock`) because the ledger itself is replaced with `os.replace` and its inode changes. A 4-process race test shows entries lost and crashes without the lock.

**Rationale:** all four are contract bugs, not modelling choices; C would throw away the two-pass role logic that the ground-truth eval is already scoring.

---

## [2026-10-13] Packaging and publishing become D6; publish gate after D2

**Context:** VISION listed auto-upload and voice cloning as out of scope. Doru's vault project (`70_ai-youtube-automation-pipeline/20_TODO.md`) already plans them, with Remotion motion graphics, thumbnails with a brand kit, audio/SFX/music and PeerTube, as chunks 3–7. His own rule is not to open the next chunk before the previous one is filmed and published.

**Decision:** These become phase D6, one standalone tool each, after D5. A publish gate sits after D2: once the director edits the Kotor footage on its own, the devlog clip is filmed and published before D3 starts.

**Rationale:** Same tool-per-stage design, so they join without touching earlier tools. The gate keeps the build tied to published content, which is what the project exists for.

---

## [2026-10-13] `edit.json` is the edit list; Kdenlive becomes an optional export; render and preview from the browser

**Context:** VISION principle 4 made `fragments.yaml` → Kdenlive the output of the director. Doru edits from a phone while travelling and wants to drop the dependency on a desktop editor: preview the edit and make small adjustments in the browser, final render on the homelab. He also wants automatic story graphics for tutorials and talking-head (Hasan's MIT Remotion kit as a base, paid services swapped for ours), and music/SFX without ComfyUI. Research: `docs/research/rendering-and-audio.md`.

**Options:**
- A) Keep `fragments.yaml` → Kdenlive as the main output, add an ffmpeg preview
- B) New `edit.json` schema as the single source of truth; adapters for ffmpeg render, browser preview/light editor, graphics engine and the optional Kdenlive export (via `fragments.yaml`, reusing `kdenlive_from_fragments.py`)
- C) Adopt OpenCut (MIT, browser editor) as the editor now

**Decision:** B. Plain montage renders with ffmpeg. Graphics go through a `GraphicsEngine` interface; Remotion (free for a single creator, reuses Hasan's kit) vs HyperFrames (Apache-2.0, agent-native) is decided by a bake-off at the start of D4; Revideo (MIT) is a fallback, Motion Canvas excluded (no server render API). Music, SFX and clean-up are standalone Python tools (`audio_clean`, `music`, `sfx`, `beats`), one model loaded at a time, no ComfyUI. Excluded for licence reasons: MusicGen, AudioGen, MMAudio weights (non-commercial), HunyuanVideo-Foley (excludes the EU). A router picks the stations per brief and content type, so vacation, tutorial and talking-head share one tool.

**Rationale:** One schema with many readers keeps every tool independent and lets the output side change without touching the director. Kdenlive stays one command away, so nothing proven is lost. C is not possible yet: OpenCut's rewrite is at scaffold level and has no project import API; re-evaluate when its Editor API ships. ComfyUI's value is the graph UI and VRAM management; files between tools replace the graph and "one model per process" replaces the VRAM manager.

**Changes:** VISION principles 4, 6, 7, architecture, backlog, phases D2/D3/D4/D6. `fragments.yaml` stays the input format of `kdenlive_from_fragments.py`; `schemas/fragments.schema.json` is kept for that adapter.

---

## [2026-10-13] Time-box D1a-2, add slice S1 before D1b, and five plan additions

**Context:** The plan now spans D0–D6 and about twenty tools. Doru's past projects sank in development without a deliverable. D1a-2 (motion/quality accuracy) could be polished indefinitely, yet nobody knows which analysis errors hurt an actual edit, because no edit exists yet.

**Decision:**
- D1a-2 ends at ≥ 70% correct move type on ground-truth seconds plus 0051 = `accidental`, or after one pass over its eight items; leftovers become known issues.
- New slice **S1** after the D1a checkpoint: a crude Kotor recap (minimal director on D1a labels, cheap descriptions, ffmpeg MP4 to the phone). Its faults set the priorities for D1b and D2.
- Wind/noise detection joins the analysis, and `audio_clean` moves from D6 to D2: on action footage ambient sound is part of the shot.
- Principle 8: web editor and services tailnet-only; footage leaves the homelab only as frames to the configured vision model.
- D3 adds a synced footage inbox that `ingest` picks up on its own (sync tool decided by its own entry).
- Every `--json` summary reports wall time and LLM cost.
- D6 capstone: Hermes (Telegram) as remote control.
- Music: YuE2 first (editable ABC score → tempo and length chosen up front, beat sync by construction); ACE-Step 1.5 fallback only (noise floor in Doru's test). SFX stay with the CC0 library + Stable Audio Open; YuE2 makes no sound effects.

**Rationale:** An early end-to-end result turns analysis tuning from guesswork into a ranked list. The other items close gaps between the plan and how Doru actually films and travels.

---

## [2026-10-13] Agent watchdog: cron script for spend and stalls; the architect reports repeated failures

**Context:** ntfy already reports permission requests, OpenCode errors, phase reports and the daily external check. It stays silent when the agent burns money in a loop, sits stuck for hours, or keeps failing audits — the cases Doru most needs to hear about while travelling.

**Options:**
- A) Parse OpenCode logs for everything
- B) Small cron script for what can be measured from outside (OpenRouter `usage_daily` for the key, `opencode` process alive + newest file change); the architect itself reports repeated audit failures and empty coder replies, since only it knows them
- C) Rely on the morning check

**Decision:** B. `scripts/agent_watchdog.py`, every 30 minutes from a separate `master` worktree, thresholds $3/day and 2 h; one alert per problem (per UTC day / per stall episode). Architect rule: notify on the second failed audit of a task or two empty coder replies.

**Rationale:** A depends on an undocumented log format that changes between OpenCode versions. C reacts up to a day late. B uses stable signals and costs nothing. A dead LXC still cannot alert itself; the morning check covers that.

---

## [2026-10-13] D1a approved after D1a-2; carried into S1

**Context:** D1a-2 reached its time-box (all eight items done once; 0051 is `accidental`) with the ground-truth eval at 0.69 overall and 0.53 on move type. Doru approved D1a.

**Decision:** Close D1a and start S1. Known issues carried as notes, not as another D1a round:
- Scene events report many near-zero "subject_enters/leaves" (0072 has 11); the eval scores event recall only. S1 adds precision to `eval_labels.py` and a noise floor to events.
- Thresholds were tuned on the six ground-truth clips; S1 reports labels on the other clips so Doru can spot overfitting while watching the recap.
- 0067 travel_backward (stern-facing camera, see the ground-truth note): measure radial contraction toward the vanishing point on the shore regions (the shores of the strait converge toward the centre) and exclude the wake band at the bottom of the frame before the travel rule; otherwise leave it to D1b vision.
- 0081 interior horizon false positives and short orbit: D1b vision veto.

**Rationale:** The time-box exists so analysis tuning is driven by what hurts an actual edit, which S1 shows.

---

## [2026-10-13] S1 recap: labels-only director, no vision descriptions; content-time buckets

**Context:** S1 needs a watchable ~90 s recap from one brief ("about 90 seconds,
chronological, calm start, more energy towards the end, ambient sound only").
The brief allows cheap vision descriptions (DeepSeek V4.1 Flash, 2 USD cap).
Wall-clock buckets turned out empty on this set: the 21 clips span 11:36–14:19
but hold only ~689 s of content, so 7 of 15 buckets fell in the lunch gap and
the first director run produced 38 s.

**Options:**
- A) Director on labels + vision descriptions per kept slot
- B) Director on labels only (`description: null`), reasons built from role,
  label, steadiness and defects; buckets over content time (clips laid
  end-to-end in chronological order) with a fill pass that tops the edit up to
  the target length
- C) Director with a full screenwriter/editor/reviewer loop (D2 scope)

**Decision:** B. No vision model was called in S1 (phase LLM spend 0.00 USD of
the 2 USD budget; descriptions join at D1b where they are the point). Pacing
position still comes from wall-clock creation times, so a calm morning and an
energetic afternoon survive content-time bucketing. `edit.json` is written per
`schemas/edit.schema.json`; `render.py` is a plain ffmpeg adapter (xfade
crossfades 0.3 s, ambient audio, 1280x720 from the proxies). The Kdenlive
export stays D2.

**Rationale:** S1 exists to show which analysis errors hurt an edit; a vision
model would blur that signal and spend budget before D1b's bake-off. C is D2.
Content-time buckets are what "spread the recap over the footage" means when
the footage has gaps.

---

## [2026-10-13] Event noise floor at 0.001 and event start above the floor

**Context:** Carry-over from D1a: scene events report many near-zero
"subject_enters/leaves" (0072 has 11; notes read `activity 0.0000 above
floor`). The first S1 attempt gated spans on peak amplitude and moved the start
event to the first sample ≥ `floor + 0.002` above the floor. That removed the
blip spans but pushed 0083's start (ground truth: boat enters at 00:10) from
8 s to 17 s — a regression on the only clip with ground-truth events.

**Options:**
- A) Span peak gate only (start stays on the bare floor crossing)
- B) Span peak gate + start at the first sample meaningfully above the floor,
  with the amplitude set at 0.002
- C) Same as B with the amplitude set at 0.001

**Decision:** C, `ACTIVITY_EVENT_MIN_AMPLITUDE = 0.001` in `scripts/motion.py`
(residual displacement as a fraction of frame height). Measured on the Kotor
set: baseline noise excess over the floor ≤ 0.0006, blip spans peak ≤ 0.0008,
real event onsets ≥ 0.0011 (0083 crosses 0.001 at 11 s, ground truth 10 s).
The start event is placed at the first sample at least 0.001 above the floor,
rise events need a rise of at least 0.001, and a span whose peak stays below
0.001 emits nothing. End notes report the peak they fell from.

**Rationale:** 0.002 sat inside the real slow-onset range and 0.001 still
clears every measured blip; the eval is the judge and 0.001 keeps 0083 within
tolerance. 0072 keeps its 11 events — its spans have real peaks — which the
S1 report lists as a known issue for D1b.

---

## [2026-10-13] Eval events score is F1 with one-to-one matching

**Context:** `eval_labels.py` scored events as matched / ground-truth events
and allowed one predicted event to match several ground-truth events. On 0083
the event at 35 s matched both 32 s and 38 s, so events scored 1.00 while the
peak event was 4 s early (outside the 3 s tolerance).

**Options:**
- A) Keep recall, add precision as a reported side number
- B) One-to-one matching (closest pairs first, each side used at most once) and
  F1 as the component score, with recall and precision reported alongside
- C) Widen `--event-tol-s` until 0083 reaches 1.00

**Decision:** B. The report now carries `recall`, `precision`, `f1`,
`matched_count`, `predicted_count`, `gt_count` per clip and `meta.wall_time_s`
/ `meta.llm_cost_usd` for the run. 0083 scores F1 0.67 (2 of 3 matched) and the
run overall drops from 0.69 to 0.66 — the honest number. C would tune the
tolerance to the test set.

**Rationale:** A false-positive detector must not score 1.00; the noise-floor
half of the same carry-over exists precisely because precision was invisible.

---

## [2026-10-13] No more hand rules per special case: `uncertain` labels go to vision; editor corrections grow the ground truth

**Context:** 0067 (stern-facing camera in a strait) needed a dedicated geometric rule. Doru: there will be many such shots; each would need another rule.

**Decision:**
- `motion` and `quality` attach a `confidence` to each label and emit `uncertain` when their signals disagree or the frame has large self-moving regions (wake, traffic, water). In D1b, `describe` adjudicates every `uncertain` segment and its label wins in `shotlog.json`. Geometry measures; it does not guess.
- In D3, every label Doru corrects in the web editor is appended to the ground-truth set, so `eval_labels.py` scores against real footage that grows with use.
- New hand-written geometric rules are added only when a case recurs and the eval shows it hurts edits.

**Rationale:** Matches CameraBench (geometry for primitives, VLMs for semantics) and turns the long tail of special cases into data instead of code. Gyro from SD-card originals remains the upgrade for rotation.

---

## [2026-10-13] S1 review: content score, diversity and shot length go into D1b

**Context:** Doru watched the S1 recap (85 s, 18 slots, labels only, no vision). Verdict: static shots are chosen very well; some shots are too long, others too short; near-duplicate shots appear, sometimes after a gap; the best moments (orbit 0091, approaching boat 0083, cave 0081, tunnel 0072) are missing because the director only knows "stable", not "beautiful".

**Decision:** D1b delivers, besides `describe` + `index` + the vision bake-off:
- a content score per segment from the vision model (subject, scenery, interest, "hero shot" flag) feeding the shotlog score;
- visual similarity between segments (image embeddings, choice by DECISIONS entry with at least two options) and a director rule: no two similar segments in the edit unless the brief asks for it, never back to back;
- shot length from pacing and content: holds and moves get a target length from the brief's pace (calm vs energetic) and a min/max, cut inside the steady range;
- the D1b checkpoint artifact is a re-rendered recap with the same brief, compared with S1.

**Rationale:** S1 showed that the remaining errors in motion labels hurt the edit far less than the absence of content understanding, repetition and uneven pacing.

---

## [2026-10-13] D1b vision bake-off: GLM 5.3 Flash is the default vision model

**Context:** VISION §6 leaves the vision backend to a bake-off between DeepSeek
V4.1 Flash and GLM 5.3 Flash on image input. D1b ran both through
`scripts/describe.py` on the kept segments of five varied Kotor clips (0067,
0072, 0081, 0083, 0091 — 32 segments, 8 uncertain) with the same prompt and
frames. Full results: `docs/reports/D1b-vision-bakeoff.md`.

**Options:**
- A) `deepseek/deepseek-v4.1-flash` as default
- B) `z-ai/glm-5.3-flash` as default
- C) route per segment type (uncertain verdicts to one, captions to the other)

**Decision:** B. `models.vision = z-ai/glm-5.3-flash`, `models.vision_fallback =
deepseek/deepseek-v4.1-flash`. Measured: GLM 32/32 parse rate vs DeepSeek 30/32
(DeepSeek returns `content: null` when its reasoning trace eats the output
budget — it needs `max_tokens` 8000 per segment and still loses answers);
0.0066 USD vs 0.0590 USD for the same 32 segments; verdicts on the three
uncertain segments with clear ground truth that GLM got right and DeepSeek did
not (0067 wake → `travel_backward`, 0081 s7 → `travel_forward`, 0081 s15 →
`travel_backward`); GLM sets `hero` on the tunnel and cave moments Doru named
in the S1 review, DeepSeek never sets it.

**Rationale:** The whole point of `uncertain` is that vision adjudicates where
geometry gives up — losing the answer on the wake segment is disqualifying,
and a 9x cost difference buys nothing back. C is premature: one model does both
jobs well. `VisionModel` keeps the backend a config value, so the local
Qwen3.8 on the 3090 can still take over for private footage.

**Side finding recorded for the builders:** DeepSeek-style reasoning models
need far more output tokens than a plain VLM; `scripts/common/vision.py`
defaults `max_tokens` to 8000 and `describe.py` treats `content: null` as an
unparseable answer instead of crashing.

---

## [2026-10-13] Segment visual similarity: MobileNetV2 ONNX through cv2.dnn, not histograms

**Context:** D1b needs visual similarity between quality segments so the
director can refuse near-duplicate shots ("no two similar segments in the edit
unless the brief asks for it, never back to back" — S1 review). Measured on the
32 kept segments of the five bake-off clips (0067, 0072, 0081, 0083, 0091),
same-scene vs cross-scene cosine gap as the score.

**Options (both CPU-only, no GPU):**
- A) **Handcrafted numpy descriptor** — HSV hue-saturation + gradient-orientation
  + luma histograms per frame, mean over the segment's 3 frames. Zero
  dependencies, zero download, 0.5 s for 32 segments. Same-scene 0.945 vs
  cross-scene 0.823 (gap 0.122) — and everything lands between 0.99 and 0.5, so
  no usable threshold. pHash (also measured) is worse: gap 0.087, only catches
  near-identical frames.
- B) **MobileNetV2-12 ONNX via `cv2.dnn.readNetFromONNX`** — ImageNet logits as
  the embedding (1000-d, L2-normalised, mean over the 3 frames). No new Python
  dependency (OpenCV 5.0.0 already ships the DNN engine), one 14 MB model file
  downloaded once to `~/.cache/yt-pipeline/models/` (ONNX model zoo,
  `validated/vision/classification/mobilenet`, the model is a standard release
  artifact). 0.8 s for 32 segments. Same-scene 0.811 vs cross-scene 0.579
  (**gap 0.232**) — nearly 2x the separation of the histogram.

**Decision:** B, with A kept as the offline/no-model fallback backend of the
same tool (`similarity.py --backend {mobilenet,hist}`; tests run on `hist`, the
default is `mobilenet`). Adjacent cave segments of 0081 score 0.93–0.97 and the
tunnel approach + hold of 0072 scores 0.98 — exactly the duplicate pairs the
S1 edit repeated. Threshold for "similar" starts at 0.90 and stays a flag.

**Rationale:** The director rule needs a threshold that separates "same shot
again" from "same trip, different shot"; only the CNN embedding gives one.
`cv2.dnn` avoids a new dependency (onnxruntime would have been the alternative,
MIT and well-maintained, but adds nothing once OpenCV runs the same file).
torch stays out (D0: no torch unless needed). CLIP is the upgrade path when
text search ("serpentine at sunset") lands at D5 — it needs a ~350 MB encoder
and is not needed for pair similarity now.

---

## [2026-10-13] D1b shotlog score: 0.55 vision + 0.45 labels, verdict wins on uncertain segments

**Context:** VISION leaves the fusion at "weighted, per mode in config, starting
from `0.40·vision + 0.35·audio_energy + 0.25·text`". In D1b the audio and text
tools do not exist yet (they land in D2/D4), while the S1 review added a
content score from the vision model and the quality/motion labels carry
confidences. `scripts/index.py` has to produce a usable ranking now.

**Options:**
- A) Keep the VISION weights and renormalise over vision only (`score =
  interest/100`) — labels would not influence the ranking at all
- B) `score = 0.55·vision + 0.45·labels`, with `vision = interest/100` (+0.15
  for a hero shot) and `labels` the S1 director's label score
  (`ROLE_BASE + 0.25·confidence + steadiness − defect penalty + 0.10 when
  clean`), weights as CLI flags with these defaults; `audio_energy` and `text`
  reserved in the schema at 0.0
- C) Wait for the D2 audio station before the shotlog exists

**Decision:** B. The schema's `weights` object carries all four components so
the VISION formula arrives unchanged when audio and text exist; the per-mode
config weights land with the D2 router (they are a router concern). On an
uncertain segment the vision **verdict** supplies the effective `role` and
`motion_label` (`role_source` / `label_source: "verdict"`) — that is the D1b
rule "its label wins in the shotlog". Every shot carries `score + hook + reason`
and `similar_to` from `similarity.json`; confident accidental/setup/teardown are
`eligible: false`. Time-overlapping shots (>50% of the shorter) keep the higher
score and the loser gets `suppressed_by` — it does not fire on the current
tiling but keeps the VISION rule alive.

**Rationale:** S1 showed label-only ranking misses the beautiful moments and
pure-vision ranking would ignore measured defects. 0.55/0.45 makes the content
score dominant without discarding what the geometry measured. C would leave the
S1 problems (repetition, missing best moments) unfixed until D2.

---

## [2026-10-13] Missing vision in the shotlog gets a neutral prior of 0.5

**Context:** The first D1b shotlog ranked two undescribed clean holds at 0.925
above the described hero shots (0.911) — the S1 failure mode ("stable, not
beautiful") reappearing whenever `describe` did not cover a segment. The cause
was the degradation rule `score = labels` when the vision component is absent.

**Options:**
- A) keep `score = labels` and let the director prefer described shots
- B) treat missing vision as an average content score (`vision = 0.5`) in the
  fusion, while `scores.vision` stays `null` to report coverage
- C) exclude undescribed shots from the ranking entirely

**Decision:** B, applied to every eligible shot. On the full 21-clip run it
re-orders the top 10 to cave and tunnel heroes (0080/s1 0.911, 0072/s7 0.895,
0081/s7 0.862) with no undescribed shot above 0.75. `describe.py` also retries
once on unparseable model output before skipping a segment — the flaky GLM
answers that caused two of these gaps parse fine on a second call.

**Rationale:** An undescribed shot is unknown, not bad and not perfect; 0.5 is
the honest prior and it is what the interest scale means (50 = average). C
would throw away geometry-good shots because a model call failed.

---

## [2026-10-13] D1b approved; Doru's recap feedback becomes the first D2 tasks

**Context:** Doru watched `recap_d1b_720p.mp4` next to S1 and read the 0067 and 0081 descriptions. Verdict: shorter shots are better; descriptions are correct. Problems:
1. A slow pan left was cut mid-move and followed by the stable shot it was landing on — a jump cut. Some static holds should be kept longer.
2. The describe JSON is dense; hard to see what the director should choose from.
3. The orbit at the end shows only its start; 2–2.5 s is too short for a stable, important move.
4. He cannot tell which shots are flagged hero.

**Decision (D2, first tasks):**
- A move and the hold it lands on form one phrase: either keep the move through its landing (cut inside the hold), or use only the hold; never cut out of a move and into its own landing. Two slots from the same take need a different take between them unless they are contiguous.
- Hero shots and complete semantic moves (orbit, reveal, travel through a cave/tunnel) get their whole steady range up to a cap (default 8 s); pacing shortening applies only to ordinary shots.
- `describe` adds a one-line `summary` (≤ 12 words) and a short tag list; the full description stays for the LLM director.
- `index` writes a `shotlog.html` contact sheet in the workspace (thumbnail, time range, summary, score, hero, eligibility), so Doru can review on the phone. Generated, never committed.
- `eval_labels.py` scores the final shotlog label (verdict when present), not only the geometric one.

**Rationale:** The first two are classic editing grammar (cut on completion of action; avoid same-scene jump cuts) that the rule-based director lacked. The contact sheet turns checkpoints into a phone review instead of reading JSON.

---

## [2026-10-13] D2-01 phrase implementation: the phrase scores as its move, head trim, best-of-take

**Context:** Implementing the D1b recap feedback ("a move and the hold it lands on
form one phrase: either keep the move through its landing (cut inside the hold),
or use only the hold; never cut out of a move and into its own landing. Two slots
from the same take need a different take between them unless they are
contiguous"). Three implementation choices had to be made, each measured on the
21-clip Kotor workspace.

**Options for the phrase score (decides whether the phrase or the hold alone
survives `dedupe_candidates`):**
- A) `max(move, hold)` — the phrase always wins; a defective move gets dragged in
  to reach a good landing (0060 s3: move 0.36 + hold 0.691).
- B) `(move + hold) / 2` — a hero move loses to a slightly better hold from the
  same take (0080 travel 0.911 diluted to 0.876 vs its own hold 0.884).
- C) `move_score` — the phrase is the move's shot, the landing is grammar; "use
  only the hold" wins exactly when the hold is the better shot on its own.

**Decision:** C. Measured on the 33 landing pairs: the phrase wins when the move
is the better part (0080 s1 0.911, 0072 s7 tunnel 0.895, 0081 s7 0.862) and the
hold alone wins when the hold is (0060 s5 Doru's pan case, 0080 s6 hero static).
On an exact tie the phrase wins. Phrase length: the end cut always lands inside
the hold (`out_s >= H.start_s + 0.75`), the head is trimmed to the pacing target
but never before the move's handle-in — so a phrase is never longer than its
target ("shorter shots are better") and the move always completes.

**Same-take rule:** adjacent slots from one take must be source-contiguous (±0.5 s)
or the candidate is skipped; contiguous neighbours merge into one slot. In a
chronological edit same-take windows are always neighbours in content time, so a
take contributes one slot; that slot is its **best** window — a blocked candidate
with a higher score replaces the conflicting slot (`same-take upgrade` warning).
Without this the tunnel hero (0.895) lost its take to a weaker dolly_in (0.729).

**Geometry `accidental`/`setup`/`teardown` are never slots**, whatever the
shotlog says. `0051` (ground truth: "Unusable clip, makes no sense") has a
quality segment labelled `accidental` whose vision verdict said `move`, so the
shotlog marked it eligible and it became the opener of the edit. The tools label;
the director decides (shot-grammar §1). The shotlog keeps the verdict and the
slot's `role`/`motion_label` follow it — only candidacy is decided here.

**Rationale:** A is what the first implementation did and it puts junk on screen;
B is what the second round did and it lost the best shot of the film. C is the
reading of the rule that keeps both Doru's constraints: no jump cut, and the
motion worth showing is shown through its landing.

---

## [2026-10-13] D2 routing, the LLM director loop and the final render

**Context:** D2 proper after the five D1b feedback tasks. Three design choices
had to be made and each was measured on the 21-clip Kotor workspace.

**Router.** `scripts/router.py` is deterministic — keyword rules on the brief,
then workspace signals (a shotlog whose shots mostly travel is a vacation),
then the vacation default. An LLM would add cost and non-determinism to a step
that only sorts the brief's own words. `schemas/route.schema.json` is strict
(like `edit.schema.json`): a route is a decision, not an analysis.

**LLM director.** One tool, `scripts/llm_director.py`, with the three roles from
VISION §3.4 and a **code validator** between the editor and the reviewer: the
Llm chooses which shots and roughly how long; the validator enforces the phrase,
landing, same-take, similar and keep-whole rules. Options considered: (A) let
the LLM write timecodes and trust it, (B) let the LLM choose shots and compute
the windows in code, (C) LLM writes timecodes, code validates and normalises the
length rules. **C.** A failed A puts jump cuts on screen; B throws away the
editor role VISION asks for. On the real workspace C produced a 92.5 s edit
with zero grammar violations; the rule-based `director.py` stays the fallback
and the `--dry-run` baseline (92.6 s on the same brief).

**The screenwriter's menu is the cuttable pool.** The first loop crashed because
the plan named shots with no usable window (0051 among them). The screenwriter
now only sees the shots `build_shot_facts` can cut — the same candidacy rule the
rule-based director uses (geometry `accidental`/`setup`/`teardown` never become
slots). Measured: 40 cuttable windows out of 88 eligible shotlog rows.

**Final render.** `render.py --originals-dir` resolves clips from the originals
first (read-only) and, with `--width`/`--height` omitted, renders at their
native size (1920x1080 here). The 720p preview from `sources[]` (the proxies)
stays the default. `--out` inside the originals folder is refused.

**Kdenlive export.** `scripts/edit_to_fragments.py` maps `edit.json` to the
`fragments.yaml` `kdenlive_from_fragments.py` already consumes, with
frame-inclusive `out` timecodes (`out_s - 1/fps`) so the fragment lengths match
the edit. The edit list stays `edit.json`.

**Known conflict reported to Doru:** the reviewer wants the closing orbit and
the chapel walk shorter than the `keep_whole` rule allows (8 s vs 5–5.5 s). The
rule wins — it is the D1b feedback ("2–2.5 s is too short for a stable,
important move") — but the reviewer's taste point is in the D2 report.

---

## [2026-10-14] D2 reviewed by Doru; small D2-2 round before the publish gate

**Context:** Doru watched `recap_d2_720p.mp4`: "much better, everything else is very good". Two faults: (1) the approach into the submarine tunnel (0072, ~00:20–00:50) is missing, only the exit is used; (2) a closing shot is too long — the camera keeps moving without purpose. D2 report question 5 (vision rescuing a geometry `accidental`) was decided by Claude as architect.

**Cause of (1):** `motion` labels 0072 00:28–02:42 as one 134 s `travel_forward` move. One segment gives one window (≤ 8 s), so approach, interior and exit compete inside the same segment and only one survives.

**Decision (D2-2, before the publish gate):**
- Long segments (> 20 s) are split into content beats: `describe` samples one frame every ~5 s over the segment and marks where the scene changes (approach / inside / exit, enters / leaves); each beat becomes its own shotlog candidate. Validate on 0072: approach and exit both eligible, director may use both with another take between.
- Important-shot cap default 6 s (was 8 s); a window ends where the steady motion ends, never on aimless movement after it. The reviewer gets the same rules so it can approve.
- Geometry `accidental` is final: `describe` may describe it but its verdict never changes the role.
- Wire `audio_clean` into `render.py` (ambient bus) and fetch an RNNoise `.rnnn` model (licence check) into the model cache.
- Beat-aligned cuts in the director stay for the first brief with a music file.

**Rationale:** Both faults come from granularity, not taste: a long take holds several moments. Fine trims are D3's job (Doru trims in the web editor), so the defaults only need to be sensible.

---

## [2026-10-14] D2-2 implementation decisions: beats format, window rule, RNNoise model

**Context:** Building the four D2-2 tasks on `d2-2-fixes`. Three points needed a
choice; all are reversible and were made by the architect.

**Content beats format.** `describe` keeps one record per quality segment and
adds an optional `beats` array: a contiguous tiling of a > 20 s segment with
`id` (`b1`..), `start_s`/`end_s`, a content-phase `label` (approach, inside,
exit, enter, leave, ...), and per-beat `summary`/`interest`/`hero` (null =
inherit the segment's). The segment's own call is the only model call — long
segments are sampled one frame every ~5 s (capped at 24) and the same answer
carries the beats. `index` emits one shotlog candidate per beat with key
`<counter>/<segment id><beat id>` (`0072/s7b1`), `beat_of`/`beat_id`/`beat_label`
and the parent's role/motion/defects. The directors build one window per beat,
the beat range intersected with the best overlapping motion move. Beats of one
segment are handled by the same-take rule (never adjacent unless contiguous),
not by similarity — approach and exit of one take may both be used when another
take sits between them. Rejected: one model call per beat (more spend for the
same answer) and expanding beats into fake quality segments (geometry stays
geometry).

**Window end rule.** "A window ends where the steady motion ends" is applied at
window construction: a move candidate's `out_s` is `steady_end_s` (never
`move.end_s`, never the +0.25 s trailing handle; the in handle stays). A phrase
still ends inside its landing hold — the hold is the landing, not aimless
movement. The same-take adjacency check now runs on the finished slot's window
after trimming (it used to run on the candidate window, so a trim could create a
jump cut). The `--important-max-s` default is 6.0 in both directors and the
editor/screenwriter/reviewer prompts state the same numbers.

**RNNoise `.rnnn` model.** Compared: (a) `GregorR/rnnoise-models` (rnnoise-nu) —
the exact format ffmpeg's `arnndn` reads (`rnnoise-nu model file version 1`),
models explicitly not subject to copyright per the repo README ("none of this
work is creative"), dormant since 2018 but the format is frozen; (b)
`dbklim/RNNoise_Wrapper` (Apache-2.0, active 2024) — trained models behind its
own Python wrapper, not the rnnoise-nu files `arnndn` loads; (c)
`werman/noise-suppression-rnnoise` — gone (404), so unmaintained. Chosen:
**`conjoined-burgers` vs `marathon-prescription` from (a)** — the table maps
signal (columns) to noise (rows); the ambient bus is general sound on recorded
footage, so `marathon-prescription` (General × General) is the right default and
`conjoined-burgers` (Recording × General) is the alternative for recordings.
Fetched to `~/.cache/yt-pipeline/models/arnndn.rnnn` (sha256
`4e84a448a4baf937992aaf4d10c8258007ec5d24219b6647dfd5fb4b563ad231`), never in
the repo. The cleanup chain moved to `scripts/common/ambience.py` so
`audio_clean.py` and `render.py` share one implementation (no tool imports
another tool).

**Interpretation of "director may use both with another take between":** the
same-take rule is unchanged (no two non-contiguous same-take slots back to back;
a better same-take slot replaces a weaker adjacent one). In a strictly
chronological edit two beats of one clip cannot get another take between them
(no other clip was filmed during the take), so the better beat wins there; in
score order or an LLM plan another take can sit between and both are allowed.
The validation on 0072 is: approach and exit both eligible in the shotlog, and a
director run that takes both with another take between (covered by tests).

---

## [2026-10-14] Feedback storage and the rules digest

**Context:** Doru asked how the system learns, where it is written, and whether those files grow too large. `DECISIONS.md` already passed 1 000 lines and every agent task re-reads it.

**Decision:**
- **Feedback (D3):** the web editor appends one JSON line per action to `<workspace>/feedback.jsonl` — `{ts, kind: label_fix|approve|reject|trim|regenerate, clip, start_s, end_s, before, after}`. No footage, frames or thumbnails. A compaction step folds all workspaces into a per-user profile (`~/.local/share/yt-pipeline/profile.sqlite`): label fixes become ground-truth items (dedup by clip + range, newest wins); approvals/rejections become counts per tag, motion label and length bucket for the scorer. Per-project files are kept; the profile is rebuilt from them, so it can always be regenerated. Expected size: ~200 bytes per action.
- **Rules digest (D3-00):** the architect writes `docs/RULES.md`, the rules in force in one short file (editing grammar, defaults, thresholds, model routing). Agents read `RULES.md` plus the last 10 DECISIONS entries, not the whole log. Every new decision that changes a rule updates `RULES.md` in the same commit.
- Risk register: `docs/RISKS.md`. User guide: `docs/guides/user-guide.md`.

**Rationale:** Small append-only files are easy to inspect, back up and rebuild; a current-state digest keeps agent context and cost flat as the history grows (same principle as Doru's vault: reference files hold the current state, history lives in git).

---

## [2026-10-14] Public-repo guard: pre-commit check, CI check, GitHub push protection

**Context:** The repo is public and agents commit unattended. Nothing automatic stopped footage, frames, transcripts, model weights or API keys from being committed; only a rule in CLAUDE.md. An audit of the full history on 2026-10-14 found no media, no keys and no other emails than the author's.

**Options:** A) gitleaks (MIT, secrets only, extra binary on the LXC); B) detect-secrets (Apache, secrets only, baseline file to maintain); C) small `scripts/check_public.py` (stdlib) covering secrets **and** media/subtitles/model weights/large files/base64 images, run as a git hook and in CI, plus GitHub secret scanning with push protection on the server side.

**Decision:** C. Hook in `.githooks/pre-commit` (`git config core.hooksPath .githooks` once per clone), CI workflow `public-check`, `.gitignore` extended (images, subtitles, html, kdenlive, models, feedback, workspaces). `tests/fixtures/` and `examples/` (synthetic data only) are allowed. Push protection must be switched on by Doru in the GitHub settings (the API is not available from the agents).

**Rationale:** Our main risk is personal media and transcripts, which secret scanners do not look for. The hook stops it before it exists in history; CI catches a skipped hook; push protection blocks known key formats even from other machines.

---

## [2026-10-14] Rules digest and golden edit-grammar tests pulled forward from D3

**Context:** `docs/RISKS.md` rows 1 and 3 assigned the rules digest to D3-00 and golden edit tests to D3. Both were pulled forward onto the `rules-digest` branch while the D2-2 round runs on its own branch: `DECISIONS.md` passed 1 000 lines and every agent task re-reads it, and the D2-01 regression (0051 came back as an opener) showed how easily one fix breaks an earlier one. Budget: no model calls, fixtures only, no footage.

**Options for the golden tests:** A) frozen `edit.json` diffs only — brittle against harmless re-scoring and blind to grammar; B) an invariant checker only — silent on reordering and window drift; C) both, plus tests proving the checker itself fires on each violation.

**Decision:**
- `docs/RULES.md` (68 lines): current-state digest of every rule in force from `DECISIONS.md`, `VISION.md` and `CLAUDE.md` — editing grammar, defaults and thresholds, model routing, safety rules — one dated line per rule. `CLAUDE.md` and `.opencode/agents/*.md` now say: read `RULES.md` plus the last 10 `DECISIONS.md` entries; every decision that changes a rule updates `RULES.md` in the same commit; `DECISIONS.md` stays the history.
- C for the goldens: `tests/test_edit_golden.py` with `tests/fixtures/golden/` — seven frozen synthetic workspaces (skip_roles, phrase_move_strong, phrase_hold_strong, same_take, similar_excluded, similar_not_adjacent, hero_present) with frozen expected cut windows from the deterministic `scripts/director.py`. Every produced edit is judged by a `grammar_violations` checker: never accidental/setup/teardown slots (geometry final), never a move cut before its landing and never move-then-own-landing, never adjacent non-contiguous same-take slots, never adjacent similar pairs, never a missed eligible hero. Checker self-tests prove each rule fires.
- CI: `.github/workflows/tests.yml` runs the full pytest suite (golden next to the label eval) with the light dependencies (no torch/whisperx), alongside `public-check`.
- `docs/RISKS.md` rows 1 and 3 updated; per-shoot golden edit sets on real test sets stay at D3.

**Rationale:** The digest keeps agent context and cost flat as the history grows (same principle as Doru's vault: reference files hold the current state). Fixture-only goldens lock the editing grammar with zero footage and zero model cost and run on every push, which is exactly the regression class the S1/D1b/D2 reviews found: junk takes as openers, jump cuts out of moves, near-duplicates and missing hero shots.

---

## [2026-10-14] D2-3 "agent-ready": a fresh agent can go from folder + brief to MP4

**Context:** Doru wants to demo, in the publish-gate clip, a fresh agent (Claude Code, OpenCode or Hermes) with no project knowledge turning a footage folder and one sentence into a recap. Gaps today: no tool makes the 720p proxies (they were made by hand), no single entry point chains the stations, no setup check, and the quickstart is spread across docs.

**Decision (small round before the clip, no new editing features):**
- `scripts/proxy.py`: originals → 720p H.264 constant-frame-rate proxies with audio, skip existing, never touch originals.
- `scripts/recap.py`: one command `--source DIR --brief "..." [--workspace DIR] [--final]` that runs the existing stations as subprocesses in order (proxy, ingest, motion, quality, describe, similarity, index, router, llm_director, render, optional final render and Kdenlive export), skips stations whose outputs exist, prints one progress line per station and a `--json` summary with wall time and total LLM cost. It imports no other tool.
- `scripts/doctor.py`: checks python deps, ffmpeg/ffprobe, `OPENROUTER_API_KEY`, config models, disk space, write access to the workspace and that the originals folder is not writable by mistake; prints what to fix.
- Skill `yt-recap` (the front door for any agent) and a 10-line quickstart at the top of `README.md` and `docs/guides/user-guide.md`.
- Acceptance: on a clean clone in a fresh shell, `doctor.py` then `recap.py --source ~/media/yt-test/raw --brief "<S1 brief>"` produces the MP4 with no other command.

---

## [2026-10-14] D2-3 implementation decisions: proxy naming, recap skip rule, doctor safety checks

**Context:** Building the four D2-3 items on `d2-3-agent-ready`. Five points needed
a choice; all are reversible and were made by the architect (one proxy bug was
found in audit and fixed before merge).

**Proxy naming and the single-file case.** The proxy keeps the exact source
filename so `ingest`/`render` match originals by name (`clip.MOV` → `clip.MOV`).
An audit repro showed the first version overwrote an original when the source
was a single file and `--out` was that file's folder with `--force`. Now: for a
folder source `--out` must not be inside the folder; for a file source `--out`
must not be the file's own folder (the name-collision case the default
`<parent>/proxy` avoids), and no proxy is ever written onto a source file path
(asserted per clip). Height 720 with `scale=-2:H`, `-fps_mode cfr` locked to the
source `r_frame_rate`, `libx264 -preset veryfast -crf 23` (nvenc `p4/cq23` when a
GPU is present, runtime retry to libx264), audio `aac 128k 48k stereo` optional.
The rebuilt set for the 21 Kotor clips is 402 MB, byte-for-byte the same format
as the hand-made proxies.

**Recap skip rule (what "outputs exist" means).** Plain existence is not enough
(a new clip or a new brief would silently produce a stale edit), so each station
carries an up-to-date rule: per-clip output mtime vs its input for
proxy/ingest/motion/quality/describe/similarity/index, and for the brief-driven
side (router, llm_director, render, render_final, export_kdenlive) the recorded
`brief.text` in `route.json`/`edit.json` must equal the current brief and the
output must be newer than `shotlog.json`/`edit.json`. A **new brief** therefore
redoes only router → llm_director → render; a second run with the same brief
skips every station in 0.01 s and costs nothing. `recap.py` writes exactly one
file itself, `workspace/brief.txt`, and only when the content changed (the
stations use its mtime for their own checks). `--force` re-runs everything.
Progress lines go to stdout normally and to stderr under `--json`, so the JSON
summary is always exactly one object. Per-station `llm_cost_usd` is taken from
the child's own `--json` summary; the run total is their sum, and
`llm_spend_usd` is the workspace ledger (`llm_spend.json`) total.

**Kdenlive export is one station, two commands.** `edit_to_fragments.py` then
`kdenlive_from_fragments.py`, with `--skip-melt` only when `melt` is not on the
PATH, so machines without MLT still get the project file.

**Doctor's originals check.** "Not writable by mistake" is a `warn` on the
source folder's write bits (`st_mode & 0o222`, never `os.access` — the tests run
as root) plus a hard `fail` when the workspace would be inside the source. The
warning does not fail the run (exit 0) because the tools already refuse to write
there; it tells the user how to make the folder read-only. The API-key check
reports only `set (hidden)` — the key never appears in any output.

**Acceptance (2026-10-14, `~/media/yt-test/ws-d23`, S1 brief, budget 1 USD):**
`doctor.py` then `recap.py --source ~/media/yt-test/raw --workspace
~/media/yt-test/ws-d23 --brief "<S1 brief>" --budget-usd 1` produced
`recap_720p.mp4` (84.3 s, 1280x720) with no other command — 10 stations,
1967 s wall, **$0.1651** LLM (describe $0.116 + llm_director $0.0495). Re-run:
all ten stations `skip` in 0.008 s at $0. `--final` added `recap_final.mp4`
(1920x1080 from the originals) and `recap.kdenlive` with no model calls. All 21
originals verified unchanged by md5 before and after; every one of the 15 slots
in the recap was frame-matched against its source window (mean abs diff ≈ 1–2,
i.e. re-encode noise).

---

## [2026-10-14] Demo scope before the clip: phone flow (D2-4) and cross-platform (D2-5)

**Context:** Doru wants the publish-gate clip to show the tool used three ways: a fresh agent in a terminal (D2-3), phone only, and a normal PC (Windows, macOS, Linux). These are delivery work on top of D2, not new editing features.

**Decision:**
- **D2-4 phone flow, repo side.** `scripts/inbox.py`: watches an inbox folder (filled by a sync tool from the phone), groups new clips into a project per day, waits until the folder is quiet for N minutes, then runs `recap.py` with a default or per-folder brief (`brief.txt`). `scripts/serve.py`: read-only HTTP server for workspaces, bound to the tailnet address only, with an index page per project (recap, final, shotlog.html). `recap.py --notify` posts the preview link to ntfy. The `yt-recap` skill gains the phrases an assistant like Hermes receives from Telegram ("fă-mi un recap din clipurile de azi"). Installing the sync tool and wiring Hermes are system changes on Doru's machines: the repo ships instructions in `docs/guides/phone-flow.md`, Doru runs them.
- **D2-5 cross-platform.** Replace the Unix-only pieces: `fcntl` ledger lock → a portable lock (choose by comparing at least two options), `pgrep` → `psutil` or a portable fallback, shell hook → Python hook. Paths through `pathlib` everywhere. CI matrix on ubuntu, macos and windows. Hardware encoders by platform: `h264_nvenc` (NVIDIA), `h264_videotoolbox` (macOS), else `libx264`. Install notes per OS in the quickstart.
- Order: D2-5 can run in parallel with D2-3 (separate worktree, disjoint files); D2-4 starts after D2-3 is merged, because it builds on `recap.py`.

---

## [2026-10-14] D2-5 portable ledger lock: `filelock` instead of `fcntl`

**Context:** `scripts/common/llm.py` took an exclusive `fcntl.flock` on the
spend ledger's sidecar lock file. `fcntl` does not exist on Windows, so the
budget cap breaks on the "normal PC" the D2-5 demo target needs. The lock must
be exclusive across processes, release on crash and work on Linux, macOS and
Windows.

**Options compared (current PyPI, 2026-10-14):**
- `filelock` 4.1.1 (MIT, tox-dev, released 2026-10-11, Python >= 3.11) — the
  lock `pip`, `tox` and `virtualenv` themselves use; `FileLock` picks
  `flock` on Unix and `msvcrt.locking` on Windows, is a context manager,
  blocking by default, released when the process dies, one small pure-Python
  package.
- `portalocker` 4.4.0 (BSD-3-Clause, released 2026-09-19, Python >= 3.10) —
  also a portable `flock`/`msvcrt` wrapper, but an older API surface
  (`Lock`, `LOCK_EX` flags, exceptions per platform) and less used in the
  Python tooling ecosystem.
- `fasteners` 0.20 (Apache-2.0, last release 2025-08) — process lock is a
  side feature next to its inter-thread primitives; maintenance is slower.

**Decision:** **`filelock`** (`FileLock` on the `<ledger>.lock` sidecar,
blocking). `ledger_lock()` keeps its context-manager signature and raises
`LLMError` on `OSError`; `write_ledger()` now does the atomic swap with
`Path.replace` (also Windows-portable). Added to `requirements.txt`.

**Rationale:** `filelock` is the most maintained of the three and the API is
exactly the one we already had (a blocking exclusive lock in a `with` block),
so the diff stays inside `ledger_lock`. Its Unix backend still uses `flock`,
so crash-release semantics are unchanged on the build machine; its Windows
backend is the library's problem, not ours. Tests cover the sidecar name, the
exclusive window and release on body exception (`tests/test_llm.py`).

---

## [2026-10-14] D2-4 phone flow: inbox grouping, tailnet serve, notify link shape

**Context:** Building the four D2-4 items on `d2-4-phone-flow` (serve.py,
recap.py --notify, inbox.py, phone-flow docs). Six points needed a choice; all
are reversible and were made by the architect (one inbox behaviour was sent
back for a fix round and is folded in below).

**serve.py: allowlist routes, wildcard refused, Range required.** The server
binds a single address (auto-detected default-route IPv4, `--bind` to override)
and refuses `0.0.0.0` / `::` / empty — "tailnet-only" is enforced in code, not
just documented. Routing is an allowlist: `/`, `/<project>/`, and only
`recap_720p.mp4`, `recap_final.mp4`, `shotlog.html` inside a project. Proxies,
`edit.json`, `source/` symlink farms and every analysis folder stay private.
A folder counts as a project when it holds at least one of those three plus
`edit.json`; projects list newest-folder-name first. Single-range HTTP (`Range:
bytes=…`) returns 206 because mobile browsers refuse to play video without it.
`--check` binds the requested port, reads the real bound port and closes — so a
port conflict is caught before a service starts. URL contract with `--notify`:
`<base>/<workspace folder name>/recap_720p.mp4` (name URL-quoted).

**recap.py --notify: notify on success only, never fail on notify.** Message
title `Recap ready (720p)`, `Recap ready (cached)` when every station skipped;
a failed run posts nothing (stderr + exit code already report it). A notify
send error is a `[warn]` and `"notified": false`, exit code stays 0 — the recap
itself succeeded and the phone message is not worth failing a render for. Two
keys added to the existing `--json` summary (`preview_url`, `notified`); no
existing key changed.

**inbox.py: day grouping and the source folder.** A clip's project day is, in
priority: a `YYYY-MM-DD` parent folder inside the inbox (wins — the acceptance
layout `inbox/2026-10-14/clip...20261012...` groups under `2026-10-14`), then a
date in the filename (`YYYY-MM-DD`/`YYYYMMDD`/`YYYY_MM_DD`), then the file's
mtime date. One workspace per day under `--projects/<day>/`; `--projects`
inside `--inbox` is refused (its proxies would be rescanned as new clips).
Source: when the day's clips all live in one folder that holds no other video,
that folder is passed to `recap.py --source` directly; otherwise a symlink farm
`<workspace>/source/` is rebuilt per run (no copies, no moves — the inbox is
read-only). The brief is the day folder's `brief.txt` (the clips' folder, or
their single `YYYY-MM-DD` parent when scattered — the fix round extended this
to farm groups), else the inbox `brief.txt`, else `--default-brief`; the
resolved brief is copied to `<workspace>/inbox_brief.txt`. Quiet wait: a group
runs only when no file under the inbox changed for `--quiet-minutes` (default
5, `0` disables). State (`<projects>/inbox_state.json`, internal) records
per-file size+mtime and the brief sha per day: a new clip, a changed brief or
`--force` re-invokes `recap.py`, whose own skip rule then decides the stations;
clips are marked processed only after a successful run. Watch mode prints a
group line only on a run or a status change (the fix round stopped per-poll
log spam); `--once` prints one line per group always.

**Sync tool: Syncthing** (this closes the "sync tool chosen by its own entry"
placeholder from the D3 plan). Phone = send-only camera folder, LXC =
receive-only into `~/media/inbox`: nothing the pipeline does can delete clips
from the phone. `docs/guides/phone-flow.md` carries the steps; installing it
and wiring Hermes remain Doru's machine steps — the repo ships instructions
only.

**Acceptance (2026-10-14, budget 1 USD):** 5 clips copied (never moved) from
`~/media/yt-test/raw` into `~/media/inbox-test/2026-10-14/` with a folder
`brief.txt`; `inbox.py --once --quiet-minutes 0 --notify --preview-base
http://10.10.1.61:8091` picked them up as project `2026-10-14` (brief_source
`folder`) and rendered `recap_720p.mp4` (1280x720, 19.5 s, 4 slots) for
**$0.043** in 540 s. `serve.py --bind 10.10.1.61 --port 8091` lists the
project; index, project page, preview and shotlog all return 200 over the
tailnet address, and the ntfy message (`Recap ready (720p)` + preview link)
arrived with that exact URL. Second `--once` run: `skipped (nothing new)` at
$0. All 21 originals verified unchanged by md5.

---

## [2026-10-14] Demo scope extended: photos + music (D2-6) and the web app with explanations (D2-7)

**Context:** Doru reviewed the D2-4 phone flow (link opens, quality fine for the demo) and wants the demo clip to beat DJI Mimo clearly: a day with more photos than clips, a soundtrack, and a view of *why* the edit looks the way it does. Spending stays paused after these phases until the Second Brain clip and the Facturi Agent demo are done.

**D2-6 (running):** photos as candidates (EXIF time, describe, burst dedup, Ken Burns, portrait on a blurred background), `--music`, cuts on beats. Mix rule decided by Doru: the ambient sound stays under the music, quieter, never removed — default ambient 12 dB below the music, adjustable.

**D2-7 (after D2-6 merges), one agent, two parts in order:**
1. *Explanations.* The director records a reason for every candidate it does not use (near-duplicate of X, defect, setup/teardown, low score, did not fit the length). `explain.html` per project: brief, screenwriter plan in words, the edit timeline (thumbnail, source in/out seconds versus the segment range, reason), every candidate with used / rejected and the reason, short IDs (`0071/s2`). A re-edit command takes feedback in plain words against those IDs ("drop 0071/s2, tunnel longer") and re-runs only the edit + render, so Doru can talk to the agent (Hermes or OpenCode) about its own analysis.
2. *One web app with a menu,* served by `serve.py`, tailnet-only: Projects (list with status: done / running), Upload (clips + photos from the phone browser, project folder name, brief, options, Start → files land in the inbox, the recap starts), and per project the tabs Recap / Why? / Shots. Options menu, each defaulting to **Auto** (the agent decides from the brief and the footage): length (30 s / 60 s / 90 s / 3 min), order (chronological / best first), audio clean-up (yes / no), music (none / pick a track / auto from `~/media/music`), ambient level under the music. The same options appear on "re-edit" in the project page.

**Pacing rule (D2-7, director):** shot length scales with the target length so a short recap carries more, shorter shots: ~20 s → 1.5–2.5 s per shot (≈8–10 shots), ~90 s → 2–5 s (≈20–25), 3 min → up to 6 s with whole moves. Hero and semantic moves stay the longest shots but shrink in proportion. Today's 20 s inbox recap had only 4 shots, which Doru finds too few.

**Later:** `--music auto` picks a track from `~/media/music` by brief and shot tags (needs the library analysed with `beats.py`); generated music (YuE2) waits for the 3090.

---

## [2026-10-14] D2-5 encoder per platform: `h264_videotoolbox` with `-b:v`, not `-q:v`

**Context:** `scripts/common/ffmpeg_tools.py` knew only `h264_nvenc` and `libx264`; macOS, a D2-5 demo target, has no nvenc and would always fall to the CPU. Selection must prefer the platform's hardware encoder and fall back to `libx264` when that encoder is missing from the local ffmpeg build.

**Options for the videotoolbox flags:** (a) constant quality `-q:v` — ffmpeg maps `global_quality` to `kVTCompressionPropertyKey_Quality`, but the encoder only accepts it when `vtenc_qscale_enabled()` holds, i.e. Apple Silicon (`!TARGET_OS_IPHONE && TARGET_CPU_ARM64` in `libavcodec/videotoolboxenc.c`, FFmpeg n6.1.1); on Intel Macs ffmpeg fails with "qscale not available for encoder. Use -b:v bitrate instead"; (b) a plain bitrate `-b:v` — accepted on every macOS. Chosen: (b), basic flags `["-c:v", "h264_videotoolbox", "-b:v", "8M"]` (a sane rate for 720p proxies and 608x1080 shorts).

**Selection.** `preferred_video_encoder()`: `h264_nvenc` when `nvidia-smi` reports a GPU, `h264_videotoolbox` on `darwin`, else `libx264`. `pick_video_encoder(available, prefer)` tries the preferred encoder first, then the rest of `H264_ENCODERS` (`libx264` last) and raises when the build has none of the three — the old last resort of "any video encoder" (which could be `amv` or `png`) is gone. The runtime retry to `libx264` in `proxy.py` and `shorts_generator.py` is unchanged. `proxy.py` now builds its codec args through `video_encode_args(encoder, crf=crf, preset="veryfast")` instead of its own copy, so the three encoders live in one place (`video_encode_args` gained `crf`/`preset`, defaults unchanged for `shorts_generator.py`). Smoke test 2026-10-14: a synthetic 3 s clip through `proxy.py` auto-picked `libx264` on the GPU-less LXC and wrote a 1280x720@25 H.264 proxy; `tests/test_ffmpeg_tools.py` covers the preference order, the missing-encoder fallback to `libx264`, the auto-detection per platform (mocked `sys.platform` / GPU probe) and the args per encoder.

**Rationale:** `-b:v` is the only rate control that works on every macOS target, and one fixed bitrate is what "basic flags" means for now — quality tuning per resolution is not needed before the D2-5 demo. Failing loudly on an ffmpeg build without any of the three H.264 encoders beats silently picking an image encoder.

---

## [2026-10-14] D2-5 CI matrix: markers plus `-m`, melt on ubuntu only

**Context:** The suite must run on ubuntu, macos and windows (the D2-5 demo target is a normal PC). Two test classes cannot run everywhere: anything needing `melt` (Kdenlive's MLT CLI) and anything reading real footage.

**Options:** (a) OS-based `skipif` inside the tests — hides the reason from the run summary and can silently skip on a dev machine that does have melt; (b) pytest markers `needs_melt` / `needs_footage` plus a `-m "not needs_melt and not needs_footage"` expression on the macOS/Windows CI jobs, keeping the existing `skipif(which("melt") is None)` as a second belt for local runs without melt. Chosen: (b). Ubuntu CI installs `melt` (apt package `melt`) so the melt end-to-end test runs there; macOS/Windows install only ffmpeg (`brew` / `choco`) and fail loudly on `ffmpeg -version` if the install did not stick. A grep of the suite found no test that reads real footage (fixtures and tmp paths only — the `/media/clip*.mp4` strings in the director tests are JSON metadata, never opened), so `needs_footage` is registered and used in the expression but nothing carries it yet. The workflow's pip line gained `filelock` and `psutil` (ledger lock and watchdog) and still avoids `requirements.txt`, so CI stays free of torch/whisperx. Numbers 2026-10-14: `742 passed` full, `741 passed, 1 deselected` with the expression.

**Rationale:** Markers keep the skip decision in the runner, where the OS is known, and the summary honest ("1 deselected" beats a vague skip reason); `fail-fast: false` so one OS failure still shows the other two.

---

## [2026-10-14] D2-6 photos + music: EXIF stack, photo data model, Ken Burns, beat snap

**Context:** D2-6 goal is to beat DJI Mimo on a day with more photos than clips:
photos become first-class shotlog candidates and edit slots (2–4 s, Ken Burns),
the render mixes a music track under the ambient bus with beat-aligned cuts.
Four interface choices were needed; all are reversible and were made by the
architect.

**Photo EXIF / HEIC stack.** Options: (a) **exiftool** (Perl, `libimage-exiftool-perl`
12.76) — richest tag coverage but a new system dependency and a subprocess per
file; it does not decode pixels, so HEIC decode would still need another library.
(b) **Pillow alone** (MIT-CMU, 12.3.0) — JPEG EXIF fine, but no HEIC at all.
(c) **Pillow + pillow-heif** (BSD-3-Clause, 1.8.1) — one register call gives
JPEG+HEIC decode *and* EXIF (`DateTimeOriginal` tag 36867, orientation 274)
through the same `Image.getexif()` API, and pixels come out as numpy for
describe/similarity/render. **Chosen: (c).** exiftool stays the manual fallback
for exotic tags; it is not installed and nothing here needs it. Both go to
`requirements.txt`; HEIC decode lives behind `scripts/common/photos.py` (no
tool imports another tool). Verified on 2026-10-14: JPEG and HEIC EXIF
round-trips on the LXC venv.

**Photo data model (ingest → describe → shotlog → edit).**
- `ingest.json` keeps `clips[]` for video and adds `photos[]` with the same
  timestamp keys (`creation_time`, `creation_time_source: "exif"`) plus
  `width`/`height`/`orientation`, so one chronological order covers both.
  Videos never change shape (motion/quality/describe stay untouched).
- `describe/<stem>.json` for a photo is one pseudo-segment `p1` covering
  `0..0.04 s` (one frame at 25 fps): same answer fields as a video segment,
  plus `subject_position` (`x`/`y` in 0..1, main subject centre) which drives
  the Ken Burns move. `media_type: "photo"` marks the record.
- Shotlog photo shots: key `<counter>/p1`, `media_type: "photo"`, role `hold`,
  motion `static`, the same window convention. **Photo labels score is a fixed
  clean-still prior of 0.7** (a still is a perfect hold; defect measurement for
  photos is future work) fused with vision as usual, so photos rank by content
  without being capped by missing geometry. Burst rule: photo shots linked by
  similarity *and* captured within 30 s of each other form a burst; only the
  best of the burst stays eligible (`suppressed_by` the winner).
- `edit.json` gains a **photo slot** (`kind: "photo"`): `photo` file name,
  `duration_s` 2–4, `ken_burns` `{move: zoom_in|zoom_out|pan_left|pan_right,
  amount, focus_x, focus_y}` and `capture_time`. Move choice is deterministic
  from `subject_position`: centre box (|x−0.5|≤0.15, |y−0.5|≤0.2) → `zoom_in`
  on the subject; otherwise pan toward the subject (`pan_left` if x<0.5 else
  `pan_right`); `zoom_out` only when the subject fills the frame (vision says
  close-up). `video[]` items become `oneOf` video_slot | photo_slot; video
  slots keep their exact shape.

**Ken Burns render.** ffmpeg `zoompan` on a still, framed to the target aspect
by `scale`+`pad`; **portrait photos land on a blurred background** (cover-scaled
copy + `boxblur`, photo overlaid fit-to-height) — never black bars. The move is
a slow zoom/pan over the slot (amount default 0.08).

**Music and beat snap.** `render.py --music FILE` and `recap.py --music FILE`
(also `edit.json` `audio.music` as today). With music, `beats.py` writes
`beats.json` (already has the grid) and **the directors snap every timeline cut
point to the nearest beat** (max shift half a beat period — the nearest beat is always within reach, so only a slot that cannot absorb the shift warns); in
`llm_director` a cut that starts a new `shot_plan` structure beat snaps to the
nearest **downbeat** instead; `director.py` (rule-based) has no sections and
snaps to beats only. With a beat grid the slots join with **hard cuts** (music
edits cut on beats; crossfades smear the beat), the 0.3 s crossfade default
stays for ambient-only edits. The render mixes the track **under the cleaned
ambient bus** with `sidechaincompress` ducking and a fade-out at the end;
music shorter than the edit loops. No music generation in this phase (YuE2 is
D6).

**Fixtures.** Test photos are generated at test runtime with Pillow (tiny
synthetic, EXIF written by the test) — never committed. The acceptance run
builds its photos from frames of `~/media/yt-test/proxy` clips with EXIF times
into the test workspace (outside the repo) and uses a synthetic 120 BPM click
track made with ffmpeg. `tests/fixtures/` stays free of binaries.

**Rationale:** one decode path (Pillow) for EXIF and pixels keeps the tools
independent and the LXC happy; the pseudo-segment convention reuses the whole
shotlog/index/director machinery for photos instead of a parallel code path;
hard cuts on beats are what Quik/Mimo do and make the 80 ms acceptance check
unambiguous.

---

## [2026-10-15] Coder switched to GLM 5.3 Flash (fallback) after repeated empty replies

**Context:** during D2-6 the DeepSeek V4.1 Flash coder returned empty replies twice on three separate tasks (proxy photo pass-through, paired beat snap, one more), so the architect did the work itself and finally stalled. The routing rule allows a coder switch on outage, never mid-task.

**Decision:** D2-7 starts with GLM 5.3 Flash as the coder (`.opencode/agents/coder.md`). DeepSeek stays the fallback. Revisit after D2-7: keep whichever coder needed fewer architect takeovers.

---

## [2026-10-15] D2-7 decisions list, shot keys and music recorded in edit.json

**Context:** D2-7 must show *why* the edit looks the way it does. The directors
picked slots but never said what they refused, so explain.html had nothing to
render; the D2-6 gap (the music track and the ambient level coming from flags
never reach `edit.json`) also has to close here, because the web app passes
options through the edit list.

**Schema (`schemas/edit.schema.json`), all optional so old files keep
validating:**
- `video_slot.shot_key` / `photo_slot.shot_key`: the short candidate id
  (`0071/s2`, `0072/p1`) that feedback and explain.html refer to.
- `decisions[]`: one row per candidate the director considered —
  `{id, status: used|rejected, reason, slot, score, clip, window_in_s,
  window_out_s}`. The reason taxonomy is fixed: `near-duplicate of <id>`,
  `defect: <names>`, `setup/teardown` / `accidental`, `low score`,
  `did not fit the length`; used rows carry the slot's own reason.
- `audio.ambient_under_music_db` next to `audio.music`, so the render's mix
  rule is in the edit list and not only in a CLI flag.

**Recording.** Both directors build the list with
`scripts/common/decisions.py` (shared code lives in `scripts/common/`): the
shotlog is the candidate universe, the used rows come from the final slots
(a phrase's landing hold is marked used with the move it belongs to), the pool
drops carry their own reason (overlapping window of X), and the rest is
derived from the shot (`suppressed_by`, role, defects, score versus the used
floor). `director.py` and `llm_director.py` take `--music` and
`--ambient-under-music-db` and write them into `audio`; `recap.py` forwards
both flags to the directors and to `render.py`, which now falls back to the
edit's `audio.ambient_under_music_db` before its own default (12).

**Rationale:** `edit.json` stays the single source of truth — render,
explain.html and the web app all read the same reasons; a sidecar decisions
file would have drifted. The shot key on the slot is what makes feedback
("drop 0071/s2") and the timeline's "source in/out versus the segment range"
possible without re-deriving ids.

---

## [2026-10-15] D2-7 pacing table: shot length scales with the target length

**Context:** the 20 s inbox recap had 4 shots of ~5 s; Doru wants a short recap
to carry more, shorter shots. The old rule sized the slot count as
`clamp(round(T/6), 4, 24)` and fixed the slot band at 2–12 s, which cannot
reach 8–10 shots in 20 s.

**Rule (implemented as `pacing_table(T)` in both directors, duplicated like the
other small helpers).** Linearly interpolated between the anchors 20 s / 90 s /
180 s, clamped outside: `slot_min` 1.5/2.0/2.5, `slot_target` 2.0/3.6/5.0,
`slot_max` 2.5/5.0/6.0, `hero_max` 3.5/6.0/6.0. `hero_max = min(6,
slot_target × 1.75)` — heroes stay the longest but shrink with the rest. Slot
count `round(T / slot_target)` clamped to 4–32 (T=20 → 10 slots, T=90 → 25,
T=180 → 32). `uniform` gives every slot `slot_target`; `calm-to-energy`
interpolates from `slot_max` down to `slot_min`. Photo slots keep 2–4 s.

**Flags become overrides.** `--min-slot-s`, `--max-slot-s`, `--calm-slot-s`,
`--energy-slot-s`, `--important-max-s` default to `None` = "from the table"; an
explicit value wins. `MIN_SLOT_S` in `llm_director` drops to 1.0 so a 1.5 s
slot survives; `MAX_SLOT_S` 12.0 and `DEFAULT_IMPORTANT_MAX_S` 6.0 stay the
outer bounds. The screenwriter/editor/reviewer prompts no longer hardcode 6 s
or 2–12 s: the effective hero cap and slot band are stated in the user message.

**Golden fixtures regenerated** (`tests/fixtures/golden/*.json`): the frozen
`expected_slots` windows shrink with the new targets; the picks, order and
grammar outcomes are unchanged (`grammar_violations == []` checked per file).
A dense synthetic workspace (1 hero + 24 holds) proves 8–10 slots at 20 s and
20–25 at 90 s.

**Rationale:** one table keeps the rule-based and the LLM director honest with
each other and makes "short recap = more, shorter shots" a testable number
instead of a prompt wish. Interpolation between the three anchors the spec
gives is the smallest thing that covers the web app's 30 s/60 s/90 s/3 min
length options.

---

## [2026-10-15] D2-7 feedback loop: `--feedback` re-runs only the edit

**Context:** Doru must be able to talk to the agent about its own analysis
("drop 0071/s2, tunnel longer"). The short ids exist since the decisions work;
now the director has to accept them back.

**Decision.** `llm_director --feedback TEXT` (repeatable):
- appends one JSON line per flag to `WORKSPACE/feedback.jsonl`
  (`{"ts", "action": "feedback", "text", "tool"}`, append-only, ~200 bytes) —
  this is the first writer of the learning store, which the D2 rules already
  reserved;
- forces the re-run for that invocation only (the up-to-date check is skipped);
- puts a "Feedback to apply / Previous edit / Rejected candidates" block into
  the screenwriter, editor **and reviewer** user messages, built from the
  existing `edit.json` `video[]` + `decisions[]`, so every short id in the
  feedback resolves;
- adds `"feedback": [...]` to the `--json` summary.

`recap.py --feedback` forwards the flags and, when any is present, runs
`llm_director` and `render` even if up to date — the analysis stations keep the
normal skip rule, so a re-edit costs only the edit + the render.
`render_final`/`export_kdenlive` need no special case: their mtime rule already
fires when `edit.json` is rewritten.

**Rationale:** feedback as a flag keeps the loop scriptable from Hermes/OpenCode
without a new RPC; the id block is what makes plain words like "0071/s2"
unambiguous; `feedback.jsonl` is written first and read later (the profile work
is not in this phase).

---

## [2026-10-15] D2-7 `--music auto`: `music_pick.py` scores the library

**Context:** the web app's music menu offers `auto`; the track must come from
`~/media/music` by the brief and the shot tags using `beats.py` energy and BPM.

**Decision.** New `scripts/music_pick.py` (standalone + `yt-music-pick` skill):
every audio file in the library is measured with `beats.py` as a subprocess
(`bpm`, `energy_mean_dbfs`, `duration_s`) and cached in
`<library>/.yt-beats/<file>.json` (skipped when the library is read-only). Four
deterministic signals: BPM against the band the brief's pace words pick
(calm → 70–100, energetic → 110–150, else 85–135), mean energy against the
matching target (−24 / −10 / −16 dBFS), file-name tokens against the brief +
shotlog tag words, and whether the track covers `0.7 × target duration`.
Weights 0.35 / 0.25 / 0.25 / 0.15, ties by name. The winner goes to
`music_choice.json` and its grid to `workspace/beats.json`.

`recap.py --music auto` inserts a `music_pick` station right after `index`
(the tags exist only then) instead of the early `beats` station; the
`llm_director`/`render` argv carry a `@music@` token that `execute()`
substitutes once `music_choice.json` has been read. `--music-dir`,
`--target-duration-s` and `--order` are new recap flags forwarded to the
stations.

**Rationale:** one pure decision tool keeps the recap chain dumb; the cache
makes a second run instant; scoring in code (not a model) keeps `music auto`
deterministic and testable. Verified on the real library: a calm brief picks
"Travelling Light" (67 BPM), an energetic brief "Gasoline" (140 BPM).

---

## [2026-10-15] D2-7 acceptance fixes: beat-snap slack, @music@ substitution, music validation

**Context:** the real-footage acceptance (21 Kotor clips + 4 photos uploaded
through the web endpoint, `--music auto`) found three defects.

1. **Cuts could stay off the beat.** `snap_slots` refused to move a hero or a
   slot whose window was full, so 11 of 14 cuts stayed 20–160 ms from the
   nearest beat — the rule says *every* timeline cut snaps and "the nearest
   beat is always within reach". **Fix:** the snap may push a slot's edge by up
   to half a beat period (`BEAT_SNAP_FRACTION × period`) past its candidate
   window. The window is the steady range; half a beat (≈0.28 s at 107 BPM) of
   context is a deliberate, bounded excursion, and heroes still keep their
   whole range to within that slack. Measured on the acceptance edit: 14/14
   cuts snapped, max 0.4 ms from a beat (was 3/14).
2. **`@music@` never reached the stations.** `station["argv"]` is a list of
   command lists, so the flat token substitution in `resolve_argv` missed it
   and render got `--music @music@`. **Fix:** substitute inside each command;
   and both directors now refuse a `--music` path that does not exist, so a
   substitution bug fails at the director instead of silently recording a bad
   path in `edit.json`.
3. **Test photos had the wrong time base** (local wall-clock in EXIF while the
   clips' `creation_time` is UTC), which put every photo after the last clip.
   Test-data fix only: the pipeline's chronology was right.

Also: `recap.py --no-audio-clean` forwards to both renders (the web app's
audio-clean-up option).

---

## [2026-10-15] D2-8 reframe stack: YuNet + TrackerMIL + saliency fallback (Haar is gone in OpenCV 5)

**Context:** `scripts/reframe.py` (D2-8, Shorts subject tracking) needs a face
detector, a tracker between detections and a no-face fallback. The build venv
runs **OpenCV 5.0.0** (`opencv-python`, CPU), which changes the options: the
classic `cv2.CascadeClassifier` API and the Haar XML files are **removed**
(`cv2.data.haarcascades` is empty), so the Haar path in `shorts_generator.py`
(`detect_face_offset`) is already broken on this venv and could not be kept.
Measured on Kotor proxy frames (1920x1080, LXC CPU), 2026-10-15.

**Face detector options compared:**
- **YuNet via `cv2.FaceDetectorYN`** — OpenCV Zoo `face_detection_yunet_2026may.onnx`
  (dynamic input shape, the zoo's default for OpenCV 5.x) and `..._2023mar.onnx`
  (fixed shape): **MIT** (model directory LICENSE), **224 KB / 227 KB**,
  ~28–74 ms per 1080p frame on this CPU, 5 landmarks + score, faces ~10x10 to
  300x300 px. Both models give identical boxes on the test frames; 2026may is
  the forward-compatible one (symbolic H/W dims for the OpenCV 5 ONNX engine).
- **Haar cascade** (`cv2.CascadeClassifier`, BSD-3, ~900 KB XML, ships with
  OpenCV 4.x) — **not available in OpenCV 5.0.0** (API and XML files removed);
  frontal-only and weaker on profile faces anyway.
- **SCRFD / RetinaFace / YOLO-face ONNX via `cv2.dnn`** — Apache-2.0/MIT, 2–30 MB,
  more decode code, no accuracy need for a crop path; rejected.

**Tracker options compared** (between detections every 8 frames = 0.32 s, scored
by IoU against per-frame YuNet boxes on 0091 orbit, 75 frames):
- **`cv2.TrackerMIL`** (OpenCV 5 main, no model download, Apache-2.0): **IoU 0.942**.
- **Linear interpolation between detections** (no tracker): **IoU 0.946**.
- **`cv2.TrackerVit` + `object_tracking_vittrack_2023sep.onnx`** (OpenCV Zoo,
  Apache-2.0, **698 KB**): **IoU 0.897**.

**No-face fallback options compared** (0083 locked-off shot, other boat enters
and approaches; 0081 people + cave):
- **Saliency map** — spectral residual (numpy FFT) centroid: x 0.20–0.36 / y 0.68–0.96
  on 0083 (lands on the wake, not the boat); motion-blob centroid jumps
  0.04 → 0.91 (wake + moving-camera parallax dominate). Free and always runs,
  but weak on boat footage — usable only behind a stability gate (hold the last
  good centre instead of chasing the wake).
- **Object detector** — OpenCV Zoo `object_detection_nanodet_2022nov.onnx`
  (NanoDet-m-plus-1.5x, **3.6 MB**, Apache-2.0, COCO incl. `boat`/`person`):
  **does not run on this OpenCV 5.0.0 pip build** (`reshape2_layer` assert in the
  new DNN graph engine; the wheel is built `WITH_ONNXRUNTIME=OFF`, so
  `OPENCV_FORCE_DNN_ENGINE=4` cannot fall back either). YOLOX (35.8 MB) is the
  heavier cousin with the same engine risk. Kept as the P1 upgrade.

**Decision:**
- **Detector: YuNet** `face_detection_yunet_2026may.onnx` via `cv2.FaceDetectorYN`,
  downloaded once to `~/.cache/yt-pipeline/models/` (never into git), MIT.
- **Tracker: `cv2.TrackerMIL`** between detections (zero download, measured at
  parity with plain interpolation, and it keeps following through short
  detection gaps where interpolation would freeze). Re-init on every detection;
  subject choice by continuity (nearest to the previous subject), never "largest
  face", so the crop never jumps between people. VitTrack is the upgrade path if
  MIL drifts on fast subjects.
- **No-face fallback: saliency map** (spectral residual + frame-difference
  motion, scene band with the bottom wake band down-weighted) with a stability
  gate: when the saliency is flat or jumps, hold the last good crop centre.
  The object detector stays the P1 backlog row until OpenCV 5's DNN engine (or
  an ONNX Runtime build) loads NanoDet.
- Crop path JSON carries `x`,`y` = crop window top-left in source pixels per
  timestamp, any aspect (`9:16` default); `x_offset` in `shorts_config.yaml`
  still overrides with a fixed crop.

**Rationale:** OpenCV-first (the D2-8 backlog principle): the detector, the
tracker and the fallback are all free CPU measurements, one 224 KB MIT model
download, no new dependencies; the measured numbers above are the pick, not
taste. Haar had to go anyway on OpenCV 5.

---

## [2026-10-15] D2-8 describe mosaic: event frame times, one image per group, legacy behind a flag

**Context:** the D2-8 PART 1 backlog row: tile 6-9 frames of a segment into one
mosaic image with their timestamps, pick the frames at OpenCV scene changes and
motion phase boundaries instead of fixed 20/50/80 %, send one mosaic per segment
(more on a long take) instead of separate frames, and keep the old path behind a
flag. Measured old vs new on the Kotor eval set
(`eval/ground_truth_boat_2026-10-12.yaml`, 6 clips, 6.92 min of footage,
`z-ai/glm-5.3-flash` vision, 2026-10-15).

**Decision:**
- **Frame times** (`--frame-mode mosaic`, the default): candidates are the
  segment's two edge anchors (`start+0.05`, `end-0.05`), the motion phase
  boundaries from `motion/<stem>.json` and the OpenCV scene changes measured on
  the segment (grayscale frame difference every 0.5 s, peak over
  `max(3 x median, 8)`, merged within 1 s). Target count: 6 frames up to 20 s,
  else `min(24, max(6, ceil(span / 5)))`; events win over the even fill, times
  are deduped below 0.25 s.
- **Mosaic**: tiles 480 px wide with the timestamp burned in (`12.4s`), grid 3
  columns (2 when a group has <= 4 tiles), black canvas, JPEG 85; the tiles are
  split into consecutive groups of at most 9 (`ceil(N/9)` groups, sizes as even
  as possible) and **each group is one model image**. The facts block lists every
  tile timestamp and a `long: yes|no` line that keys the beats rule (the old
  "more than three frames" trigger no longer works now that short segments carry
  6 tiles).
- **`--frame-mode legacy`** keeps the old path exactly: fixed interior times
  (three frames, or one every ~5 s capped at 24), one image per frame, the old
  facts block. It is the A/B baseline, not the default.

**Measured A/B (6 GT clips, `describe --force` then `index` then
`eval_labels.py --labels shotlog`):**

| | legacy (old) | mosaic (new) |
|---|---|---|
| move score | 0.678 | **0.681** |
| role score | 0.996 | 0.996 |
| horizon score | 0.970 | 0.970 |
| events F1 | 0.667 | 0.667 |
| model images sent | 134 | **35** (−74 %) |
| frames shown to the model | 134 | **216** (+61 %) |
| content beats found | 12 | **15** |
| vision cost | 0.03643 USD | **0.03098 USD** (−15 %) |
| **vision cost per minute of footage** | **0.00526 USD/min** | **0.00447 USD/min** |

**Rationale:** the mosaic is not worse on any eval component (move is a hair
better, the rest identical), shows 61 % more frames for 15 % less money and
cuts the number of model images by three quarters — so it ships as the default
and the old path stays behind `--frame-mode legacy` for the next bake-off
(frames vs mosaic vs native video input, P1). Frame times at real events also
found more content beats (15 vs 12), which is what the long-take split needs.

---

## [2026-10-15] Coder empty replies were upstream rate limits: provider fallback + wait-and-retry

**Context:** the empty coder replies of D2-6, D2-7 and D2-8 (DeepSeek V4.1 Flash and GLM 5.3 Flash alike) were OpenRouter `429 rate-limited upstream` errors from the provider serving the model (found in `~/.local/share/opencode/log/`). The architect then did the coder's work on the more expensive model: $9.78 on the night of 2026-10-15. Credit was not the cause (balance fine).

**Options:** (A) provider routing in `opencode.json` — `allow_fallbacks: true`, `sort: "throughput"` so OpenRouter prefers fast providers and moves on when one is busy; (B) wait and retry once before the architect takes over; (C) bring-your-own-key at the model vendor for private rate limits (another account and bill).

**Decision:** A + B now (no cost). C only if 429s continue. GLM 5.3 Flash stays the coder; compare takeovers after the next phase.

---

## [2026-10-15] Web app look: one theme for the app, Why? and Shots (D2-9 design pass)

**Context:** Doru's first look at the D2-7 web app on the phone: works, but looks like a test page, and the demo clip will show it.

**Decision:** one palette across `serve.py`, `explain.html` and `shotlog.html` (Kotor at night: deep water background, limestone text, sea-teal accent; green used / red left out / amber running), a serif for names and titles with the system sans for the interface, no web fonts (the app stays offline and private), 44 px touch targets. The memorable element is the edit strip under the player: the edit as proportional blocks (clip vs photo), tap a block to jump there, the current block lights up while playing. Plain labels: "New recap", "Clean the sound", "in the edit as v3" / "left out". Projects are ordered by last activity, not by name.

---

## [2026-10-15] Length "All good shots" and the estimate on the project page

**Context:** Doru wants to say the length he wants and be told how long the edit would be with every good shot in it.

**Decision:** `scripts/common/pacing.estimate_all_good(shotlog)` counts every eligible, non-suppressed, non-duplicate shot with score ≥ 0.5 at the length a long cut gives it (static hold 4 s, move 5 s, hero/semantic move 6 s, photo 3 s, capped by its window). The project page shows "All good shots would make about N (M shots)". `--target-duration-s 0` (web: Length → All good shots) sets the target to that estimate and uses the long-cut pacing (the 180 s anchor) in both directors. A workspace moved to another folder still finds its proxies (index thumbnails, render) through `<workspace>/proxy/`.

---

## [2026-10-15] Shot length policy: static shots keep the time limit, camera moves keep the move (middle ramp up to 1.2x)

**Context:** Doru's rule: static shots must respect the time limit; for a pivot, pan or orbit keep as much of the move as possible, the middle may be sped up, at most 1.2x; the seconds rule must hold for 80-90 % of the shots. Until now hero and semantic-move slots kept the last `hero_max` seconds of their window and everything else was capped at `slot_max`, so a static hero hold ran to 6 s and a pan was cut wherever its pacing target ended.

**Options:** (A) a global speed-up of the whole slot (changes the look of the whole move, audio pitch/tempo audible); (B) a ramp: head and tail quarters in real time, middle half at `s` (the speed change sits in the part of a move where it is least visible); (C) no speed change, only longer moves (breaks the seconds rule more often).

**Decision:** B. `edit.json` video slots get the optional `middle_speed` (1.0-1.25, default 1.0): the middle half of the source window plays at that rate; on-screen length is `0.25L + 0.5L/s + 0.25L` (`common/moves.py`, used by both directors, render, serve, explain, export). `common/moves.apply_move_policy` runs in both directors after slot selection and before the beat snap:
- a *move* = role `move`/`transition_in`/`transition_out` or important, never when the label is `static`/`minor_motion`;
- a move keeps as much of its candidate window as its on-screen budget allows (never outside the window, never overlapping another slot of the same take, a landing phrase keeps its end); `middle_speed` is used (at most 1.2) only when it lets more of the move in;
- the budget of an ordinary move is its pacing length (capped at `slot_max`), so the edit's total length does not grow; at most `15 % (rounded)` of the slots (minimum 1), the best scores among the moves that want more than `slot_max`, may run up to `hero_max` on screen and are reported as `exception:` warnings;
- every other slot, every static or hold slot (a static hero included) and every photo is clamped to `slot_max`;
- the beat snap works in on-screen time (a ramped slot's source moves by `delta / (0.5 + 0.5/s)`), never lengthens a slot past its cap (a cut whose nearest beat would break the cap goes to the beat before it) and keeps head/tail quarters in real time.

The render builds a ramped slot from three trims (middle `setpts=(PTS-STARTPTS)/s`, ambient audio `atempo=s` on the middle) and is unchanged for `middle_speed` 1. Kdenlive cannot express the ramp: the fragments stay real time and the export warns. The LLM validator accepts windows that start earlier than `window_out - hero cap` and a move longer than `slot_max` (up to the hero cap) on at most 15 % of the slots; a static hero hold is a static shot, not `keep_whole`.

**Golden changes (intentional):** `hero_present` hero hold `26.5-30.0` becomes `27.5-30.0` (static hero clamped from 3.5 s to `slot_max` 2.5 s); `phrase_move_strong` clipA `8.75-10.75` becomes `6.932-10.75` (the move is the one exception: 3.5 s on screen, middle at 1.2x). Director tests that measured source length now measure on-screen length.

**Consequence / open point:** an ordinary move only gains the ~9 % of source the ramp buys inside its pacing length; letting every move grow to `slot_max` would keep more of each move but lengthen a 90 s edit by about a fifth. Doru can change that budget in one line (`apply_move_policy`).

---

## [2026-10-15] Vertical 9:16 recap from the same edit (D2-11 part 2)

**Context:** Doru wants a Shorts/Reels version of a recap without a second edit.

**Options:** (A) a second edit for vertical; (B) the same `edit.json`, each slot cropped with a moving window from `reframe.py`; (C) a fixed centre crop.

**Decision:** B. `render.py --format 16x9|9x16|both` and `recap.py --format` (stations `render_vertical`, `render_final_vertical`). `reframe.py` runs as a subprocess once per source clip (on its proxy, path rescaled to the rendered file) and is cached under `<workspace>/reframe/`; the `crop` filter runs on source time before the trim so a `middle_speed` ramp keeps the window on the subject. A failed reframe gives a centred crop and a warning, never a failed render. Photos keep Ken Burns; a landscape photo is cropped to 9:16 around `ken_burns.focus_x`. 720x1280 preview, 1080x1920 final. The web app has a Format select (Auto = 16:9) and shows/serves the vertical files. Same duration as the 16:9 render by construction (same edit, same slot lengths).

**Consequence:** the crop is only as good as reframe's subject tracking; a vertical recap costs one extra render plus one reframe pass per clip (cached).

---

## [2026-10-15] Normal speed by default, cuts centred on the best moment (D2-12)

**Context:** D2-11 sped every long move up to 1.2x by default and kept the end of the window for important shots. Doru's corrections: normal speed unless he asks for the ramp, and when a move is longer than its allowed on-screen length keep the part around its best moment (a push through a gate is best at the gate, usually the middle), not the start or the end.

**Options:** (A) keep the ramp default and add a peak only to the trim; (B) ramp optional, peak-centred trim with a layered peak source; (C) peak only from the vision model.

**Decision:** B. The ramp is opt-in: `--speed-ramp` on `director.py`, `llm_director.py` and `recap.py`, and a Speed ramp select (Auto = off / off / on) on the web Upload and Re-edit forms. With the ramp off `apply_move_policy` never sets `middle_speed` but keeps the length policy (static/holds/photos <= `slot_max`, at most 15 % rounded of the slots up to `hero_max`). `meta.speed_ramp` is stored so a changed setting re-runs the edit. The trimmed window is centred on a peak chosen by `common/peak.py`: (a) `peak_s` from describe (new field on segments and beats, validated inside the span, copied by `index.py` into `shotlog.json`), (b) the maximum smoothed `scene_activity` of `motion/` inside the window when it exceeds the clip median by at least 0.001, (c) the window middle. The cut is clamped inside the candidate window and the steady range. The mock vision backend returns a deterministic `peak_s` (35-65 % of the span) so offline runs use path (a); it is not truthful.

**Why:** the vision model sees the subject, the motion signal is free and always available, the middle is a neutral fallback. Priority follows reliability of intent, then availability.

**Landing phrases:** a move followed by its landing hold keeps the landing rule (the cut ends at or after the floor), so the peak can fall outside the cut; the director warns. Choosing the peak over the landing would drop the landing hold and is left to Doru.

**Golden changes (intentional):** nine fixtures changed only in `expected_slots`: windows that started at the head of the window are now centred on the peak, which is the window middle in the fixtures (for example hero 13.75-16.25); `phrase_move_strong` is 7.25-10.75 (ramp off, hero cap 3.5 s); in `photo_slots` the first photo now sorts before the video because the video slot's chronological key moved later.

**Consequence:** D2-11 behaviour is reproduced exactly with `--speed-ramp` when no peak is known; with a peak, ramped cuts are also peak-centred. A peak from the 1 s motion signal is coarse (about +-0.5 s).

---

## [2026-10-15] Kept versions, honest run state, smooth Ken Burns, exact length (D2-13)

**Context:** Doru's re-edit on the moved kotor-demo workspace failed at `render_vertical` (the photo path still pointed at the old `/tmp` folder). The page then showed the new strip over the old video and stayed `running`. He also asked to keep old edits next to new ones, for smooth photo zooms and for a typed length.

**Options (versions):** (A) git-like history of `edit.json` only; (B) one folder per version with the edit, the renders made from it and a small `version.json`; (C) suffix files in the workspace root.

**Decision:** B, in `scripts/common/versions.py`, called by `recap.py` after a run whose edit or render stations ran. Renders are hard-linked (they are replaced atomically by `render.py`), small files copied; a render older than `edit.json` is never kept with it. The first versioned run keeps the existing recap as v1. `serve.py` shows the newest version with a video, chips per version and a compare view. Photos resolve like clips: a stale `sources[]` path falls back to `<workspace>/proxy`. Render stations no longer stop each other. `serve.py` reaps its child (a zombie pid read as `running`) and reads progress and failure from `run.log` after a start marker. Ken Burns: the frame is enlarged up to 4x (max 8000 px wide) in yuv444 before `zoompan`, motion is smoothstep-eased, and pans stay inside the zoomed frame (before, half of a pan was clamped still and the rest moved in 2 px steps: 51 of 99 frames stood still; now 5, at the eased ends). "All good shots" now really sends `--target-duration-s 0` (the form value was dropped before).

**Consequence:** each version costs only its JSON (videos are links). Versions are never deleted by the tools.

---

## [2026-10-15] Best shots win over time coverage; the model's edit is repaired, not discarded (D2-14)

**Context:** On kotor-demo (60 s, chronological) the best shot of the day (0072/s7b1, hero, 0.95) and the tunnel approach (0072/s5, 0.91) were left out while a 0.68 shot with a tilted horizon got in. The screenwriter had planned the tunnel as three slots in a row; the validator called that a same-take jump cut, the three rounds ended with violations and the whole LLM edit was replaced by the rule director, silently. The rule director split the day into equal time buckets, one best shot each, so the long tunnel take got one slot.

**Options:** (A) keep the rules, only explain better; (B) stars first in the rule director, repair instead of fallback in the LLM loop, and allow same-take moves that read as an ellipsis; (C) drop chronological buckets entirely (score order then sort by time).

**Decision:** B. Chronological picks give up to 40 % of the slots to heroes or shots >= 0.85 first, then fill the uncovered buckets. Two cuts of one take may sit back to back when both are camera moves, forward in time, at least 8 s apart (static framings still never). The LLM loop repairs a draft's mechanical violations from the shot facts (rebuild the cut, drop slots outside the plan, drop the weaker of a jump-cut pair) and only falls back when that fails. `meta.director` (schema) records `llm`/`rules`, approval and notes; `explain` runs as a recap station after the edit, shows who made it and marks an unused plan; the Shots tab marks every card "in the edit as vN" or "left out: reason" with filters (All / In the edit / Good but left out).

**Why not C:** coverage of the day still matters for a recap; stars-first keeps both.

**Consequence:** a strong stretch (tunnel, cave) can hold several slots; the 8 s / 0.85 / 40 % numbers are first guesses to tune on real footage.

---

## [2026-10-15] Best photo of a burst: no closed eyes, no blur (D2-15)

**Context:** Doru takes several photos of one moment; the edit should keep the one where nobody blinks and nothing is blurred. The burst winner was the highest score only.

**Options:** (A) OpenCV only: Haar eye cascades (not shipped with OpenCV 5) or YuNet landmarks (no eye openness) plus Laplacian blur; (B) ask the vision model, which already sees every photo, for a fixed list of flaws, plus a measured Laplacian sharpness; (C) a dedicated blink model (new dependency and download).

**Decision:** B. `describe` asks for `photo_flaws` (eyes_closed, blur, motion_blur, tilted, overexposed, underexposed, obstructed, cut_off) and stores a measured `sharpness`; `index` picks the burst winner without a spoiling flaw first, then by score weighted 0.7-1.0 by relative sharpness, and makes a lone photo with a spoiling flaw ineligible. Old photo descriptions are redone once (cents). Photo paths fall back to `<workspace>/proxy` like clips.

**Consequence:** eye judgement is only as good as the vision model; sharpness is relative inside a burst, never an absolute threshold (a clear sky is "soft").

---

## [2026-10-15] Loop guards for the build agents (D2-16)

**Context:** The D2-16 architect ran almost 3 hours, spent about $3 against a $1.50 brief budget, re-read the same code and produced an 18-line fix (a good one). It edited on master and never committed, so the watchdog could only warn.

**Options:** (A) switch to OpenHands for its stuck detector (repeats of action/observation x4, action/error x3, monologue x3, ping-pong x6); (B) use opencode's own guards plus a watchdog that can stop the run; (C) only stricter briefs.

**Decision:** B. opencode `steps` (architect 150, coder 80: at the cap the agent must stop and summarise), `doom_loop: deny` (unattended runs cannot answer the prompt), watchdog `--kill-over-usd` and `--kill-no-commit-hours` (cron every 15 min, 2 USD, 1.5 h), and architect rules: branch first, commit every finished step, check spend before expensive steps, re-reading twice without change is a loop.

**Why not A:** a second agent stack (Docker runtime, new config) for a guard opencode mostly has; revisit if B still lets loops through.

**Consequence:** a run that loops is stopped within about 15 minutes of crossing a limit; budgets are enforced from outside the agent.

---

## [2026-10-15] Still vertical crop without a face; repair drops cuts that cannot be valid (D2-17)

**Context:** On kotor-demo v3 the 9:16 version shook on clips with no person: the saliency centre followed waves and wake. The model's edit was again replaced by the rules because two planned shots had windows shorter than the slot minimum, so no rebuilt cut could pass.

**Decision:** A clip range with no face keeps one still crop on the median salient centre (render cache renamed `_reframe_v2` so old paths are recomputed). After rebuilding cuts, the repair drops any planned slot whose window still cannot give a valid cut, instead of discarding the whole draft. An eligible photo with a minor flaw (e.g. cut_off) is reported as "did not fit the length (minor flaw: …)", not as a defect.

**Consequence:** no camera-style pan on faceless vertical clips (a slow deliberate pan could come back later as a Ken Burns-like option); the model edit survives short-window mistakes.

---

## [2026-10-15] Edit expectations as a test; the model keeps the top heroes (D2-19)

**Context:** On kotor-demo v4 the edit was "by: llm" (repair works) but the model dropped the top heroes 0072/s5 (0.91, the tunnel approach) and 0072/s7b1 (0.95, the bunker mouth). Doru wants his feedback turned into a file agents check, so they iterate without him, and the LLM director to keep the best shots the way the rule director already does.

**Options (expectations):** (A) a new schema for edit expectations; (B) a small hand-written YAML checked by a standalone tool, no schema; (C) encode the checks in pytest fixtures only.

**Options (heroes):** (A) only explain better in the prompts; (B) a blocking `hero_missing` violation plus a repair that swaps the star in, never falling back to rules; (C) force the screenwriter to list every star.

**Decision:** B and B. `eval/kotor-edit.yaml` holds `must_include` items (an `any_of` list of shot keys and a note) and `length_s: {target, tolerance}`; `scripts/eval_edit.py` (standalone, `--help`/`--json`/`--out`, read-only) prints PASS/FAIL per expectation and exits 1 on any fail. `llm_director.validate_edit` raises `hero_missing` when an eligible star (hero or score >= `STAR_MIN_SCORE` 0.85) from the top `STAR_SHARE` (40 %) of the slots is left out of the plan; it is blocking for the reviewer, and `repair_draft` swaps it in for the weakest non-star slot whose length is within a factor of two, never creating a same-take jump cut (`common.moves.is_jump_cut`). `STAR_MIN_SCORE`, `STAR_SHARE` and `is_star` moved from `director.py` to `common/moves.py` so both directors import one value (the "no tool imports another tool" rule). A dropped hero photo stays the non-blocking `hero` warning. Prompts get one line each: keep the top-scored heroes unless the brief excludes them, and say which and why in notes if one is dropped.

**Why not schema (A):** two keys and a length do not need a schema yet; the YAML is the interface and stays safe to commit (shot keys and numbers only, no footage, no paths).

**Consequence:** Doru's review is a file any agent can run (`eval_edit.py`); a re-edit that drops a top star fails the check and the repair is supposed to fix it inside the LLM loop instead of silently falling back to the rules.

---

## [2026-10-17] Thumbnail tool, brand kit and the PSD writer (D6-48, pulled forward by Doru)

**Context:** Doru pulled the D6 thumbnail tool forward (issue #48): one command must turn a portrait photo + a title into a branded 1280x720 YouTube thumbnail identical to his approved `DEPPSiT-template-v2` (kept locally, not in the repo). Two new dependencies were needed: a background-removal model and a PSD writer.

**Options (PSD writer):** (A) `psd-tools` — MIT, actively maintained (1.25.0 released 2026-10-14), `PSDImage.new` / `create_pixel_layer` / `save`, negative layer offsets survive a round trip; (B) `pytoshop` — BSD, last release 1.2.1 in 2018 (Python 2.7/3.4-3.6 classifiers), read/write but unmaintained.

**Options (cut-out):** (A) `rembg` 2.0.85 (MIT, maintained) with the `birefnet-portrait` session model, the one Doru tested on 2026-10-17 against PhotoCraft, `u2net` and `isnet-general-use` (only BiRefNet kept hair, both hands and all fingers with no haze); (B) PhotoCraft `layer.removeBackground` — rejected (poor edges, and no PhotoCraft dependency anywhere in the pipeline); (C) MatAnyone, the D6 video background remover, not applicable to stills yet.

**Decision:** A and A. `scripts/thumbnail.py` writes the PSD with psd-tools (one rasterized layer per element: background, portrait, title, keyword, kicker, tag — text layers cropped to their alpha bbox so each block moves in Photoshop) and cuts the portrait with rembg's Python API (`new_session("birefnet-portrait")`, lazy import, `requirements.txt` line `rembg[cpu]` since 2.0.85 ships without an ONNX backend in the base extra). pytoshop and PhotoCraft stay out. The model downloads on first run to `~/.rembg/models/birefnet-portrait/` (2.0.85; older rembg used `~/.u2net/` — the integration test checks both). On the 6 GB build LXC the BiRefNet inference is OOM-killed even at 450x600 input: the integration test runs the cut-out in a subprocess and skips on exit 137 instead of killing the suite.

**Brand kit:** the look is data — `brand.json` at the repo root (re-included in `.gitignore`), validated by `schemas/brand.schema.json`: canvas, palette, font files (Montserrat Black + JetBrains Mono, SIL OFL, shipped in `assets/fonts/` so a fresh clone renders; paths are configurable and a missing font is a hard error naming the path, never a fallback), layout baselines, drop shadow, portrait margins/placement, the generated-background recipe (seed 7 grain) and the per-theme colour roles. `--brand` points at another kit.

**Interpretations the brief left open (one-line changes in `brand.json`/`thumbnail.py` if Doru wants them different):** the title is one line that shrinks 4 px steps to a 40 px floor (a wrapped second line would collide with the keyword's first baseline); the kicker stays `warm` in both themes (the brief only fixes it for `tech`); `--background` given with `tech` replaces the generated background (darkened like `creator`); the outer glow is baked inside the PSD `portrait` layer (exactly the six listed layers, no seventh glow layer — in the JPG it is screen-blended as specified); the drop shadow follows the Photoshop angle convention (120° = light upper-left, shadow toward the lower right at +2.0, +3.46 px).

**Consequence:** `python scripts/thumbnail.py --photo ... --title ... --keyword ... --theme tech|creator [--psd]` produces the branded thumbnail from a brand file alone; Doru runs it once on a real photo (the one thing the build LXC cannot verify). PSD text is rasterized — retouching means moving/repainting layers, not editing type.

---

## [2026-10-19] Video background removal: RVM by default, MatAnyone2 personal-use only (D6, issue #50)

**Context:** Doru needs his talking-head footage cut out and placed over screen capture (first use: the Second Brain clip, edited in FilmCraft 0.9.0). He did it by hand in Sammie-Roto 2 (SAM2 + MatAnyone2, GUI clicks + export). The D6 roadmap and the thumbnail decision above named MatAnyone as the video background remover, but its licence (NTU S-Lab 1.0) allows non-commercial use only, and the pipeline may be sold later.

**Options (tested 2026-10-19 on the same 10.6 s 1080p25 clip, RTX 4070 Laptop 8 GB, full numbers in issue #50):** (A) SAM2 + MatAnyone2, the Sammie models driven from a script — best quality, 48 s, non-commercial; (B) EdgeTAM + ViTMatte-small (the stack of FilmCraft draft PR storytold/filmcraft#2122, run in Python on GPU) — Apache-2.0, 92 s, clearly worse in Doru's review; (C) RobustVideoMatting resnet50 — GPL-3.0, fully automatic, ~15 s, steadiest edges (`downsample_ratio` 0.6 steadier still); visibly behind A only on fast, motion-blurred hands. A permissive model matching A was searched for and not found (SAM2Matting, VideoMaMa, MaGGIe are all non-commercial).

**Decision:** C by default, A as an optional backend for Doru's own videos only. Doru compared C and A side by side in FilmCraft and judged C "more than enough". Output is ProRes 4444 with alpha (`yuva444p10le`, `prores_ks -profile:v 4444`); FilmCraft 0.9.0 reads its alpha correctly. RVM code is never vendored into this repo: it is fetched at first run through `torch.hub` (GPL obligations then stay with the downloaded code; the videos produced are not GPL). MatAnyone2 is never the default and never used for paid work without written permission from its authors. MatAnyone2 gotcha: the first-frame mask goes in on a 0–255 scale, not 0–1, or the first frame comes out empty and tracking degrades.

**Why not B:** permissive licence, but per-frame ViTMatte flickers (1.7x the edge noise of Sammie) and a temporal EMA that hides it leaves ghost halos around moving hands.

**Consequence:** prototypes live outside the repo in `~/Proiecte-AI/YouTube/roto-lab/` (`rvm_test.py`, `matanyone2_test.py`, `edgetam_vitmatte.py`); the pipeline step (`scripts/matte.py <video> --start/--end --backend rvm|matanyone2`) is built when the Second Brain edit needs it. The repo still has no LICENSE file; one must be chosen before any commercial use. FilmCraft's native rotoscoping (#2122, #406) is being followed and may replace this step for manual work.

---

## [2026-10-20] Watchdog: per-run spend cap from a run-spend ledger (S51 lot 0)

**Context:** the agent budgets are per run (per brief / per lot), but the watchdog only knew "spend today" from the OpenRouter key. A run that started at $0 and a day already at $2.90 looked the same. Issue #51 lot 0 asks for spend since the run started, a `--run-cap` from the brief, an ntfy at 80 % (`Budget`) and a stop at 100 % (`Blocked`).

**Options:** (A) baseline `usage_daily` at run start and measure the delta on every cron pass, nothing on disk; (B) the same delta, but appended to a per-run spend ledger file (`entries[].cost_usd`, the `common/llm.py` entry shape) that the watchdog sums since `run_started_at`; (C) read the opencode session DB for per-message model cost.

**Decision:** B. `agent_watchdog.py --run-cap USD --lot "S51 lot 1" [--ledger PATH] [--start-run]`. The watchdog keeps `~/.cache/yt-pipeline/run-spend.json` itself: every invocation appends the OpenRouter daily-usage delta since its previous observation as one `openrouter-delta` entry, so everything billed to the key (script calls through `common/llm.py` and agent model calls) is counted exactly once. A run starts with `--start-run` or implicitly the first time the agent is seen running after being absent; `run_spend_usd` sums the entries since then. 80 % of the cap sends `--title Budget` (never stops); 100 % stops the agent and sends `--title Blocked` naming the lot and the reason (independent of `--kill-over-usd`, which stays daily). `--dry-run` writes neither ledger nor state.

**Why not A:** nothing survives a watchdog restart and two cron passes cannot agree on the baseline. **Why not C:** the opencode DB schema is internal and does not cover script spend on the same key.

**Consequence:** the architect checks `run_spend_usd` before every expensive step (vision run, director run, coder round); the daily `--spend-usd` check is unchanged. Implemented by the architect after two empty coder replies (upstream rate limit).

---

## [2026-10-20] Transcription: openai-whisper behind a Transcriber interface (S51 lot 1)

**Context:** The publish chain needs per-source word timestamps when there is no edit project (`transcribe.py`), and the word JSONs it writes must be exactly what `subs_from_edit.py --words-dir` reads. The build LXC has no torch/whisper and must stay that way for the test suite.

**Options:** (A) `openai-whisper` (MIT, maintained reference implementation) — the one the sprint brief names: turbo on GPU, `--model tiny` on CPU; (B) `faster-whisper` (MIT, CTranslate2, faster on CPU, already on the real machine transitively via whisperx); (C) whisper.cpp binding (no Python wheel story here).

**Decision:** A, per issue #51 lot 1. `Transcriber` interface with `WhisperTranscriber` and `MockTranscriber` lives inside `scripts/transcribe.py` (the sprint puts `GraphicsEngine` in `scripts/graphics.py` the same way; it moves to `scripts/common/` when a second consumer appears). `torch`/`whisper` import lazily inside the whisper backend only, a missing import is a clear error naming the pip package, and `openai-whisper` joins `requirements.txt` for the real machine without being installed on the LXC. `--backend mock` is the deterministic offline run. The `_words.json` from `transcribe.py` is the Whisper result shape (`text`, `language`, `segments[].words[]`) — exactly the `subs_from_edit.py` input — while `subs_from_edit.py` writes the flat timeline list (`[{word, start, end}]`, the `shorts_generator.py` shape). Both are documented in the `yt-publish` skill.

**Why not B:** the brief names openai-whisper; faster-whisper stays the next candidate if CPU transcription speed ever matters.

**Consequence:** real runs need `pip install openai-whisper` on the laptop; tests and offline runs use `--backend mock` and never touch a model. Word times from this run are the initial transcript only — the Shorts flow still takes word timing from WhisperX forced alignment on the corrected SRT.

---

## [2026-10-20] Offline mock text per role and a YT_CONFIG override (S51 lot 1)

**Context:** `publish.py` must run end-to-end on the fixture with a mock LLM (issue #51 lot 1 acceptance). `llm_backend: mock` returned one fixed string, so structured consumers (`metadata.py` JSON, `translate_srt.py` `[N]` lines) could not run offline, and tests could not point a subprocess at a test config.

**Options:** (A) recorder-style chain tests only (what `test_recap.py` does) and per-tool mock tests; (B) `llm_mock_text` in the config (a string for all roles or a role→text mapping) plus a `YT_CONFIG` env override in `resolve_config_path`; (C) a `--mock` flag on every tool.

**Decision:** B. `MockClient` picks `texts[role]` and falls back to its default text; `make_client` wires `llm_mock_text` through; `YT_CONFIG` wins over the repo `config.yaml` and `config.yaml.example` (same pattern as `YT_SPEND_LEDGER`/`YT_WORKSPACE`). A chain test can now run `publish.py` as a real subprocess against the fixture with a canned metadata JSON and translate lines.

**Why not A:** it would not exercise the real output handoff between stations. **Why not C:** the backend is config, never code.

**Consequence:** offline end-to-end runs of any LLM tool work with one config file; production configs simply omit `llm_mock_text`.

---

## [2026-10-20] Metadata draft carries structured thumbnail photo concepts (S51 lot 1 addendum)

**Context:** The lot 1 addendum to issue #51 (Doru, 2026-10-20) asks `metadata.py` to also write a `## Thumbnail` section in `metadata.md`: 2-3 distinct photo concepts with pose, a real emotion (never neutral), 3-4 words of thumbnail text that say the same as the title (row 1 title, row 2 keyword, kicker), theme `tech` or `creator`, and whether to use a frame from the clip (timecode from the transcript where the emotion fits) or a new photo. The chosen concept must map directly to the `thumbnail.py` flags.

**Options:** (A) the model writes the `## Thumbnail` section as free-form markdown in its JSON; (B) a structured `thumbnail_concepts` key (2-3 objects with `name`/`pose`/`emotion`/`text{title,keyword,kicker}`/`theme`/`source{kind,timecode}`) validated like `chapters` and rendered by the tool; (C) no section for now — frame picking is issue #52's vision pass.

**Decision:** B. The tool validates the shape (2-3 concepts, non-empty name/pose/emotion, non-empty title and keyword, `theme` in `tech`/`creator`, `source.kind` in `clip_frame`/`new_photo`, a clip-frame timecode normalized and inside the transcript) and renders the section with the exact `thumbnail.py --photo --title --keyword --kicker --theme` command line per concept. Content quality (a real emotion, the text saying the same as the titles) stays the model's job, steered by `prompts/metadata.md` — same split as the CTA rules. The old one-line "Thumbnail text:" note is folded into the section; the `--json` summary reports `thumbnail_concepts` and every canned mock JSON in the tests carries the new key.

**Why not A:** free-form markdown cannot be checked and does not map to the `thumbnail.py` flags. **Why not C:** the addendum is in this sprint; #52 later picks candidate frames with the vision model on top of these concepts.

**Consequence:** `metadata.md` grows a `## Thumbnail` section whose concepts can be executed as-is (grab the frame or shoot the photo, then run the listed `thumbnail.py` command). The model contract (`prompts/metadata.md` + `validate_model_json`) changed in a backwards-incompatible way: old canned model JSON without `thumbnail_concepts` is rejected by design.

---

## [2026-10-20] matte.py: RVM through torch.hub, own frame loop, ProRes 4444 export (S51 lot 2)

**Context:** Issue #50 scope 1-2 / sprint lot 2: talking-head clip in, ProRes 4444 `.mov` with alpha out, only a time range processed, RVM as the default backend (decision 2026-10-19) and its code never vendored into this repo.

**Options (inference loop):** (A) the hub's `convert_video` converter API (whole-file, its own reader and outputs); (B) our own loop — `cv2.VideoCapture` plus the official recurrent call `fgr, pha, *rec = model(src, *rec, downsample_ratio)`, two PNG sequences and one ffmpeg `alphamerge` export; (C) TorchScript/ONNX backends instead of the hub.

**Decision:** B. `scripts/matte.py` validates every user error before the lazy `torch` import (so a missing torch never masks a bad argument), seeks with `CAP_PROP_POS_MSEC`, carries the 4 recurrent states across frames, writes `fgr/%05d.png` + `pha/%05d.png` in a temp dir and encodes with the issue #50 recipe (`prores_ks -profile:v 4444`, `alphamerge,format=yuva444p10le`) to an atomically replaced output. The model is `torch.hub.load("PeterL1n/RobustVideoMatting", "resnet50", trust_repo=True)` — `trust_repo=True` because a CLI must never block on the hub's interactive trust prompt; the clone and `rvm_resnet50.pth` land in `~/.cache/torch/hub/`. `--ratio` (`downsample_ratio`) defaults to 0.6 (steadier edges, the band issue #50 measured) with 0.25 documented as the faster option. The output is video-only (the editor keeps the original's audio). Unit tests run without torch; one integration test skips through `pytest.importorskip("torch")` and asserts the alpha on a generated clip. `torchvision` joins `requirements.txt` (hub dependency).

**Probes say `yuva444p12le` on ffmpeg >= 6.0:** issue #50 and the sprint acceptance name `ffprobe pix_fmt yuva444p10le`, but ProRes 4444 (`ap4h`) is a 12-bit codec — ffmpeg < 6.0 reported `yuva444p10le` for it and ffmpeg >= 6.0 decodes it as `yuva444p12le`. The encoder recipe is unchanged and the file is the same ProRes 4444 with alpha that FilmCraft 0.9.0 reads (tested on the Sammie exports); the integration test accepts both strings.

**Why not A:** `convert_video` owns its own reader and whole-file range, needs `av tqdm pims`, and still leaves the ProRes export to us. **Why not C:** no second model format is needed this sprint.

**Consequence:** `python3 scripts/matte.py clip.mp4 [--start S] [--end E] [--ratio R] [--out FILE] [--json]` produces the composite-ready matte on GPU when present, on CPU otherwise (build LXC: 3 s 640x360 at ratio 0.6 in ~45 s). The settings used on the real Second Brain clip are added to issue #50 as a comment after Doru's run; MatAnyone2 stays out of the repo (scope 3, personal-use only).

---

## [2026-10-21] HyperFrames is the graphics engine; Remotion left the plan (S51 lot 3)

**Context:** The D4 bake-off (Remotion vs HyperFrames) is cancelled. Remotion's licence is free only up to 3 people and paid above, which does not fit a pipeline that may be sold; HyperFrames is Apache-2.0 and already renders ProRes 4444 MOV overlays via `npx hyperframes render --format mov`. EffectCraft stays the option for hand-made brand assets via Lottie. Issue #51 lot 3 needs one CLI that turns a template plus props into a transparent overlay at timeline resolution.

**Decision:** `GraphicsEngine` interface in `scripts/graphics.py` with `HyperFramesEngine` (composition templates in `assets/graphics/` filled through `data-composition-variables`, rendered by the pinned `hyperframes@0.8.181` CLI) and `MockEngine` (composed HTML plus an ffmpeg transparent MOV, for tests and offline runs). Templates use `brand.json` values only; `graphics.py` holds no colour, font family or size constant. The CLI is pinned (`npx --yes hyperframes@0.8.181`), never a floating `npx hyperframes`. The HyperFrames agent skills are **not committed**: the published set is ~19 MB (~3.2 MB of markdown, 330 files) and carries no licence file beside it, over the 2 MB limit; the install command (`GIT_CLONE_PROTECTION_ACTIVE=0 npx skills add heygen-com/hyperframes --all`) and the pinned upstream commit `908f2d095bba4e2b97363c5533e2702f1e92505d` are documented in `yt-graphics` and the README. The runtime code does not depend on the skill files.

**What the render run taught us:** (1) The GSAP CDN (`cdn.jsdelivr.net/npm/gsap@3.14.2`) loads in the render on this machine, so the templates keep the CDN script and register a paused timeline on `window.__timelines`. (2) `@font-face` with an **absolute** path or a `file://` URL does not load in the HyperFrames render (the page is served from the project root); a path relative to the project root does. `graphics.py` therefore resolves the brand font to an absolute path (hard error naming path and brand key when missing), copies it into the render work dir under `fonts/` and references it relatively — a deliberate deviation from the brief's "absolute paths", documented here. (3) Number variables arrive as unitless CSS custom properties, so `font-size: var(--title_size_px)` is invalid; the templates use `calc(var(--title_size_px) * 1px)` and `calc(var(--tracking_em) * 1em)`. (4) `--strict-variables` passes when only declared keys are sent; `graphics.py` sends exactly the declared ids. (5) The plan schema's `allOf` requires props per template for **every** entry, including rejected ones; to honour the brief's "a rejected entry is never validated against props", `run_plan` filters out schema errors whose path is `entries/<i>/props` for a rejected entry, while still validating the rest of the plan.

**Why not Remotion:** paid above 3 people, and the bake-off is no longer needed once HyperFrames renders the alpha MOV the editor reads. **Why not commit the skills:** over the 2 MB markdown budget and no licence file beside them; they are agent knowledge, not a runtime dependency.

**Consequence:** `python3 scripts/graphics.py --template {lower_third,counter,callout} --props JSON --out OUT.mov [--width W] [--height H] [--fps FPS] [--duration SEC] [--brand brand.json] [--backend {hyperframes,mock}] [--json]` and `--plan graphics_plan.json --out-dir DIR` produce ProRes 4444 `.mov` overlays with alpha at timeline resolution; every output is checked with ffprobe for `yuva444p10le`/`yuva444p12le`. D4's graphics station builds on this `GraphicsEngine`; the plan tool (`graphics_plan.py`) is the next task.

---

## [2026-10-21] Licence: AGPL-3.0-or-later with a CLA (S51 lot 4)

**Context:** The repo was public with no `LICENSE` (RISKS #10). The pipeline may be sold, and it must survive on AGPL-compatible dependencies: RVM is GPL-3.0 but is loaded through `torch.hub` and never vendored; HyperFrames is Apache-2.0; the brand fonts are SIL OFL.

**Decision:** `LICENSE` is the GNU AGPL v3 text; every script carries `# SPDX-License-Identifier: AGPL-3.0-or-later`. External contributions are accepted only with a signed CLA granting the copyright holder the right to relicense the contribution, so dual licensing (e.g. a commercial closed-source edition) stays possible. `CONTRIBUTING.md` carries the CLA form.

**Why not MIT:** a permissive licence alone cannot keep a commercial edition closed — anyone could take the code and ship it under any terms, including a competing closed product.

**Consequence:** SPDX headers in every script under `scripts/` (including `scripts/common/`), `CONTRIBUTING.md` carries the CLA, RISKS #10 is closed.

---

## [2026-10-21] S51 sprint order and caps (issue #51)

**Context:** Issue #51 is the publish-kit sprint. It runs on one branch, `sprint-publish-kit`, with one PR to master at the end that Doru merges.

**Decision:** Lots run 0 → 1 → 2 → 3 → 4 with per-lot caps of 1 / 6 / 4 / 8 / 1 USD and a ceiling of 25 USD. Lot 3 (HyperFrames graphics) was the first to be cut if the total ran short — it did not have to be. Doru added lots 5 (FilmCraft bridge, 5 USD) and 6 (publish kit for manual upload, 4 USD) on 2026-10-21; they run after lot 4 on the same branch and PR. Lot 6 is variant B per Doru's 2026-10-21 comment: the API upload is parked because an unaudited Google project cannot make videos public.

**Why not split the sprint into separate PRs:** one branch and one PR keep the sprint reviewable as a whole and match the issue's plan.

**Consequence:** Nothing from D3 or the rest of D6 beyond the listed scope is in this sprint; D2-20 (issue #47) stays paused.

---

## [2026-10-21] Publish kit instead of API upload (S51 lot 6, variant B)

**Context:** YouTube locks every video uploaded through the Data API from an **unaudited** Google project as private and it cannot go public before the project passes the YouTube API audit (same for TikTok's Content Posting API); Doru changed lot 6 on 2026-10-21 accordingly.

**Decision:** No `yt_upload.py`; `publish.py --kit` writes `<project>/publish-kit/` (video + RO/EN SRTs + thumbnail + `studio.md` copy-ready blocks and a Studio-order checklist) with loud checks (title 100, description 5000, EN subtitles present, thumbnail <= 2 MB and 1280x720, tags <= 500); `metadata.py` writes `metadata.json` so the kit is deterministic from the validated model JSON; the lot 5 `loudness.py --video` pass is opt-in via `--kit-loudness`.

**Why not the API upload:** the audit gate above.

**Consequence:** The manual upload is 3-5 minutes in Studio; the API upload can come back after a Google project audit (parked, not deleted from the plan — see the issue comment).

---

## [2026-10-21] Phone review taps write the workspace's expectations.yaml (S54 lot 2)

**Context:** Issue #54 lot 2 adds two per-shot buttons on the Shots tab ("must be in" / "wrong label") that write "to the expectations file that `eval_edit.py` reads", and themes the run-progress `<progress>` bar from `brand.json` (Chrome on Android ignores `accent-color` on `<progress>`). D2-19 puts curated expectations in `eval/*.yaml` (committed regression fixtures); phone taps are live review data that belongs to one workspace.

**Options (destination):** (A) append the taps to the repo's `eval/<project>-edit.yaml`; (B) write a workspace-local `expectations.yaml` that `eval_edit.py --workspace WS --expect WS/expectations.yaml` reads; (C) only append to the existing `feedback.jsonl` action log.

**Decision:** B, with one extra top-level key. `serve.py` gains a single write route `POST /<project>/expectations` (urlencoded `shot` / `kind` / `note`; 200 JSON, 400 one-line text, 404 unknown project, 405 otherwise; GET on that path stays 404) that appends to `<workspace>/expectations.yaml` in the exact shape `eval_edit.py` already reads: `must_include: [{any_of: [shot], note}]` for "must be in" and `wrong_label: [{shot, note}]` for "wrong label". Taps are idempotent per (shot, kind), the write is atomic and the file is machine-owned (rewritten with a header comment, so hand-written YAML comments are lost). `eval_edit.py` is unchanged and ignores `wrong_label` — those rows are the record Doru and the agents read before the next re-edit or re-analysis. The progress bar takes `brand.json` `colors.cool` through `progress::-webkit-progress-value` / `progress::-moz-progress-bar` (fallback `#57b8c4`, never a start-up failure).

**Why not A:** live taps would mix into the committed regression fixtures. **Why not C:** the issue names the expectations file, and `eval_edit.py` must be able to check what the taps said.

**Consequence:** a phone review of the Shots tab becomes a file any agent can run (`eval_edit.py --expect WS/expectations.yaml`); curated expectations stay in `eval/`. The RULES serve line and the D2-19 expectations line grow the one write route and the workspace file.

---

## [2026-10-21] Mode B angles: paired edit.json slots sharing one id (S56 lot 1)

**Context:** Issue #56 lot 1 has `rough_cut.py` write an `edit.json` (the existing schema) that `export_filmcraft.py` opens with "the screen on Video 1 and the camera on Video 2, the same cuts on both". The reference project lays every kept window on two parallel tracks at one timeline position. `edit.json` has one sequential `video[]` timeline and `additionalProperties: false` everywhere, so two clips at one timeline position are not representable with unique slots — and the issue forbids changing the schema ("the existing schema").

**Options:** (A) extend `schemas/edit.schema.json` with a multi-angle field and teach every adapter; (B) encode the angle pair as two consecutive video slots sharing one `id` (screen first, then camera), understood by `export_filmcraft.py`; (C) only the camera angle in `edit.json`, the screen cloned by an export flag.

**Decision:** B. `rough_cut.py` writes one slot per angle of a kept window, both with the window's `id`; `export_filmcraft.py`'s `build_timeline` groups consecutive video slots with the same `id`, gives the group one timeline start (the cursor advances once per group), routes the members to Screen 1 / Camera 1 as before, emits one audio clip from the first non-screen member and one marker per group (Green with the window's reason, Rose when any member is flagged). Unique-id slots — every existing edit — behave byte-for-byte as before (regression test on `tests/fixtures/filmcraft/edit.json`).

**Why not A:** the schema is the interface the issue pins ("the existing schema"), and every adapter would need the multi-angle concept. **Why not C:** the pairing would live in a CLI flag instead of the edit list, breaking "edit.json is the only source of truth".

**Consequence:** a rough-cut `edit.json` is consumable by `export_filmcraft.py` (and `eval_rough_cut.py`); `render.py`, `subs_from_edit.py` and the web app still treat slots sequentially and a Mode B rough cut is not rendered in this slice. A kept window's `reason` names the cut before it (`cut: pause 1.2 s`, `cut: filler 'deci'`, `cut: retake of w007`, `start: session`) and becomes the FilmCraft marker; every merged cut run is a rejected `decisions[]` entry. RULES.md grows the Mode B paired-slots line.

---

## [2026-10-21] Mode B cut rules: pauses, fillers, retakes (S56)

**Context:** Issue #56 lot 1 turns talking-head + screen recording sessions into a rough cut a human finishes in FilmCraft; the reference is Doru's hand edit of the same material. The cut has to remove the dead air, the filler words and the restarted sentences without clipping words.

**Options:** (A) cut silence by audio loudness (auto-editor's approach, `docs/research/prior-art.md` §2.6); (B) cut by the word timestamps Whisper already produced (gaps between words, filler tokens, restarted sentences); (C) let an LLM choose the cut list.

**Decision:** B. A gap between words over `pause_gap_s` (0.6 s) is cut keeping `handle_s` (0.12 s) each side so words are not clipped; standalone filler tokens from a config file are dropped (`ă`, `îî`, `ăă`, `ăăă`; `deci`/`practic` only when repeated within 3 s); a sentence restarted by a later one within 60 s (normalised-word prefix similarity ≥ 0.55, ≥ 3 words) is cut, keeping the last take. Every cut run is a rejected `decisions[]` entry and the kept window's `reason` names the cut before it (the FilmCraft marker). Thresholds tuned on the reference eval (`docs/reports/S56-eval.md`): `retake_similarity` 0.75 → 0.55, `fillers.always` + `ok`.

**Why not A:** word timestamps already mark the pauses and clipping words is the known pitfall of loudness cutting (it needs denoising first). **Why not C:** the rule cut is deterministic and free; the optional `--llm` pass only proposes extra cuts for human approval and never changes the edit.

**Consequence:** `rough_cut.py` + `eval_rough_cut.py` + `mode_b.py`, the `yt-mode-b` skill, the sessions manifest schema; the cut rules are the Mode B defaults in `scripts/rough_cut_config_example.yaml`.

---

## [2026-10-21] Mode B one-command flow (S56 lot 3)

**Context:** Issue #56 lot 3 asks for one command that runs the whole Mode B flow on a folder of OBS recordings, the way `recap.py` is the single entry point for Mode A.

**Options:** (A) import `transcribe.py`/`rough_cut.py`/`export_filmcraft.py` and call their functions; (B) chain the three station CLIs as subprocesses (the `recap.py` pattern); (C) a shell script.

**Decision:** B. `scripts/mode_b.py FOLDER [--out-dir DIR] [--backend {whisper,mock}] [--config FILE] [--llm] [--force] [--json]` scans the folder (a `camera ...` file plus an optional sibling screen file), transcribes every session with no words JSON yet, writes `<out-dir>/sessions.yaml`, runs `rough_cut.py` and exports `project.fcproj`. The skip rule is "output exists" (`--force` re-runs every step); a failing step stops the run and the next run resumes at it. Outputs go under `<folder>/mode_b/`; originals are read-only.

**Why not A:** no tool imports another tool (the repo rule); the CLIs are the interfaces. **Why not C:** a shell script cannot carry the `--json` summary, the exit codes or the tests.

**Consequence:** one command turns a folder of OBS recordings into a FilmCraft rough cut; `mode_b.py` is the Mode B front door, `rough_cut.py` and `export_filmcraft.py` stay the stations.

---

## [2026-10-21] Unfinished reference: eval on the finished stretch; retake_similarity reverted (S56 follow-up)

**Context:** Doru has edited only the first 150 s of the Second Brain reference; the lot 2 precision_used 0.64 was not a real ceiling and the tuned thresholds were fitted to partly uncut material. `eval_rough_cut.py --until-s S` was added (commit 3d269b4) and the two lot 2 threshold changes were re-checked on the `--until-s 150` slice: A1 (`retake_similarity` 0.75, `fillers.always` with `ok`) gives precision_used 0.5619, recall_used 0.9629, F1_used 0.7097; B (`retake_similarity` 0.55, `fillers.always` with `ok`) gives precision_used 0.5638, recall_used 0.9496, F1_used 0.7075.

**Decision:** the headline numbers are the `--until-s 150` ones (precision_used 0.5619, recall_used 0.9629, F1_used 0.7097 for the final config); the full-sequence numbers are indicative only. `fillers.always` keeps `ok`; `retake_similarity` goes back to 0.75 because 0.55 costs 0.0133 recall_used for 0.0018 precision_used on the finished stretch.

**Consequence:** the Mode B cut rules run with `retake_similarity: 0.75` and the `ok` filler; **the eval is rerun when the reference edit is final** and the thresholds may move again.

---

## [2026-10-21] Phone cut review writes cut_review.yaml (S57 lot 1)

**Context:** Issue #57 lot 1 adds a Cuts view to the web app so Doru can approve or reject each automatic Mode B cut from the phone. The taps must feed back into `rough_cut.py` on a rerun and into `eval_rough_cut.py` as ground truth.

**Options:** (A) reuse `expectations.yaml`; (B) a workspace-local `cut_review.yaml` written by `serve.py` and read by both Mode B tools; (C) only a UI log with no tool integration.

**Decision:** B. `serve.py` gains a Cuts tab (Mode B projects only) and two routes: `POST /<project>/cut-review` (urlencoded `id`/`action`/optional `note`; 200 JSON, 400 one-line text, 404 unknown project, 405 otherwise; GET stays 404) appends idempotently per cut id to `<workspace>/cut_review.yaml` (atomic, machine-owned, header comment), and `GET /<project>/cut-audio?id=` serves a cached `audio/wav` of the cut span plus one second each side from the `sources[]` media. `rough_cut.py --review` keeps a `keep` run in the edit (reason `kept by review`, run id stable) and `eval_rough_cut.py --review` treats each row as extra ground truth that wins over the `--fcproj` reference inside its span.

**Why not A:** expectations are per-shot edit checks; cut review is per-cut-run and needs the clip/window. **Why not C:** the taps would not change the next rough cut.

**Consequence:** a phone review of the Cuts tab becomes a file both Mode B tools read; `schemas/` is unchanged (`decisions[]` stays frozen, cut words are derived at render time from `sessions.yaml` + the word JSONs).

---

## [2026-10-21] chapters.json is the chapter source of truth (S57 lot 2)

**Context:** Issue #57 lot 2 needs the topic chapters of a Mode B rough cut as a file the FilmCraft export can place as section markers and the publish kit can reuse, instead of asking the model for chapters again in `metadata.py`. The chapter rules already exist in `prompts/metadata.md` (first chapter at 0, non-decreasing, titles at most 40 characters, real topic changes).

**Options:** (A) ask the model again in `metadata.py` and keep chapters only in the publish draft; (B) a `chapters.json` written by a new `chapters.py` station (role `metadata`, one pass over the kept transcript in timeline time) that `export_filmcraft.py --chapters` and `mode_b.py` consume; (C) hard-code chapter times from the cut reasons.

**Decision:** B. `scripts/chapters.py edit.json WORDS [--out chapters.json] [--json]` maps the per-source Whisper words onto the edit timeline (the `subs_from_edit.py` midpoint rule; a Mode B pair is one window and its words are counted once), builds the kept transcript as `[MM:SS.d] words…` lines and asks the `metadata` role once (temperature 0.3, retry once on unparseable output). The first chapter is snapped to 0, starts must be non-decreasing and inside the transcript, titles are 1–40 and summaries 1–200 characters after strip; the file is validated against `schemas/chapters.schema.json` before the atomic write. `export_filmcraft.py --chapters` adds one Green section marker per chapter (`name` = title, `comment` = summary) and reports `markers.sections`; `mode_b.py` runs `chapters.py` between the rough cut and the export and passes `--chapters` when the file exists.

**Why not A:** the publish kit would ask the model for the same chapters a second time and the two answers could disagree. **Why not C:** chapter times are a content judgement, not a cut artefact.

**Consequence:** `chapters.json` is the chapter source of truth from `chapters.py`; the publish kit can read it instead of asking again (a later task), and the FilmCraft project opens with the chapters as Green section markers. `schemas/` and `scripts/common/` are unchanged.

---

## [2026-10-21] Shorts ranking writes shorts_candidates.json (S57 lot 3)

**Context:** Issue #57 lot 3 needs the best Shorts spans of a Mode B rough cut as a file `shorts_generator.py` can cut and the FilmCraft export can mark, instead of the generator's own hand-written `shorts_config.yaml` pick. A Short is a self-contained 20-60 s span that opens with a hook and ends on a payoff.

**Options:** (A) let the model propose spans freely from the transcript; (B) deterministic sentence-aligned candidates (20-60 s, inside one chapter) that the model only scores on hook and payoff; (C) a pure rules score with no model.

**Decision:** B. `scripts/shorts_rank.py edit.json WORDS [--chapters chapters.json] [--out shorts_candidates.json] [--json]` maps the kept words onto the edit timeline (the `chapters.py` midpoint rule; a Mode B pair is one window and its words are counted once), splits them into sentences on pauses over 0.6 s and builds candidates: for every sentence start, the longest run of consecutive sentences inside one chapter whose span is 20-60 s. One `director` pass (temperature 0.3, retry once on unparseable output) scores every candidate on `hook` and `payoff` (0-100) with a one-line reason; `score = (hook + payoff) / 2`, ranked descending with the earlier start breaking ties, and the top 5 are written to `shorts_candidates.json` (validated against `schemas/shorts_candidates.schema.json` before the atomic write). `shorts_generator.py --candidates` cuts them (each candidate becomes `short<rank>`, no `x_offset`, so the existing auto reframe runs) and `export_filmcraft.py --shorts-candidates` marks the top 3 with Mango.

**Why not A:** free spans can start mid-sentence or cross a chapter boundary, and the generator needs clean cut points. **Why not C:** hook and payoff are content judgements a rule cannot make; the model only ranks spans the rules already made safe.

**Consequence:** `shorts_candidates.json` is the ranked Shorts list; the generator's own `segments:` pick stays the default and the file is opt-in. The candidate times are the timeline seconds of the edit they were ranked on, so the generator must run against a video with that same timeline. `schemas/` and `scripts/common/` are unchanged.
