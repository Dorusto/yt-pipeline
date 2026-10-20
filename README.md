# yt-pipeline

CLI pipeline for processing YouTube clips: transcription → correction → translation → metadata → 9:16 shorts with karaoke subtitles.

**Use it from an AI agent** (Claude Code or opencode): see [docs/AGENT_SETUP.md](docs/AGENT_SETUP.md).

## Quickstart — folder + brief to recap

```bash
git clone https://github.com/Dorusto/yt-pipeline && cd yt-pipeline
uv venv && uv pip install -r requirements.txt
export OPENROUTER_API_KEY=sk-or-v1-...            # key from https://openrouter.ai/keys
.venv/bin/python scripts/doctor.py --source ~/media/trip/raw
.venv/bin/python scripts/recap.py --source ~/media/trip/raw --workspace ~/media/trip/workspace \
    --brief "90 s, chronological, calm start, ambient sound only"
# -> ~/media/trip/workspace/recap_720p.mp4 (plus edit.json and shotlog.html)
# full quality from the originals + a Kdenlive project:
.venv/bin/python scripts/recap.py --source ~/media/trip/raw --workspace ~/media/trip/workspace --final --brief "90 s, chronological, calm start, ambient sound only"
# re-running skips finished stations; a new brief only redoes the edit
```

Install per OS — `ffmpeg` + `ffprobe` first: `sudo apt install ffmpeg` (Linux), `brew install ffmpeg` (macOS), `winget install ffmpeg` (Windows, then reopen the shell). Python 3.11+ and `uv` everywhere (`pip install uv`). On Windows run the commands from PowerShell with `.venv\Scripts\python.exe` and `$env:OPENROUTER_API_KEY=...`. The video encoder is picked per machine: `h264_nvenc` with an NVIDIA GPU, `h264_videotoolbox` on macOS, else `libx264` (everywhere, CPU).

For the details see [docs/guides/user-guide.md](docs/guides/user-guide.md); agents should load the `yt-recap` skill.

Phone-only flow (Syncthing → inbox.py → serve.py → ntfy): [docs/guides/phone-flow.md](docs/guides/phone-flow.md).

---

## Workflow

```
1. Export final video from editor (.mp4)
2. whisper Clip.mp4 --language Romanian --model turbo --output_format srt \
       --word_timestamps True --max_line_width 42 --max_line_count 2 \
       --output_dir /path/to/export/
3. python scripts/correct_srt.py raw.srt Clip_RO.srt
4. [Manual SRT review — fix remaining errors directly in the file]
5. python scripts/translate_srt.py Clip_RO.srt Clip_EN.srt
6. [DEPRECATED 2026-09-06, see docs/DECISIONS.md] python scripts/analyze_srt.py Clip_RO.srt Clip.mp4
   → no longer used for the main video's metadata — a generic transcript-only model can't
     see narrative decisions already made (angle, what got cut, pre-selected Shorts moments).
     Main video metadata is now written directly by Claude from the project's own structure doc.
7. Edit scripts/shorts_config.yaml with segments already decided in the project's own planning
   doc (not from a generated candidates file)
8. .venv/bin/python scripts/shorts_generator.py --video Clip.mp4
9. [Upload main video → add youtube_url to scripts/shorts_config.yaml]
10. [Also deprecated for the same reason as step 6] python scripts/analyze_srt.py Clip_RO.srt Clip.mp4
    --shorts-config scripts/shorts_config.yaml — per-short title/description are now written
    manually (by Claude, from the pre-decided segment), not generated here
```

Note: Whisper accepts `.mp4` directly — no separate audio export needed.

`analyze_srt.py` itself is not removed (client now points at OpenRouter, see below) — only its use for metadata/Shorts-description generation is deprecated. `shorts_generator.py` (video cropping + karaoke burn-in) is unaffected — it does no text generation.

All outputs (translated SRT, cropped shorts) are saved next to the video file.

---

## Scripts

All scripts live in `scripts/` (see paths above for exact commands).

| Script | Input | Output |
|:---|:---|:---|
| `correct_srt.py` | `raw.srt` | `raw_corectat.srt` |
| `translate_srt.py` | `_RO.srt` + `video.mp4` | `_EN.srt` next to video |
| `analyze_srt.py` | `_RO.srt` + `video.mp4` | `video_metadata.txt` + `shorts_candidates.txt` |
| `analyze_srt.py --shorts-config` | config yaml | `{name}_metadata.txt` in `shorts/` |
| `shorts_generator.py` | `video.mp4` + config | `Short{N}-{name}.mp4` + karaoke ASS |
| `reframe.py` | `video.mp4` | crop path JSON (time -> x, y) for a moving reframe |
| `thumbnail.py` | portrait photo + title | `thumbnail.jpg` (1280x720) + optional `thumbnail.psd` |
| `kdenlive_from_fragments.py` | `fragments.yaml` | `.kdenlive` rough-cut project |
| `srt_from_fragments.py` | `fragments.yaml` + raw SRTs | retimed `.srt` for the rough cut |
| `notify.py` | message | POST to the project's ntfy topic |
| `inbox.py` | watcher | inbox folder → project per day → `recap.py` |
| `serve.py` | server | read-only preview of the workspaces on the tailnet |

