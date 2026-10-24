# Tools

Every tool, grouped. The [README](../../README.md) is the front door; this is the
reference. Each tool is a standalone script in `scripts/` with `--help`, `--json`
and re-run safety; shared code lives in `scripts/common/`.

## The yt entry point

`scripts/yt.py` (launched by `bin/yt`) runs any tool from any folder:

```bash
yt doctor --source <folder>
yt recap --source <folder> --brief <brief>
```

`yt <tool> [args...]` resolves the repository root (`YT_PIPELINE_HOME` first,
else walking up from the script to the folder holding `scripts/` and
`requirements.txt`) and the Python (`YT_PIPELINE_VENV`, else
`<root>/.venv/bin/python`, else the plugin's own virtual environment under
`~/.claude/plugins/data/`, else `python3`), then runs `scripts/<tool>.py` with
the remaining arguments and propagates its exit code unchanged. `yt --help`
lists the tools; `yt --json` and `yt --help --json` print the same JSON (the
resolved root, Python and tool list). `bin/yt` is a thin launcher that finds a
Python and runs `scripts/yt.py`.

---

## The finished-clip pipeline

### Workflow

```
1. Export the final video from the editor (.mp4) — the edit is the source of truth
   (a FilmCraft project, an edit.json, or just the media file)
2. Transcribe once when no word JSON exists yet — one Whisper pass per source:
   .venv/bin/python scripts/transcribe.py Clip.mp4 --out-dir words/
   (raw .srt + _words.json; --backend mock for an offline run; the model defaults
    to turbo on a CUDA GPU, else medium on CPU for a non-English language (including
    auto or no language), small only for an explicit en, overridable with --model,
    YT_PIPELINE_WHISPER_MODEL or transcribe.model)
3. Run the publish chain (skill: yt-publish):
   .venv/bin/python scripts/publish.py --project out/ --context context.md \
       --fcproj project.fcproj --sequence Main --words-dir words/ --json
   (also --from-edit edit.json or --media Clip.mp4; steps: subtitles -> corrections
    -> human-review stop -> EN translation -> thumbnail -> metadata draft,
    publish_state.json tracks them and re-runs skip finished steps)
4. [Manual SRT review at the stop — fix remaining errors in the file]
5. Continue past the review stop: repeat step 3 with --resume
6. Build the manual-upload kit for YouTube Studio:
   .venv/bin/python scripts/publish.py ... --resume --kit --final-video final.mp4 --kit-loudness
   (publish-kit/: video, RO/EN SRTs, thumbnail, studio.md copy-ready blocks + checklist)
7. For Shorts from the finished video: edit scripts/shorts_config.yaml with the
   segments decided in the project's own planning doc, then
   .venv/bin/python scripts/shorts_generator.py --video Clip.mp4 (skill: yt-shorts)
8. [Upload main video → add youtube_url to scripts/shorts_config.yaml]
```

Note: Whisper accepts `.mp4` directly — no separate audio export needed.

