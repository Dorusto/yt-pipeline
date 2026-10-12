# D1a-03 — `scripts/ingest.py`: folder scan with metadata and gyro detection

## Goal

One standalone CLI tool that scans a folder of video clips and writes `ingest.json`
in the project workspace: per clip the duration, fps, resolution, streams,
creation time and whether gyro/IMU data is present. Plus its agent skill and its
tests. This is the first stage of the AI Director analysis pipeline (phase D1a).

## Files to create or change (exact paths, nothing else)

- `scripts/ingest.py` — create
- `.claude/skills/yt-ingest/SKILL.md` — create
- `tests/test_ingest.py` — create

Do not touch any other file. In particular do not edit `CLAUDE.md`, `README.md`,
`docs/`, `schemas/`, `scripts/common/`, `config.yaml*`, or any existing script,
skill or test.

## Rules that apply (copied in full from CLAUDE.md, docs/VISION.md and docs/DECISIONS.md)

1. **Code and documentation → in English. Comments in code → none (self-documenting
   code).** A module docstring and one-line docstrings on public functions/classes
   are fine — same style as `scripts/notify.py` and `scripts/common/schema.py`.
2. **Every new tool** is a standalone script in `scripts/` plus a skill in
   `.claude/skills/`, with `--help`, `--json` and re-run safety. No tool imports
   another tool; shared code only in `scripts/common/`.
3. **Files are the interface.** Stages talk through JSON/YAML on disk, validated
   against schemas in `schemas/`. Any stage can be re-run alone.
4. **Same contract for humans and agents.** Files and flags in, files out.
   `--help` with examples. `--json` prints a machine-readable summary. Safe to
   re-run: steps already done are skipped.
5. **Originals are read-only.** All outputs go to a project workspace next to the
   footage. Never write into the source folder, never modify or delete a source
   file. The repo is public: never commit footage, frames or media files.
6. **Definition of done (every task):** tests pass; ran end-to-end on sample media;
   outputs validated against `schemas/`; no source file modified; skill updated if
   behavior changed.
7. **Skills live in the repo** in `.claude/skills/`, with only portable frontmatter
   fields (`name`, `description`, `compatibility`). Commands are relative to the
   repo root. The skill describes what the script does today.
8. **The build LXC has 4 CPU cores, no GPU and no network guarantee.** Every test
   must pass offline. `ffprobe` is on the PATH. Generate tiny test media inside
   `tmp_path` with ffmpeg; never read or write `~/media/`.
9. **Do not re-install MediaPipe — OpenCV was chosen intentionally.** Not relevant
   here (this tool is ffprobe-only) but do not add dependencies.
10. **GitHub commits** are made by the architect, not by you. Do not run `git
    commit`, `git push` or open PRs.

## Interface

```bash
.venv/bin/python scripts/ingest.py SOURCE_DIR --workspace DIR [--json]
.venv/bin/python scripts/ingest.py --help
```

- **SOURCE_DIR** (positional, required): folder of video clips to scan. Non-recursive:
  only files directly in SOURCE_DIR whose extension is `.mp4`, `.mov` or `.m4v`
  (case-insensitive) are considered. Sorted by file name.
- **`--workspace DIR`** (required): output root. Creates `DIR` if missing and writes
  `DIR/ingest.json`. Never writes into SOURCE_DIR.
- **`--json`**: prints one JSON object to stdout, e.g.
  `{"clips": 21, "output": "/abs/workspace/ingest.json", "gyro_clips": 0, "total_duration_s": 749.5}`.
  Without `--json` print a short human summary (clip count, total duration, how many
  clips have gyro).
- **Exit codes:** `0` success. `1` on any error, with one `[error] ...` line on
  stderr (missing SOURCE_DIR, not a directory, no video files found, ffprobe
  failure). Never prompt interactively.
- **Re-run safety:** re-running simply rewrites `ingest.json` from a fresh scan;
  nothing else is touched. Partial output is never left behind on failure — write
  the JSON only after the whole scan finished and validated.

## Output contract — `DIR/ingest.json`

Must pass `common.schema.validate_or_raise(data, "ingest")` from
`scripts/common/schema.py` (the authoritative schema is
`schemas/ingest.schema.json`, read it). Shape:

```json
{
  "generated_at": "ISO 8601",
  "source_dir": "/abs/path/scanned",
  "clip_count": 21,
  "clips": [
    {
      "file": "name.mp4",
      "path": "/abs/path/name.mp4",
      "size_bytes": 12972850,
      "duration_s": 21.93,
      "creation_time": "2026-10-12T11:36:18" ,
      "creation_time_source": "filename",
      "width": 1280, "height": 720, "fps": 25.0, "video_codec": "h264", "nb_frames": 548,
      "streams": [ {"index": 0, "codec_type": "video", ...}, ... ],
      "gyro": {"present": false, "evidence": null},
      "warnings": []
    }
  ],
  "warnings": []
}
```

Field rules:

- Run `ffprobe -v error -show_streams -show_format -of json FILE` once per clip.
- `duration_s` from the container (`format.duration`), fallback: primary video
  stream duration, else `nb_frames / fps`.
- Primary video stream = first stream with `codec_type == "video"` and a codec that
  is not an attached picture (skip `mjpeg` streams with no `duration` and no
  `nb_frames` if they appear after the main video stream; the main video stream is
  the first video stream with `width` and `height`).
