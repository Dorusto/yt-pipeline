---
name: yt-mode-b
description: Turn one or more talking-head + screen recording sessions into a Mode B rough-cut edit.json — silence/pause gaps cut, standalone Romanian filler words dropped, restarted sentences (retakes) removed, every cut carrying a reason, and paired screen/camera slots sharing one id so export_filmcraft.py opens them as two angles of one cut. Also scores a rough cut against a hand-made FilmCraft edit of the same material (precision/recall, the sessions and sections the reference did not use, the biggest disagreements). Also runs the whole flow on a folder of OBS recordings in one command (mode_b.py transcribes what has no words JSON yet, rough-cuts it and exports the FilmCraft project). Use when the user asks to rough-cut a talking-head + screen session, remove pauses/fillers/retakes from a recording, build an edit.json from a sessions manifest, prepare a Mode B recording for a human edit in FilmCraft, run the whole Mode B flow on a folder in one command, or score the rough cut against a hand edit. Trigger phrases - "fă rough-cut din sesiunile astea", "taie pauzele și filler-ele", "scoate repetările", "rough cut talking-head + ecran", "edit.json din sessions.yaml", "Mode B rough cut", "cut the pauses and fillers", "remove the retakes", "rough-cut this recording", "rulează tot fluxul Mode B", "mode_b.py", "one command Mode B", "score the rough cut", "eval_rough_cut", "cât de bun e rough-cut-ul". Not for the deterministic director over analysed footage (that is `director`), the LLM director (`llm_director`), rendering (`render`), subtitles (`publish`/`subs_from_edit.py`) or the FilmCraft export itself (`export_filmcraft.py`).
compatibility: Run from the repository root. Needs the repo .venv, PyYAML and ffprobe (for media duration and container start time; missing media is a warning, not an error). No model is called unless --llm is given, and --llm only proposes cuts for human approval — it never changes the edit.
---

# yt-mode-b

Mode B is the talking-head + screen recording flow: a camera file, an optional
OBS screen file and the session's Whisper word JSON. `rough_cut.py` (lot 1)
turns one or more sessions into an `edit.json` rough cut that opens through
`export_filmcraft.py` as a FilmCraft project with the screen on `Screen 1` and
the camera on `Camera 1`, the same cuts on both, ready for a human edit.

## Rough cut (`rough_cut.py`)

```bash
.venv/bin/python scripts/rough_cut.py sessions.yaml --out edit.json --json
.venv/bin/python scripts/rough_cut.py sessions.yaml --out edit.json \
  --config scripts/rough_cut_config_example.yaml
.venv/bin/python scripts/rough_cut.py sessions.yaml --out edit.json --llm --json
.venv/bin/python scripts/rough_cut.py sessions.yaml --out edit.json \
  --review cut_review.yaml --json
```

- `sessions.yaml` is the session manifest, validated against `schemas/sessions.schema.json`: one entry per session with `camera`, optional `screen`, `words` (the Whisper word JSON) and optional `sync_offset_s` (seconds added to a camera time to get the screen time; computed from the file name timestamps plus the container start time when omitted).
- `--out` is the `edit.json` to write (atomic replace; re-running with the same inputs rewrites the same edit, only `generated_at` changes).
- `--config` overrides the cut rules; without it the built-in defaults apply (the committed `scripts/rough_cut_config_example.yaml` ships them).
- `--review FILE` reads the phone cut review `cut_review.yaml` (written by `serve.py` from the Cuts tab). A row with `action: keep` whose `id` matches a cut run and whose `window_in_s`/`window_out_s` overlaps that run keeps the material in: the run is not removed, the kept windows merge across it, the absorbing window's `reason` becomes `kept by review`, and the run's `decisions[]` entry becomes `status: used` / `reason: kept by review` with the absorbing window's slot id. A row with `action: cut` changes nothing (the run is cut as usual). A row whose window does not overlap any run is warned and ignored. Run ids (`c001`, `c002`, …) are assigned before the review is applied, so a keep never shifts the ids of later cuts.
- `--llm` (off by default) runs one `director` pass over the kept transcript and writes extra-cut proposals to `<out-dir>/rough_cut_proposals.json` for human approval. The proposals are listed in the `--json` summary and are **never** applied to `edit.json`.
- `--json` reports `outputs` (`edit`, `proposals`), `sessions`, `windows`, `slots`, `words_read`, `words_kept`, `cuts` (`pause`/`filler`/`retake`), `review` (`keep`/`cut`/`ignored`), `warnings`, `wall_time_s`, `llm_cost_usd`.
- Exit codes: `0` success, `2` user error (missing/invalid manifest, missing words JSON, bad config, LLM budget reached). Errors print `[error] <message>` on stderr and write nothing.

### Cut rules

- **Pause cuts**: a gap between words longer than `pause_gap_s` is cut, keeping `handle_s` on each side so words are not clipped. The silence before the first word and after the last word is cut too (the tail needs the media duration; a missing duration is a warning).
- **Filler cuts**: a token in `fillers.always` is dropped whenever it stands alone; a token in `fillers.repeated` is dropped only when it repeats within `repeat_window_s` (a stutter). Isolated occurrences are kept.
- **Retake cuts**: a sentence restarted by a later sentence within `retake_window_s` (normalised-word prefix similarity `>= retake_similarity`, at least `retake_min_words`) is cut, so the last take survives. Chains work naturally.
- Every kept window carries a reason (`start: session`, `cut: pause 1.2 s`, `cut: filler 'deci'`, `cut: retake of w007`); every merged cut run gets a `decisions[]` entry with its reason.