---

## Foundation (AI Director, phase D0)

Shared building blocks for the AI Director track (see `docs/VISION.md`). Independent of the finished-clip pipeline above.

- `scripts/common/` — shared code no tool may duplicate: `notify.py` (ntfy), `ffmpeg_tools.py` (encoder selection), `schema.py` (JSON-Schema validation), `config.py` (AI Director config loader), `__init__.py`.
- `config.yaml` — project-wide AI Director config: OpenRouter model id per role, per-job `llm_budget_usd`, `llm_backend`, optional `spend_ledger`. Copy from the committed `config.yaml.example`; `config.yaml` is git-ignored and holds no secrets (API keys stay in `OPENROUTER_API_KEY`).
- `scripts/common/llm.py` — OpenRouter client with `llm_budget_usd` spend tracking against a per-project ledger file (`<workspace>/llm_spend.json`), role-to-model resolution and a mock backend for offline tests and dry runs. An OpenRouter response without `usage.cost` raises `MissingUsageCost` instead of counting as free.
- `schemas/` — JSON Schemas for the artifacts that pass between stages. `fragments.schema.json` describes the edit decision list read by `kdenlive_from_fragments.py` and `srt_from_fragments.py`; `ingest.schema.json`, `motion.schema.json` and `quality.schema.json` describe the D1a analysis outputs; `reframe.schema.json` describes the crop path written by `reframe.py`.
- `tests/` — pytest suite with tiny offline fixtures. Runs on a CPU-only machine (no GPU, no torch/whisperx).
- `scripts/notify.py` — post a short message to the project ntfy server (URL from `NTFY_URL`, default `http://10.10.1.61:8090/opencode-doru`).

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/notify.py "hello" --dry-run
```

`shorts_generator.py` and the two rough-cut generators no longer assume a GPU: they pick `h264_nvenc` with an NVIDIA GPU, `h264_videotoolbox` on macOS, and fall back to `libx264` otherwise, and validate their `fragments.yaml` input against the schema before touching any media.

---

## Footage analysis (AI Director, phase D1a)

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

## Render (edit.json → 720p MP4)

The S1 output adapter turns an `edit.json` edit list into a preview-quality 720p MP4. It cuts each video slot from the proxy clip named in `sources[]`, scales and pads it to the requested frame, joins the slots in timeline order with crossfades or hard cuts and keeps each slot's own ambient audio. Captions, graphics, music and SFX are warned about and ignored. Skill: `yt-render`.

```bash
.venv/bin/python scripts/render.py --edit /path/to/ws/edit.json --out /path/to/ws/recap_720p.mp4 --json
```

| Script | Input | Output |
|:---|:---|:---|
| `render.py` | `edit.json` + the clips in `sources[]` | `--out` MP4 (1280x720, libx264 + AAC) |

`--edit` is validated against `schemas/edit.schema.json`; `--media-dir` resolves clip names not in `sources[]`; `--width`/`--height` set the frame; `--xfade-s` is the fallback crossfade length. Re-running skips when `--out` is newer than `--edit` unless `--force` is given. The MP4 is written atomically and never touches the source clips.

---

## Thumbnail (brand kit)

`thumbnail.py` turns a portrait photo + a title into a branded 1280x720 YouTube thumbnail (the approved `DEPPSiT-template-v2` look, driven by `brand.json`): cut-out portrait with an outer glow, generated or photo background, title/keyword/kicker in the brand fonts and the `#DEPPSiT` tag. Skill: `yt-thumbnail`.

```bash
.venv/bin/python scripts/thumbnail.py --photo photo.jpg --title "AI-UL MEU" --keyword "RULEAZĂ ACASĂ" \
    --kicker "// homelab · automatizări" --theme tech --out thumb/
.venv/bin/python scripts/thumbnail.py --photo photo.jpg --title "AI-UL MEU" --keyword "RULEAZĂ ACASĂ" \
    --theme creator --background frame.jpg --out thumb/ --psd
```

- `--theme tech` generates the dark background; `--theme creator` requires `--background FRAME.jpg` (cover-scaled, darkened). The look (colors, fonts, layout, glow) comes from `brand.json` (validated against `schemas/brand.schema.json`); fonts ship in `assets/fonts/` (SIL OFL).
- `--psd` also writes `thumbnail.psd` with the layers `background`, `portrait` (glow baked in), `title`, `keyword`, `kicker`, `tag` for manual retouching.
- The cut-out uses rembg (`birefnet-portrait`, downloaded to `~/.rembg/models/` on first run); `--skip-cutout` uses the photo's own alpha instead.