Each chain step is also a standalone script (`transcribe.py`, `correct_srt.py`, `translate_srt.py`, `thumbnail.py`, `metadata.py`) when only one of them is needed. `analyze_srt.py` metadata generation is deprecated since 2026-09-06 (a generic transcript-only model can't see the narrative decisions already made — angle, what got cut, pre-selected Shorts moments) and is not part of the chain; the script itself stays (client now points at OpenRouter, see below). `shorts_generator.py` (video cropping + karaoke burn-in) is unaffected — it does no text generation.

Outputs: the publish chain writes under `--project`; `translate_srt.py` and `shorts_generator.py` save next to the video file.

### Scripts

All scripts live in `scripts/` (see paths above for exact commands).

| Script | Input | Output |
|:---|:---|:---|
| `correct_srt.py` | `raw.srt` | `raw_corectat.srt` (`--capitalised` also fixes the capitalised variant; a `*wrong|correct` line does it per line) |
| `translate_srt.py` | `_RO.srt` + `video.mp4` | `_EN.srt` next to video |
| `analyze_srt.py` | `_RO.srt` + `video.mp4` | `video_metadata.txt` + `shorts_candidates.txt` |
| `analyze_srt.py --shorts-config` | config yaml | `{name}_metadata.txt` in `shorts/` |
| `shorts_generator.py` | `video.mp4` + config | `Short{N}-{name}.mp4` + karaoke ASS |
| `reframe.py` | `video.mp4` | crop path JSON (time -> x, y) for a moving reframe |
| `thumbnail.py` | portrait photo + title | `thumbnail.jpg` (1280x720) + optional `thumbnail.psd` |
| `kdenlive_from_fragments.py` | `fragments.yaml` | `.kdenlive` rough-cut project |
| `srt_from_fragments.py` | `fragments.yaml` + raw SRTs | retimed `.srt` for the rough cut |
| `rough_cut.py` | `sessions.yaml` (camera + optional screen + words JSON) | `edit.json` rough cut (silence, fillers, retakes cut) |
| `notify.py` | message | POST to the project's ntfy topic |
| `inbox.py` | watcher | inbox folder → project per day → `recap.py` |
| `serve.py` | server | preview of the workspaces on the tailnet plus the shotlog page's expectations buttons (reachable by URL only), the Mode B Cuts-tab cut review, the Re-edit box's "Ask the director" reply, the read-only Costs page, the feedback/requests form, per-project delete and per-version delete, per-instance feature switches, the upload consent checkbox, the tab bar Recap / Files / Cuts / Shorts (the explain and shotlog pages stay reachable by URL only), the poster cards and the shot-strip thumbnails |
| `thumbs.py` | workspace with `edit.json` + recap | `thumbs/poster.jpg` + `thumbs/shot-N.jpg` (320 px); skill `yt-thumbs` |
| `costs.py` | ledger DB + subcommand | SQLite ledger / totals JSON |
| `edit_drop.py` | workspace with `versions/` | new version with shots removed or a shot made longer/shorter (no model call) |
| `recap_meta.py` | `edit.json` | `recap_meta.json` (title, description, hashtags) |

`recap_meta.py` also hands the channel's `channel.style` parts (`title_style`, `description_order`, `pinned_comment_voice`) to the model in a `## Style` section, one line per part; a missing part is left out.

Every script prints one JSON summary with `--json` (`wall_time_s` and `llm_cost_usd` included, human lines on stderr) and never blocks on a prompt without a TTY: interactive questions have flag alternatives (`analyze_srt.py --select`, `shorts_generator.py --video`/`--audio`/`--srt`) and a missing flag without a terminal is a usage error.

**Exit codes** (every tool, constants in `scripts/common/exit_codes.py`): `0` success, `1` finished but something needs a human look (check-type tools, suspect results), `2` usage/input/config error and nothing ran, `3` runtime failure after work started (external program, model call, disk). `scripts/mode_b.py` is the one exception and keeps its own documented `0`/`1`/`2`/`3` meanings (all clear / suspect / incomplete / error).

**Structured errors** (`--json`): when a `--json` run fails, every tool prints one `{"ok": false, "error": "...", "exit_code": N}` object on stdout (the human `[error]` line stays on stderr), through `scripts/common/errors.py` `report_error`, so an agent reads the same shape everywhere.

---

## AI Director foundation

### Foundation (AI Director, phase D0)

Shared building blocks for the AI Director track (see `docs/VISION.md`). Independent of the finished-clip pipeline above.

- `scripts/common/` — shared code no tool may duplicate: `notify.py` (ntfy), `ffmpeg_tools.py` (encoder selection), `schema.py` (JSON-Schema validation), `config.py` (AI Director config loader), `summary.py` (`--json` wall time and LLM cost fields), `__init__.py`.
- `config.yaml` — project-wide AI Director config: OpenRouter model id per role, per-job `llm_budget_usd`, `llm_backend`, optional `spend_ledger`. Copy from the committed `config.yaml.example`; `config.yaml` is git-ignored and holds no secrets (API keys stay in `OPENROUTER_API_KEY`).
- `scripts/common/llm.py` — OpenRouter client with `llm_budget_usd` spend tracking against a per-project ledger file (`<workspace>/llm_spend.json`), role-to-model resolution and a mock backend for offline tests and dry runs. An OpenRouter response without `usage.cost` raises `MissingUsageCost` instead of counting as free.
- `schemas/` — JSON Schemas for the artifacts that pass between stages. `fragments.schema.json` describes the edit decision list read by `kdenlive_from_fragments.py` and `srt_from_fragments.py`; `ingest.schema.json`, `motion.schema.json` and `quality.schema.json` describe the D1a analysis outputs; `reframe.schema.json` describes the crop path written by `reframe.py`.
- `tests/` — pytest suite with tiny offline fixtures. Runs on a CPU-only machine (no GPU, no torch/whisperx).
- `scripts/notify.py` — post a short message to the project ntfy server (URL from `NTFY_URL` or the config key `ntfy_url` — no built-in default).

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/notify.py "hello" --dry-run
```

`shorts_generator.py` and the two rough-cut generators no longer assume a GPU: they pick `h264_nvenc` with an NVIDIA GPU, `h264_videotoolbox` on macOS, and fall back to `libx264` otherwise, and validate their `fragments.yaml` input against the schema before touching any media.

---

## Footage analysis

### Footage analysis (AI Director, phase D1a)

Three standalone CPU-only tools that label raw footage before any edit decision.
They only label — nothing is deleted or trimmed. Sources are read-only; every
output goes into the workspace. A fourth tool, `eval_labels.py`, scores those
outputs against a ground-truth YAML instead of labelling footage.
Skills: `yt-ingest`, `yt-motion`, `yt-quality`, `yt-eval`.

```bash
.venv/bin/python scripts/ingest.py  /path/to/footage --workspace /path/to/ws
.venv/bin/python scripts/motion.py  /path/to/footage --workspace /path/to/ws
.venv/bin/python scripts/quality.py /path/to/footage --workspace /path/to/ws
.venv/bin/python scripts/eval_labels.py --ground-truth eval/ground_truth.yaml --workspace /path/to/ws
```

| Script | Input | Output |
|:---|:---|:---|
| `ingest.py` | folder of clips | `ws/ingest.json` — duration, fps, resolution, streams, creation time, gyro present |
| `motion.py` | one clip or a folder | `ws/motion/<stem>.json` — trajectory, motion phases, CameraBench moves with steady range + handles |
| `quality.py` | one clip or a folder | `ws/quality/<stem>.json` — per segment: role (move, hold, transition in/out, setup, teardown, accidental) with confidence + measured defects (blur, exposure, shake, obstruction, horizon) |
| `eval_labels.py` | ground-truth YAML + workspace | per-clip and total move/role/horizon/event scores (table, or `--json`/`--out` report validated against `eval.schema.json`) |

All three analysis tools accept `--json`, re-run safely (existing outputs are
skipped, `--force` re-analyses) and validate their output against `schemas/`.
`quality.py` prefers a matching `ws/motion/<stem>.json` and falls back to coarse
frame-diff segmentation without it. `eval_labels.py` reads the workspace only and
writes nothing unless `--out` is given.

---

## The director and the edit

### Director (AI Director, phase S1)

`director.py` turns the workspace `motion/` and `quality/` labels into an
`edit.json` edit list — rule-based and deterministic, no model. It picks cut
windows, scores them from role, confidence, steadiness and defects, applies a
pacing and writes the edit list validated against `schemas/edit.schema.json`.
Skill: `yt-director`.

```bash
.venv/bin/python scripts/director.py --workspace /path/to/ws \
  --brief "about 90 seconds, calm start, more energy towards the end" \
  --target-duration-s 90 --order chronological --pacing calm-to-energy --json
```

| Script | Input | Output |
|:---|:---|:---|
| `director.py` | `ws/ingest.json` + `ws/motion/*.json` + `ws/quality/*.json` | `ws/edit.json` — video slots (clip, in/out, score, reason) + audio/captions/graphics plan |

The LLM director keeps its last reviewer verdict in `edit.json` `meta.director.review`, and `--interpret-feedback TEXT` says how the director reads a feedback request (one cheap call, no edit written) with a cost/time estimate. The three role prompts (screenwriter, editor, reviewer) share the standard playbook in `prompts/playbook.md` — the editing techniques the director knows, pointing to `docs/RULES.md` for the grammar. The director-facing capability list (`prompts/tools.md`) goes only to the chat prompt (`--interpret-feedback`).

The agent mode splits the loop in two so a host coding agent can direct the edit without an OpenRouter key. `--prepare` writes `<workspace>/director/inputs.md` — the brief, the brief settings, the editing rules, the eligible shots and their shot facts, plus the two output shapes — and makes no model call. The host agent writes `<workspace>/shot_plan.json` and `<workspace>/edit.json` (the editor answer shape), then `--apply` validates both against the schemas and the editing grammar, repairs mechanical faults with the same machinery the loop uses and writes the final `edit.json` with `meta.director.by = "agent"`. `--prepare` and `--apply` are mutually exclusive with each other, `--interpret-feedback` and `--dry-run`; the OpenRouter three-role loop stays the default.

```bash
.venv/bin/python scripts/llm_director.py --workspace /path/to/ws \
  --brief "about 90 seconds, chronological" --prepare --json
.venv/bin/python scripts/llm_director.py --workspace /path/to/ws \
  --brief "about 90 seconds, chronological" --apply --json
```

---

## Render and output

### Render (edit.json → 720p MP4)

The S1 output adapter turns an `edit.json` edit list into a preview-quality 720p MP4. It cuts each video slot from the proxy clip named in `sources[]`, scales and pads it to the requested frame, joins the slots in timeline order with crossfades or hard cuts and keeps each slot's own ambient audio. Captions, graphics, music and SFX are warned about and ignored. Skill: `yt-render`.

```bash
.venv/bin/python scripts/render.py --edit /path/to/ws/edit.json --out /path/to/ws/recap_720p.mp4 --json
```

| Script | Input | Output |
|:---|:---|:---|
| `render.py` | `edit.json` + the clips in `sources[]` | `--out` MP4 (1280x720, libx264 + AAC) |

`--edit` is validated against `schemas/edit.schema.json`; `--media-dir` resolves clip names not in `sources[]`; `--width`/`--height` set the frame; `--xfade-s` is the fallback crossfade length. Re-running skips when `--out` is newer than `--edit` unless `--force` is given. The MP4 is written atomically and never touches the source clips.

### Loudness (loudness.py)

`loudness.py` applies the audio loudness recipe as one command, with two backends. Skill: `yt-filmcraft`.

```bash
.venv/bin/python scripts/loudness.py --video clip.mp4 --json
.venv/bin/python scripts/loudness.py --fcproj project.fcproj --sequence Main --json
.venv/bin/python scripts/loudness.py --fcproj project.fcproj --dry-run
```

- `--video` runs an ffmpeg two-pass `loudnorm` to the YouTube targets (`I=-14 LUFS`, `TP=-1 dBTP`, `LRA 11` by default, set with `--target-i`/`--target-tp`/`--target-lra`), copies the video stream and re-encodes the audio as AAC 320k. `--out` defaults to `<stem>_loud.mp4` (or the input's own suffix); writing onto the input is refused.
- `--fcproj` applies the Essential Sound recipe (Dialogue type, Auto-Match loudness, Clarity Dynamics medium) to the voice clips of one audio track through the headless `filmcraft-cli` (`$FILMCRAFT_CLI` or PATH). The input project is never modified unless `--in-place` is given; without it the tool works on a copy (`<stem>_loudness.fcproj`). `--dry-run` prints the commands without running them.
- Both backends report `wall_time_s` and `llm_cost_usd` (`0.0`); `--json` adds the measured and target loudness values.

### Colour (colour.py)

`colour.py` applies a basic colour recipe to the camera clips of a FilmCraft project as one command. Skill: `yt-filmcraft`.

```bash
.venv/bin/python scripts/colour.py --fcproj project.fcproj --sequence Main --json
.venv/bin/python scripts/colour.py --fcproj project.fcproj --dry-run
.venv/bin/python scripts/colour.py --fcproj project.fcproj --in-place
```

- It measures each recording session's average white balance, exposure and light contrast on a few frames of the camera clips and applies one Lumetri Basic Correction per session (temperature, tint, exposure, contrast) to every camera clip of that session, through the headless `filmcraft-cli` (`$FILMCRAFT_CLI` or PATH). The input project is never modified unless `--in-place` is given; without it the tool works on a copy (`<stem>_colour.fcproj`). `--dry-run` prints the JSONL commands without running them.
- Clips are grouped into sessions by capture time (`--session-gap-min`, default 180); `--track` (default `Camera 1`) picks the video track, `--frames-per-clip` (default 3) the frames sampled per clip, and `--target-luma`/`--target-contrast` (default 0.5/0.18) the targets.
- It reports `wall_time_s` and `llm_cost_usd` (`0.0`); `--json` adds the per-session measured values and corrections.

---

## Media tools

### Thumbnail (brand kit)

`thumbnail.py` turns a portrait photo + a title into a branded 1280x720 YouTube thumbnail (the approved brand template look, driven by `brand.json`): cut-out portrait with an outer glow, generated or photo background, title/keyword/kicker in the brand fonts and the channel tag. Skill: `yt-thumbnail`.

```bash
.venv/bin/python scripts/thumbnail.py --photo photo.jpg --title "AI-UL MEU" --keyword "RULEAZĂ ACASĂ" \
    --kicker "// homelab · automatizări" --theme tech --out thumb/
.venv/bin/python scripts/thumbnail.py --photo photo.jpg --title "AI-UL MEU" --keyword "RULEAZĂ ACASĂ" \
    --theme creator --background frame.jpg --out thumb/ --psd
```

- `--theme tech` generates the dark background; `--theme creator` requires `--background FRAME.jpg` (cover-scaled, darkened). The look (colors, fonts, layout, glow) comes from `brand.json` (validated against `schemas/brand.schema.json`); fonts ship in `assets/fonts/` (SIL OFL).
- `--concepts FILE` takes the title, keyword, kicker and theme from a `thumbnail_concepts.json` (validated against `schemas/thumbnail_concepts.schema.json`) instead of the flags; `--pick N` (1-based, default 1) chooses the concept. With `--concepts`, `--title`, `--keyword`, `--kicker` and `--theme` must not be given; a `--pick` outside `1..len(concepts)` is a usage error.
- `--psd` also writes `thumbnail.psd` with the layers `background`, `portrait` (glow baked in), `title`, `keyword`, `kicker`, `tag` for manual retouching.
- The cut-out uses rembg (`birefnet-portrait`, downloaded to `~/.rembg/models/` on first run); `--skip-cutout` uses the photo's own alpha instead.

### Background removal (matte.py)

`matte.py` cuts a talking-head clip out of its background with RobustVideoMatting (RVM, loaded through `torch.hub` — never vendored) and writes a ProRes 4444 `.mov` with alpha, ready to place over screen capture. It runs on CUDA when present, else on CPU, and processes only the `--start`/`--end` range. Skill: `yt-matte`.

```bash
.venv/bin/python scripts/matte.py clip.mp4 --start 10 --end 40 --ratio 0.25 --out clip_matte.mov --json
```

| Script | Input | Output |
|:---|:---|:---|
| `matte.py` | `clip.mp4` (+ `--start`/`--end`) | `clip_matte.mov` — ProRes 4444 with alpha, video only |

`--ratio` is RVM's `downsample_ratio` (default 0.6, steadier edges; 0.25 is faster). The output keeps the input resolution and fps and has no audio (the editor keeps the original's). Re-running overwrites the output; an `--out` that would overwrite the input is refused. The first run downloads the RVM code and weights into `~/.cache/torch/hub/`.

### Picture-in-picture finishing (finish_pip.py)

`finish_pip.py` finishes a Mode B clip in the open FilmCraft project: from a sequence inspect JSON it finds the screen spans (only real screen video items — a still or a graphic on the screen track never makes a span), mattes the camera source ranges covering them and places the mattes as a bottom-right picture-in-picture, snapped to the span edges. Skill: `yt-finish-pip`.

```bash
.venv/bin/python scripts/finish_pip.py --sequence sequence.json \
  --camera-dir <clip-folder>/camera --matte-dir <clip-folder>/matte \
  --out-dir <clip-folder>/finish --bridge 127.0.0.1:7714 --dry-run
```

| Script | Input | Output |
|:---|:---|:---|
| `finish_pip.py` | `sequence.json` + `camera/` | `finish_pip.place.jsonl`, `finish_pip.animate.jsonl` |

- The camera ranges are padded 0.5 s and merged under 2 s, one `matte.py` call each; the mattes go to `--matte-dir`, never into the camera folder (originals are read-only). A still or a graphic on the camera track is not a camera piece.
- The PiP is 45 % of the frame (36 % over full-screen explainer scenes) with an eased entry (quick ease-out with slight overshoot) and a short anticipation exit.
- `--dry-run` prints the plan; `--bridge` (or `--project` for a headless file) runs both phases through `filmcraft-cli run steps.jsonl`. `--json` reports `ranges[]` with `matted`/`placed`/`keyframed`, `edge_checks[]`, `nothing_to_do`, `wall_time_s` and `llm_cost_usd`.
- Re-run safe: a re-run on a finished project prints `nothing to do`; when the placements exist but the animate phase is missing or failed it rewrites `finish_pip.animate.jsonl` instead. Exit codes: `0` ok, `2` usage error, `1` unexpected.

### Graphics overlays (graphics.py)

`graphics.py` renders branded graphic overlays (lower third, counter, callout) as ProRes 4444 `.mov` files with alpha at timeline resolution, ready to drop over the edit in the editor. The engine is HyperFrames (Apache-2.0): a composition HTML per template in `assets/graphics/` is filled with the brand values from `brand.json` and the entry props, then rendered by the pinned `hyperframes@0.8.181` CLI. Skill: `yt-graphics`.

`graphics_plan.py` reads a timeline words JSON (flat or Whisper shape) and, optionally, a `markers.json` (only `Mango` markers become candidates), and has the `graphics` model propose where overlays belong into `graphics_plan.json` for human approval. Every entry starts `approved: false`; a human flips the ones to render and `graphics.py --plan` renders only those.

```bash
.venv/bin/python scripts/graphics_plan.py words.json --markers markers.json \
    --context context.md --max-entries 3 --json
.venv/bin/python scripts/graphics.py --template lower_third \
    --props '{"name":"<name>","role":"creator"}' --out /tmp/lt.mov --json
.venv/bin/python scripts/graphics.py --template counter \
    --props '{"number":"42","label":"clips"}' --out /tmp/counter.mov --duration 3
.venv/bin/python scripts/graphics.py --template callout \
    --props '{"text":"echo hello","style":"terminal"}' --out /tmp/callout.mov
.venv/bin/python scripts/graphics.py --plan graphics_plan.json --out-dir overlays/ --json
```

- `graphics_plan.py` accepts both words shapes (`words_shape` `flat`/`whisper`), caps the entries with `--max-entries` (default 4, `>= 0`; zero is valid) and validates the plan against `schemas/graphics_plan.schema.json`; an invalid model answer is retried once, then exits 2. `--json` reports `output`, `entries`, `approved`, `markers_used`, `words_shape`, `model`, `wall_time_s`, `llm_cost_usd`.
- `--template` is `lower_third` (`name`, `role`), `counter` (`number`, `label`) or `callout` (`text`, optional `style` `text`/`terminal`). `--width`/`--height`/`--fps`/`--duration` default to `1920`/`1080`/`30`/`3.0` (`--duration` `> 0` and `<= 15`).
- `--plan` renders only the `approved: true` entries of a `graphics_plan.json` (validated against `schemas/graphics_plan.schema.json`), one `<id>.mov` per entry in `--out-dir`.
- `--backend hyperframes` (default) needs Node.js 22+ and npx; `--backend mock` is deterministic and offline (composed `index.html` + an ffmpeg transparent MOV). Every output is checked with ffprobe for an alpha pixel format (`yuva444p10le`/`yuva444p12le`).
- The look comes from `brand.json` via `--brand`; a missing font file is a hard error naming the path and the brand key. The HyperFrames agent skills are not committed (the published set is ~19 MB); install them with `GIT_CLONE_PROTECTION_ACTIVE=0 npx skills add heygen-com/hyperframes --all` (pinned commit `908f2d095bba4e2b97363c5533e2702f1e92505d`).

---

## Rough cut and Mode B

### Rough-cut assembly (raw footage → Kdenlive project)

For narrative clips with a lot of raw, uncut footage (multi-day trips, vlogs), scrubbing through every source file to find the moments already selected in the written structure doc wastes time. These two scripts skip that: given a list of (source file, in, out) fragments already identified (from raw transcripts, matched to quotes), they generate a ready-to-open Kdenlive project with those fragments placed on the timeline, plus a subtitle file retimed to match.

```
1. Identify fragments (source video + in/out timecode + label), e.g. by grepping raw
   Whisper SRTs for the quotes already chosen in the video structure doc
2. Write them to fragments.yaml (see format below)
3. python3 scripts/kdenlive_from_fragments.py fragments.yaml
   → generates the .kdenlive project (validated with `melt` automatically)
4. python3 scripts/srt_from_fragments.py fragments.yaml transcripts_raw/ output.srt
   → generates a subtitle file retimed to the new (cut) timeline
5. Open the .kdenlive project, Project → Subtitles → Import Subtitle File → output.srt
6. Continue editing normally (trim, add music/transitions) from this starting point
```

**fragments.yaml format:**

```yaml
source_dir: /path/to/raw/clips
output: /path/to/Project.kdenlive
fragments:
  - file: DJI_..._D.MP4
    in: "00:11:16.320"
    out: "00:11:50.680"
    label: beat5-descriptive-label
  - file: ...
    in: "..."
    out: "..."
    label: ...
```

Fragments are placed on the timeline in the order they appear in the YAML. Each unique source file gets one producer (`<chain>`); fragments from the same file reuse it with different in/out.

**Why this isn't part of the main correct→translate→analyze→shorts pipeline:** it operates on *raw, uncut* footage before an editing decision has been made, not on the *final exported* video. It's a pre-editing step, used once per clip to skip manual scrubbing — not a repeated per-clip pipeline stage like the scripts above.

See `docs/ARCHITECTURE.md` for how the generated Kdenlive/MLT XML is structured, and `docs/DECISIONS.md` for why several non-obvious choices were made (video/audio track split, explicit stream indices, subtitle approach).

### Timeline markers (fcproj_markers.py)

`fcproj_markers.py` reads the sequence-level markers of a FilmCraft `.fcproj` project (read-only) and writes two files next to it: `markers.json` in the shape `graphics_plan.py --markers` already consumes, and `todo.md` holding only the Rose markers — the redo list outside the editor. FilmCraft need not be installed. Skill: `yt-filmcraft`.

```bash
.venv/bin/python scripts/fcproj_markers.py project.fcproj --json
.venv/bin/python scripts/fcproj_markers.py project.fcproj --sequence Main --out markers.json --todo todo.md
```

| Script | Input | Output |
|:---|:---|:---|
| `fcproj_markers.py` | `.fcproj` project | `markers.json` (all markers) + `todo.md` (Rose markers) |

`--sequence` is required when the project has more than one sequence. Each marker carries `time_s` (ticks / 254016000000), `timecode` (`HH:MM:SS:FF` at the sequence frame rate), `name`, `comment`, `colour` and `kind`, sorted by time. Clip-level markers are counted and reported as `clip_markers_ignored`, never mapped. `--json` reports `output`, `todo`, `sequence`, `markers`, `rose`, `clip_markers_ignored`, `wall_time_s`, `llm_cost_usd`.

### FilmCraft export (export_filmcraft.py)

`export_filmcraft.py` turns an `edit.json` edit list into an editable FilmCraft `.fcproj` project. It writes the project JSON directly (route `json`) with the exact shapes a real FilmCraft save produces, so no FilmCraft is needed at export time. Media is referenced by path, never embedded. Skill: `yt-filmcraft`.

```bash
.venv/bin/python scripts/export_filmcraft.py edit.json --out project.fcproj --json
.venv/bin/python scripts/export_filmcraft.py edit.json --out project.fcproj \
  --graphics-plan graphics_plan.json --graphics-dir overlays/ --sequence Main
```

| Script | Input | Output |
|:---|:---|:---|
| `export_filmcraft.py` | `edit.json` (+ optional `graphics_plan.json` + overlays) | `.fcproj` project (atomic replace, re-run safe) |

Video slots go on `Camera 1`, screen slots on `Screen 1`, each video slot's audio on `Audio 1`, overlays on `Graphics 1` above the picture. One Green marker per used slot carries its reason; a slot flagged for a human check (`centred` or a speed ramp) gets a Rose "to redo" marker instead; each placed overlay gets a Mango marker. Missing media is a warning (the project opens with offline media), photo slots export as stills with a "Ken Burns not exported" warning and speed ramps export at real time with a warning. `filmcraft-cli` is only needed to open or inspect the result (`$FILMCRAFT_CLI` or PATH). `--json` reports the placed counts, marker counts, missing media and warnings.

Consecutive video slots sharing the same `id` are two angles of one cut window: they are placed at one shared timeline start, with one audio clip per pair and one marker per pair. This is how a Mode B rough cut (below) opens with the screen and the camera cut identically.

### Mode B rough cut (rough_cut.py)

`rough_cut.py` turns one or more talking-head + screen recording sessions into an `edit.json` rough cut: silence/pause gaps cut, standalone Romanian filler words dropped, restarted sentences (retakes) removed, every cut carrying a reason. The output opens through `export_filmcraft.py` with the screen on `Screen 1` and the camera on `Camera 1`, the same cuts on both, ready for a human edit. Skill: `yt-mode-b`.

```bash
.venv/bin/python scripts/mode_b.py FOLDER --json
.venv/bin/python scripts/rough_cut.py sessions.yaml --out edit.json --json
.venv/bin/python scripts/rough_cut.py sessions.yaml --out edit.json --config scripts/rough_cut_config_example.yaml
.venv/bin/python scripts/rough_cut.py sessions.yaml --out edit.json --llm --json
.venv/bin/python scripts/rough_cut.py sessions.yaml --out edit.json --review cut_review.yaml --json
```

| Script | Input | Output |
|:---|:---|:---|
| `mode_b.py` | a folder of OBS recordings (`camera ...` files + optional sibling screen files) | `mode_b/words/`, `mode_b/sessions.yaml`, `mode_b/edit.json`, `mode_b/project.fcproj` (one command, skip rule, `--force`) |
| `rough_cut.py` | `sessions.yaml` (camera + optional screen + Whisper words JSON) | `edit.json` rough cut (atomic replace, re-run safe; `--review cut_review.yaml` honours the phone cut review) |
| `eval_rough_cut.py` | `edit.json` + `sessions.yaml` + a reference `.fcproj` + `--words-dir` | precision/recall of the kept words vs a hand edit, the sessions/sections the reference did not use, the biggest disagreements, a `subs_from_edit.py` cross-check (table, or `--json`/`--out`); `--review cut_review.yaml` adds the phone cut review as ground truth |

`mode_b.py` is the one-command front door: it transcribes every session with no words JSON yet (`--backend mock` for offline runs), writes the manifest, runs `rough_cut.py` and exports the FilmCraft project, skipping any step whose output already exists (`--force` re-runs everything). `--config` and `--llm` are passed to `rough_cut.py`; outputs go under `<folder>/mode_b/` and the last human line is `project: <path>`.

`sessions.yaml` is validated against `schemas/sessions.schema.json`. The cut rules (`pause_gap_s`, `handle_s`, `retake_window_s`, `retake_similarity`, `retake_min_words`, `repeat_window_s`, `fillers`) come from `--config` or the built-in defaults (see `scripts/rough_cut_config_example.yaml`). Word times come only from the given word JSON files — the tool never transcribes. `--llm` (off by default) proposes extra cuts for human approval in `<out-dir>/rough_cut_proposals.json` and never changes the edit. `--json` reports `outputs`, `sessions`, `windows`, `slots`, `words_read`, `words_kept`, `cuts`, `warnings`, `wall_time_s`, `llm_cost_usd`.

`eval_rough_cut.py` scores a rough cut against a hand-made FilmCraft edit of the same material: it maps both sides' words with the same midpoint rule and reports `precision`/`recall`, the same numbers minus the sessions and long sections the reference did not use (`precision_used`/`recall_used`), the ten biggest disagreement runs with timecodes, and a `subs_from_edit.py` cross-check. It is a measuring tool with no pass/fail gate (exit `0` on success, `2` on a user error) and calls no model.

---

## Publish

### Publish kit (publish_kit.py)

`publish_kit.py` is the standalone kit tool. It reads the publish chain's existing outputs (`metadata.json`, `<name>_RO.srt`, `<name>_EN.srt`, `thumbnail.jpg` and the final video) and writes `<project>/publish-kit/` for a manual YouTube Studio upload: the RO/EN subtitles and the thumbnail copied, the final video **linked** (hard link, else symlink, else only its absolute path in `studio.md`) and `studio.md` with one copy-ready block per Studio field plus a checklist in Studio order. It never re-runs or overwrites the chain's outputs, so it can be re-run alone after changing only the thumbnail. Skill: `yt-publish`.

```bash
.venv/bin/python scripts/publish_kit.py --project out/ --final-video final.mp4 \
  [--name NAME] [--kit-loudness] [--kit-title TITLE] [--json]
```

`--kit-loudness` runs `loudness.py --video <final video> --json` as a subprocess first and links its `_loud` output; `--kit-title` picks the Studio title (default: the first `metadata.json` title). Before linking the video it prints one line naming the file and its size, and before the loudness pass one line saying it writes a large file (to stderr with `--json`). Checks that would fail in Studio fail loudly before anything is written (title 100 characters, description 5000, EN subtitles present, thumbnail ≤ 2 MB and 1280x720 when present, tags ≤ 500); a failure exits 2 with one `[error]` line per problem and leaves no `publish-kit/` behind. The thumbnail is optional: when `thumbnail.jpg` is missing the kit is built without it and the checklist says to make and upload one. There is no API upload — an unaudited Google project locks API uploads as private (see `docs/DECISIONS.md`).

`publish.py --kit` is orchestration only: it calls `publish_kit.py` as its last step and forwards `--project`, `--name`, `--final-video`, `--kit-loudness` and `--kit-title`; the child's JSON is the run's `kit` summary. The step is skipped when `publish-kit/studio.md` exists and `--force` re-runs it.

```bash
.venv/bin/python scripts/publish.py --project out/ --context ctx.md \
  --fcproj project.fcproj --sequence Main --words-dir words/ --resume \
  --kit --final-video final.mp4 --kit-loudness --json
```

`--final-video` defaults to the `--media` file and is required with `--fcproj`/`--from-edit`.

`--whisper-model` is passed to `transcribe.py` as `--model` in the `--media` step; without it `transcribe.py` resolves the default (turbo on a CUDA GPU, else medium on CPU for a non-English language (including auto or no language), small only for an explicit en).

The `verify` step compares the corrected `<name>_RO.srt` with the final video already known to the run (`--final-video`, or the `--media` file): the last cue's end time must be within 2 s of the video duration and the SRT must not be older than the video. A mismatch exits 2 naming both times (or saying the subtitles are older) and aborts the chain before translation, metadata and the kit; with no final video the step is skipped with reason `no final video`.

### Metadata draft (metadata.py)

`metadata.py` turns a corrected Romanian `.srt` plus a context note into a `metadata.md` publish draft: 3 titles, a description in Romanian and English with the channel's fixed CTA wording, chapters from the timeline, tags and a pinned-comment proposal. It writes `metadata.json` next to the draft with the validated fields. Skill: `yt-publish`.

```bash
.venv/bin/python scripts/metadata.py SRT --context CONTEXT.md [--title TITLE] \
  [--chapters chapters.json] [--previous-url URL] [--repo-url URL] \
  [--out-dir DIR] [--name NAME] [--prompt PATH] [--json]
```

- `--title` is the chosen title the human already picked; the model gets it in a `## Chosen title` section and uses it verbatim as the first title.
- `--chapters` takes a `chapters.json` (validated against `schemas/chapters.schema.json`) and uses its chapters instead of the model's.
- The channel's fixed wording comes from `channel.fixed` in the config, one string per language: `members_sentence` (with `{members}` filled from `channel.members`) and `closing` are inserted verbatim, the RO description getting the RO string and the EN description the EN one. A missing part, a missing language or an empty string leaves that part out — never replaced by built-in text. `channel.members` is required only when a `members_sentence` string contains the `{members}` placeholder; a channel that does not thank members in the description runs without it.
- The channel's own style instructions come from `channel.style` in the config, one non-empty string per part (`title_style`, `description_order`, `pinned_comment_voice`). The tool hands the present parts to the model in a `## Style` section, one line per part; a missing part is left out — never replaced by built-in text. `thumbnail_formula` stays in `channel.fixed`.
- A chapter may carry `title_en`; when at least one does, the draft adds a `## Chapters (EN)` section and `metadata.json` keeps `title_en` per chapter.

### Thumbnail concepts (thumbnail_concepts.py)

`thumbnail_concepts.py` writes 2-3 Romanian thumbnail concepts from the confirmed title, the context note and the corrected `.srt`: `thumbnail_concepts.json` (validated against `schemas/thumbnail_concepts.schema.json`) and a Romanian `thumbnail_concepts.md`. The chosen concept feeds `thumbnail.py --concepts`/`--pick`. Skill: `yt-publish`.

```bash
.venv/bin/python scripts/thumbnail_concepts.py SRT --context CONTEXT.md --title "AI-UL MEU" \
  [--out-dir DIR] [--name NAME] [--prompt PATH] [--json]
```

- The model role is `metadata` (same as `metadata.py`); the concepts start from the confirmed title and each phrases the promise its own way.
- The channel's thumbnail formula comes from `channel.fixed.thumbnail_formula` (RO, else EN) and is handed to the model in a `## Channel` section; with none set the section is omitted.
- A `clip_frame` timecode is normalized to `HH:MM:SS` and must be inside the transcript.

---

## Web app

### Costs ledger (costs.py)

`costs.py` keeps a small SQLite ledger of what the project costs and earns: model jobs, build-agent spend, fixed monthly costs, one-off investments and donations. It calls no model, so `llm_cost_usd` is always `0.0`. Skill: `yt-costs`.

The ledger is a SQLite file **outside the repository**. Its path comes from `--ledger`, else the `YT_PIPELINE_COSTS_DB` environment variable, else `costs.path` in the config; with none set the tool stops with a usage error naming those three ways. Docs and examples write `<costs.db>`.

```bash
.venv/bin/python scripts/costs.py --ledger <costs.db> add --kind donation --amount 10 --date 2026-10-01
.venv/bin/python scripts/costs.py --ledger <costs.db> import-jobs --workspaces <workspaces>
.venv/bin/python scripts/costs.py --ledger <costs.db> import-dev --from <run-spend.json>
.venv/bin/python scripts/costs.py --ledger <costs.db> import-opencode --db <opencode.db> --match <folder-part>
.venv/bin/python scripts/costs.py --ledger <costs.db> import-donations --from <donations.csv>
.venv/bin/python scripts/costs.py --ledger <costs.db> totals --month 2026-10 --json
.venv/bin/python scripts/costs.py --ledger <costs.db> list --kind model_job
```

- Five kinds: `model_job`, `dev_spend`, `fixed_monthly`, `investment`, `donation`.
- `import-jobs` reads each workspace's `llm_spend.json`; `import-dev` reads the build agents' run-spend JSON; `import-opencode` reads opencode's session database (`--db`, repeatable `--match` for this project's folders). All are idempotent by `source` (the project name for jobs, the date for dev spend), so re-running never duplicates a row.
- `import-donations --from <donations.csv>` imports donations from a CSV export with a header row (required `date`, `amount`; optional `note`, `id`), idempotent by `source` (`donation:<id>` or `donation:<date>:<amount>:<note>`); a bad date or amount is skipped with a warning and the run still succeeds.
- `totals` sums per kind, computes the recurring monthly cost, the fixed total over the months since the first fixed entry and the balance (donations minus model + dev + investments + fixed monthly × months). `costs.py totals --json` and the web page share `compute_totals` from `scripts/common/costs.py`.

### Costs page

The web app has a read-only **Costs** page (`GET /costs`, linked in the top bar). It shows, per project, the model cost from the workspace's `llm_spend.json` and the number of versions, then the ledger totals (monthly running cost, development total, donations and the balance, coloured green when positive and red when negative) and an optional donation link. The page reads the same totals object as `costs.py totals --json`, through the shared `scripts/common/costs.py`; it never writes the ledger and never handles payments.

The ledger path comes from `--ledger`, else `YT_PIPELINE_COSTS_DB`, else `costs.path`; the donation links from `--donation-url`, else `YT_PIPELINE_DONATION_URL`, else `costs.donation_links` (a list of `{label, url}`; `costs.donation_url` with one link stays accepted) in the config. With no ledger configured the page says the ledger is not set up. The per-workspace `feedback.jsonl` (about an edit) and the per-instance requests file (feature requests to the owner) are two separate stores.

The **Feedback and requests** form posts to `/costs/request` and appends one JSON line (`date`, `type`, `text`, `contact`, `backed`) to the requests file. Its path comes from `--requests`, else `YT_PIPELINE_REQUESTS`, else `requests.path`; with none set the form is hidden and the page says requests are not set up. `--check` warns about a missing ledger or requests file without failing.

### Web app parts and consent

`serve.py` can be handed to a friend with only some parts on. `--features` (comma-separated), else `YT_PIPELINE_FEATURES`, else `web.features` in the config lists the parts shown on this instance — `upload`, `recap`, `why`, `shots`, `shorts`, `costs`, `requests`; with none set every part is on. The server refuses the routes of a part that is off with a 404, not just hiding the links. `--consent-text`, else `YT_PIPELINE_CONSENT_TEXT`, else `web.consent_text` is optional: when set, the upload page shows it with a required "I agree" checkbox and an upload without it is refused. The upload and re-edit brief fields carry hint chips and a multi-line example placeholder that is never sent. A project page has a bin icon that deletes the workspace and its inbox source, refused while a run is going.

### Logo and brand

The app logo is one of three flat SVG marks in `assets/brand/` (`option-1` clapperboard, `option-2` viewfinder, `option-3` reticle), own work under the repository licence. `GET /brand` shows all three side by side for the owner to choose. `brand.logo` in the config (or `--brand-logo`, else `YT_PIPELINE_BRAND_LOGO`; default `option-1`) picks the one this instance shows left of the app name in the top bar and uses as the SVG favicon (`GET /favicon.svg`). Each mark is a 48×48 square drawn in two theme colours (`--sea`, `--stone`) as CSS variables with hex fallbacks, so it adapts to the light and dark theme when inlined and still renders standalone.

---

## Repo checks

- `check_public.py` — the pre-commit hook and CI check that fails on footage, frames, transcripts, keys or private IP addresses in tracked files.
- `check_stable.py` — the CI check that fails when a diff touches a file in the `stable-files` block of `docs/RELEASES.md` without a `Stable-change: approved` trailer.

---

## Setup

### Installation

```bash
git clone https://github.com/Dorusto/yt-pipeline.git
cd yt-pipeline
uv venv
uv pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu124
```

Requires: `ffmpeg` (the encoder is automatic — `h264_nvenc` with an NVIDIA GPU, `h264_videotoolbox` on macOS, else `libx264`), `openai-whisper` via pipx, `OPENROUTER_API_KEY` in environment (DeepSeek-direct retired 2026-09-02, no more top-ups — see `docs/DECISIONS.md`). Run the test suite with `.venv/bin/python -m pytest -q` (deps: `requirements-dev.txt`).

### shorts_config.yaml

```yaml
video: "MyClip.mp4"
srt:   "MyClip_RO.srt"
youtube_url: "https://youtu.be/..."   # optional — fills short descriptions

segments:
  - name: "Hook"
    start: "00:00:00"
    end:   "00:00:54"
    # no x_offset → moving crop that follows the subject (reframe.py)

  - name: "Delegarea"
    start: "00:05:06"
    end:   "00:05:46"
    x_offset: 800   # manual crop override (pixels from left, 0–1312)
```

Copy from `shorts_config_example.yaml`. File is git-ignored.

### Tech stack

| Component | Tool |
|:---|:---|
| Transcription | `openai-whisper turbo` (pipx) |
| Forced alignment | `whisperx` (wav2vec2 Romanian) |
| Face detection | OpenCV YuNet |
| ASS subtitles | `pysubs2` |
| Video render | `ffmpeg h264_nvenc` (NVIDIA GPU) / `h264_videotoolbox` (macOS) / `libx264` (CPU fallback, automatic) |
| AI (translation; metadata gen deprecated) | DeepSeek via OpenRouter |
| Notifications | ntfy (`scripts/notify.py`) |
| Schemas / tests | JSON Schema (`jsonschema`) / `pytest` |
| Package management | `uv` |
