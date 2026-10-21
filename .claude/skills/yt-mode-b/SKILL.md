---
name: yt-mode-b
description: Turn one or more talking-head + screen recording sessions into a Mode B rough-cut edit.json — silence/pause gaps cut, standalone Romanian filler words dropped, restarted sentences (retakes) removed, every cut carrying a reason, and paired screen/camera slots sharing one id so export_filmcraft.py opens them as two angles of one cut. Use when the user asks to rough-cut a talking-head + screen session, remove pauses/fillers/retakes from a recording, build an edit.json from a sessions manifest, or prepare a Mode B recording for a human edit in FilmCraft. Trigger phrases - "fă rough-cut din sesiunile astea", "taie pauzele și filler-ele", "scoate repetările", "rough cut talking-head + ecran", "edit.json din sessions.yaml", "Mode B rough cut", "cut the pauses and fillers", "remove the retakes", "rough-cut this recording". Not for the deterministic director over analysed footage (that is `director`), the LLM director (`llm_director`), rendering (`render`), subtitles (`publish`/`subs_from_edit.py`) or the FilmCraft export itself (`export_filmcraft.py`).
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
```

- `sessions.yaml` is the session manifest, validated against `schemas/sessions.schema.json`: one entry per session with `camera`, optional `screen`, `words` (the Whisper word JSON) and optional `sync_offset_s` (seconds added to a camera time to get the screen time; computed from the file name timestamps plus the container start time when omitted).
- `--out` is the `edit.json` to write (atomic replace; re-running with the same inputs rewrites the same edit, only `generated_at` changes).
- `--config` overrides the cut rules; without it the built-in defaults apply (the committed `scripts/rough_cut_config_example.yaml` ships them).
- `--llm` (off by default) runs one `director` pass over the kept transcript and writes extra-cut proposals to `<out-dir>/rough_cut_proposals.json` for human approval. The proposals are listed in the `--json` summary and are **never** applied to `edit.json`.
- `--json` reports `outputs` (`edit`, `proposals`), `sessions`, `windows`, `slots`, `words_read`, `words_kept`, `cuts` (`pause`/`filler`/`retake`), `warnings`, `wall_time_s`, `llm_cost_usd`.
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

## Later lots

The eval script (`eval_rough_cut.py`) and the one-command wrapper (`mode_b.py`) are later lots; this skill covers `rough_cut.py` only.