### Paired slots

When a session has a screen file, each kept window becomes a pair of slots sharing one `id`: first the screen slot (camera window shifted by `sync_offset_s`), then the camera slot. `export_filmcraft.py` places the pair at one shared timeline start, with one audio clip per pair and one marker per pair.

## FilmCraft export

```bash
.venv/bin/python scripts/export_filmcraft.py edit.json --out project.fcproj \
  --screen "2026-10-17 22-25-44.mp4" --json
```

`--screen` names the screen source file (the OBS file without the `camera ` prefix does not contain the `screen` token, so pass it explicitly). The export reports `slots_placed` and `markers.green`; a pair contributes two slots and one marker.

## Eval against a hand edit (`eval_rough_cut.py`)

```bash
.venv/bin/python scripts/eval_rough_cut.py --edit edit.json --sessions sessions.yaml \
  --fcproj REF.fcproj --sequence "Second Brain - 15 min" --words-dir words/ --json
.venv/bin/python scripts/eval_rough_cut.py --edit edit.json --sessions sessions.yaml \
  --fcproj REF.fcproj --sequence "Second Brain - 15 min" --words-dir words/ \
  --section-min-s 30.0 --out report.json
.venv/bin/python scripts/eval_rough_cut.py --edit edit.json --sessions sessions.yaml \
  --fcproj REF.fcproj --sequence "Second Brain - 15 min" --words-dir words/ \
  --review cut_review.yaml --json
```

- Compares the words `rough_cut.py` kept with the words a human kept in a FilmCraft project of the same material. Both sides use the same midpoint rule (`(start + end) / 2` in `[in, out)`); a word is identified by `(stem, flat index)`.
- `--edit` is the `edit.json` `rough_cut.py` wrote; `--sessions` is the same manifest (validated against `schemas/sessions.schema.json`); `--fcproj` + `--sequence` name the reference project and the sequence to read (its `Audio 1` track); `--words-dir` holds one words JSON per source stem.
- `--review FILE` reads the phone cut review `cut_review.yaml` and treats each row as extra ground truth that wins over the `--fcproj` reference inside its span: a `keep` row adds the words of its window to Doru-kept (recall drops when our edit cut them), a `cut` row removes them from Doru-kept (precision drops when our edit kept them). The report gains `review_rows` (the number of rows applied) only when the flag is given.
- `--until-s S` scores only the first S seconds of the reference timeline (float, must be `> 0`): the reference clips are trimmed at S, the kept set is built from the trimmed windows, and a word counts on either side only when its stem has a counted clip and its midpoint lies inside that stem's covered source span. Stems the reference uses only after S land in `bucket.session_words`. The `subs_from_edit.py` cross-check stays full-sequence whatever `--until-s` says. The report carries `until_s` (`null` when the flag is absent).
- Reports `precision`/`recall` and `precision_used`/`recall_used` (the same numbers minus the bucket: words in sessions the reference did not use at all, plus dropped runs spanning `--section-min-s`), the ten biggest disagreement runs with timecodes, and a `subs_from_edit.py` cross-check of `words_kept` against our `|doru|`.
- `--json` prints the report object; `--out` also writes it atomically. Exit codes: `0` success (the eval has no pass/fail gate), `2` user error (missing file, bad sequence, a clip with `speed != 1.0` or `reverse`, a non-positive `--until-s`). No model is called (`llm_cost_usd` is always `0.0`).

## One command (`mode_b.py`)

```bash
.venv/bin/python scripts/mode_b.py FOLDER --json
.venv/bin/python scripts/mode_b.py FOLDER --backend mock --force --json
```

`mode_b.py` is the Mode B front door: it scans `FOLDER` for `camera ...` files
(plus an optional sibling screen file whose stem is the camera stem without the
`camera ` prefix), transcribes every session with no words JSON yet, writes
`<out-dir>/sessions.yaml`, runs `rough_cut.py` and exports `project.fcproj`. It
chains the three station CLIs as subprocesses and imports none of them.

- `--out-dir` (default `<FOLDER>/mode_b/`) holds `words/`, `sessions.yaml`,
  `edit.json`, `project.fcproj` and (with `--llm`) `rough_cut_proposals.json`.
- `--backend` is passed to `transcribe.py` (`mock` for offline runs).
- `--config` and `--llm` are passed to `rough_cut.py`.
- Skip rule: a step is skipped when its output exists (`--force` re-runs every
  step). A failing step stops the run (exit `2`) and the next run resumes at it.
- `--json` reports `outputs` (`words_dir`, `sessions`, `edit`, `project`),
  `sessions`, `transcribed`, `skipped` (`transcribe` count, `rough_cut`/`export`
  bools), `warnings`, `wall_time_s`, `llm_cost_usd`. On success the last human
  line is `project: <path>`.
- Exit codes: `0` success, `2` user error (missing folder, no camera files, a
  failing step). Errors print `[error] <message>` on stderr and write nothing.