- `fps` from `r_frame_rate` (`"25/1"` → `25.0`); `null` if `0/0`.
- Every stream goes into `streams[]` in index order with `index`, `codec_type`,
  `codec_name`, `codec_tag` (from `codec_tag_string`), `handler_name` (from
  `tags.handler_name`), and when present `width`, `height`, `fps`, `sample_rate`,
  `channels`, `nb_frames`, `duration_s`. `null` for values ffprobe does not report.
- **Creation time** — first match wins:
  1. `creation_time` in `format.tags` or in any stream `tags` → `creation_time_source:
     "metadata"` (keep the string ffprobe reports).
  2. else parse the file name: first `YYYYMMDD` (8 digits) followed by `HHMMSS`
     (6 digits), as in `dji_mimo_20261012_113618_0051_..._video.mp4` →
     `"2026-10-12T11:36:18"`, `creation_time_source: "filename"`.
  3. else `"creation_time": null, "creation_time_source": null`.
- **Gyro** — `gyro.present` is true iff some stream matches:
  `codec_tag == "djmd"` **or** `handler_name` contains `DJI meta`, `gyro`,
  `gyroscope` or `motion` (case-insensitive), **and** when the stream reports
  `nb_frames` it must be `> 1` (a one-sample data track such as `tmcd` timecode is
  not gyro). When present, `gyro.evidence` is a short string naming the stream,
  e.g. `stream 2: codec_tag=djmd handler='DJI meta' samples=548`; when absent,
  `gyro.evidence` is `null`. Rationale: DJI Osmo Action 4 writes IMU samples into a
  `djmd` "DJI meta" stream only when in-camera stabilization is off; Mimo exports
  and proxies usually drop it.
- `clip_count` equals `len(clips)`.
- `warnings` (clip-level and top-level): human-readable notes, e.g. a stream ffprobe
  reports oddly; empty lists when there is nothing to say.

## Skill — `.claude/skills/yt-ingest/SKILL.md`

Follow the style of the existing skills (frontmatter with `name`, `description`,
`compatibility`, then steps with commands from the repo root). `name: yt-ingest`.
The `description` must mention scanning a footage folder for duration/fps/
resolution/streams/creation time/gyro and include trigger phrases such as
"scanează folderul de footage", "ingest metadata", "verifică dacă are gyro",
"scan the footage folder". `compatibility`: run from the repository root, needs
the repo `.venv` and `ffprobe`; reads the source folder read-only; writes
`ingest.json` into the workspace.

## Tests — `tests/test_ingest.py`

`tests/conftest.py` already puts `scripts/` on `sys.path`, so `import common.schema`
works. Generate tiny clips inside `tmp_path` with ffmpeg, e.g.:

```bash
ffmpeg -v error -f lavfi -i testsrc=size=64x48:rate=10:duration=1 -c:v libx264 -pix_fmt yuv420p out.mp4
```

Cover at least:

1. `--help` exits 0 and mentions `--workspace` and `--json`.
2. A folder with two generated clips → `ingest.json` written, validates against the
   `ingest` schema, `clip_count == 2`, file names sorted.
3. Per-clip fields: `duration_s` ≈ 1.0, `width == 64`, `height == 48`, `fps` ≈ 10.0,
   a video stream at index 0, `gyro.present is False`.
4. Creation time fallback: a clip named `dji_mimo_20261012_113618_0051_x.mp4` gets
   `creation_time == "2026-10-12T11:36:18"` and `creation_time_source == "filename"`
   (ffmpeg writes no creation_time tag with `-v error` defaults on this machine —
   if it does, accept `"metadata"` and assert the value is not null instead).
5. Gyro detection on a synthetic file is not possible with plain ffmpeg; unit-test
   the detection helper directly with a fake stream dict: a `djmd` stream with
   `nb_frames: 548` → present with evidence; a `tmcd` stream with `nb_frames: 1` →
   absent; an `mjpeg` `VideoHandler` stream → absent. Expose that helper as a
   module-level function in `scripts/ingest.py` (e.g. `gyro_from_streams(streams)`).
6. Errors: missing SOURCE_DIR → exit 1 with `[error]` on stderr; a folder with no
   video files → exit 1; `--json` on success prints parseable JSON.
7. Re-run: run twice, output still validates and matches.

Use `subprocess` to run the script as a CLI (like an end-to-end check) and direct
function calls for the helpers. Run tests with:

```bash
.venv/bin/python -m pytest -q
```

## Acceptance checks (the architect will run these)

```bash
cd /root/projects/yt-pipeline
.venv/bin/python -m pytest -q                      # all green (existing suite + new)
.venv/bin/python scripts/ingest.py --help           # usage + examples
.venv/bin/python scripts/ingest.py ~/media/yt-test/proxy --workspace ~/media/yt-test/workspace --json
```

Expected on the real test folder (21 DJI Mimo proxies, read-only):

- exit 0, `--json` reports `"clips": 21`;
- `~/media/yt-test/workspace/ingest.json` validates against the `ingest` schema;
- every clip: 1280x720, 25 fps, h264 video + aac audio, plus a one-sample `tmcd`
  data stream;
- `gyro.present` is `false` for all 21 (proxies lost the DJI metadata stream);
- `creation_time_source` is `"filename"` for all 21 (proxies lost the creation_time
  tag) with times matching the `dji_mimo_YYYYMMDD_HHMMSS_` pattern in the names.

## Out of scope

Proxy generation, motion analysis, quality analysis, LLM calls, recursive folder
scan, reading `~/media/yt-test/raw`, schema changes (the schema is already written
and is the contract), README/ROADMAP/DECISIONS updates (the architect writes those),
git commits.