---

## Background removal (matte.py)

`matte.py` cuts a talking-head clip out of its background with RobustVideoMatting (RVM, loaded through `torch.hub` — never vendored) and writes a ProRes 4444 `.mov` with alpha, ready to place over screen capture. It runs on CUDA when present, else on CPU, and processes only the `--start`/`--end` range. Skill: `yt-matte`.

```bash
.venv/bin/python scripts/matte.py clip.mp4 --start 10 --end 40 --ratio 0.25 --out clip_matte.mov --json
```

| Script | Input | Output |
|:---|:---|:---|
| `matte.py` | `clip.mp4` (+ `--start`/`--end`) | `clip_matte.mov` — ProRes 4444 with alpha, video only |

`--ratio` is RVM's `downsample_ratio` (default 0.6, steadier edges; 0.25 is faster). The output keeps the input resolution and fps and has no audio (the editor keeps the original's). Re-running overwrites the output; an `--out` that would overwrite the input is refused. The first run downloads the RVM code and weights into `~/.cache/torch/hub/`.

---

## Graphics overlays (graphics.py)

`graphics.py` renders branded graphic overlays (lower third, counter, callout) as ProRes 4444 `.mov` files with alpha at timeline resolution, ready to drop over the edit in the editor. The engine is HyperFrames (Apache-2.0): a composition HTML per template in `assets/graphics/` is filled with the brand values from `brand.json` and the entry props, then rendered by the pinned `hyperframes@0.8.181` CLI. Skill: `yt-graphics`.

```bash
.venv/bin/python scripts/graphics.py --template lower_third \
    --props '{"name":"Doru","role":"creator"}' --out /tmp/lt.mov --json
.venv/bin/python scripts/graphics.py --template counter \
    --props '{"number":"42","label":"clips"}' --out /tmp/counter.mov --duration 3
.venv/bin/python scripts/graphics.py --template callout \
    --props '{"text":"echo hello","style":"terminal"}' --out /tmp/callout.mov
.venv/bin/python scripts/graphics.py --plan graphics_plan.json --out-dir overlays/ --json
```

- `--template` is `lower_third` (`name`, `role`), `counter` (`number`, `label`) or `callout` (`text`, optional `style` `text`/`terminal`). `--width`/`--height`/`--fps`/`--duration` default to `1920`/`1080`/`30`/`3.0` (`--duration` `> 0` and `<= 15`).
- `--plan` renders only the `approved: true` entries of a `graphics_plan.json` (validated against `schemas/graphics_plan.schema.json`), one `<id>.mov` per entry in `--out-dir`.
- `--backend hyperframes` (default) needs Node.js 22+ and npx; `--backend mock` is deterministic and offline (composed `index.html` + an ffmpeg transparent MOV). Every output is checked with ffprobe for an alpha pixel format (`yuva444p10le`/`yuva444p12le`).
- The look comes from `brand.json` via `--brand`; a missing font file is a hard error naming the path and the brand key. The HyperFrames agent skills are not committed (the published set is ~19 MB); install them with `GIT_CLONE_PROTECTION_ACTIVE=0 npx skills add heygen-com/hyperframes --all` (pinned commit `908f2d095bba4e2b97363c5533e2702f1e92505d`).

---

## Director (AI Director, phase S1)

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

---

## Rough-cut assembly (raw footage → Kdenlive project)

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

---

## Installation

```bash
git clone https://github.com/Dorusto/yt-pipeline.git
cd yt-pipeline
uv venv
uv pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu124
```

Requires: `ffmpeg` (the encoder is automatic — `h264_nvenc` with an NVIDIA GPU, `h264_videotoolbox` on macOS, else `libx264`), `openai-whisper` via pipx, `OPENROUTER_API_KEY` in environment (DeepSeek-direct retired 2026-09-02, no more top-ups — see `docs/DECISIONS.md`). Run the test suite with `.venv/bin/python -m pytest -q` (deps: `requirements-dev.txt`).

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
    # no x_offset → moving crop that follows the subject (reframe.py)

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
| Video render | `ffmpeg h264_nvenc` (NVIDIA GPU) / `h264_videotoolbox` (macOS) / `libx264` (CPU fallback, automatic) |
| AI (translation; metadata gen deprecated) | DeepSeek via OpenRouter |
| Notifications | ntfy (`scripts/notify.py`) |
| Schemas / tests | JSON Schema (`jsonschema`) / `pytest` |
| Package management | `uv` |
