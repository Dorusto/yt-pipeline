---
name: yt-eval-edit
description: Check a workspace edit.json against a YAML of human expectations — every must_include item (at least one of its shot keys must appear on a slot) and the total on-screen length (length_s target ± tolerance, middle-speed ramps and photo durations counted) printed as a PASS/FAIL table, exit 1 on any fail. Use when the user asks to verify an edit against Doru's feedback turned into a test, to check that the heroes made it into the edit, or to re-check an edit after a director or llm_director run. Trigger phrases - "verifică edit.json", "sunt cadrele hero în montaj", "check the edit against expectations", "eval the edit", "did the edit pass the expectations", "rulează așteptările pe edit". Not for scoring motion/quality labels against ground truth (that is yt-eval) and not for building or fixing the edit (that is director / llm_director).
compatibility: Run from the repository root. Needs the repo .venv with pyyaml; CPU only, no GPU, no network, no model calls. Reads the workspace and the YAML read-only; the only file written is --out. Fast (well under a second).
---

# yt-eval-edit

Turn "the edit looks right" into a pass/fail list. The expectations YAML holds what a human requires of an edit — the shot keys that must appear and the total length — and this tool checks `WORKSPACE/edit.json` against it, one PASS/FAIL row per expectation in YAML order. Use it after a director run and every time Doru's review feedback is written down as an expectation, so the same mistake cannot come back unnoticed.

## Step 1: check an edit

```bash
.venv/bin/python scripts/eval_edit.py --workspace /path/to/ws --expect eval/kotor-edit.yaml
.venv/bin/python scripts/eval_edit.py --workspace /path/to/ws --expect eval/kotor-edit.yaml --json
.venv/bin/python scripts/eval_edit.py --workspace /path/to/ws --expect eval/kotor-edit.yaml --out report.json
```

- `--workspace DIR` (required) holds `edit.json` (written by `scripts/llm_director.py` or `scripts/director.py`). `--expect PATH` (required) is the YAML below.
- The default prints a fixed-width table, one row per expectation, then `N passed, M failed`. `--json` prints the report object instead; `--out FILE` additionally writes that object atomically (`.tmp` then `os.replace`), re-run safe and overwriting.
- Exit code 0 only when every expectation passes; exit 1 when any expectation fails or an input is missing/unreadable (one `[error] ...` line on stderr). It never asks questions.
- The workspace and the YAML are read-only; the tool calls no model (`llm_cost_usd` is always 0.0).

## Expectations YAML format

```yaml
must_include:
  - any_of: ["0072/s5"]
    note: "the tunnel approach is the strongest long take of the day (0.91) and must be in the edit"
  - any_of: ["0072/s7b1", "0072/s7b5"]
    note: "one of the cave-exit heroes"

length_s:
  target: 60
  tolerance: 3
```

- Each `must_include` item passes when at least one of its `any_of` shot keys appears on a slot of `edit.json` `video[]`. A plain string item (`- "0072/s5"`) means `any_of` with that one key and an empty note. `note` is the human reason, always printed.
- A slot's key is its `shot_key`; a photo slot falls back to `id`, then to the photo file stem plus `/p1` (e.g. `river/p1`).
- `length_s` passes when the total on-screen length lies in `[target - tolerance, target + tolerance]`. A photo slot counts its `duration_s`; a video slot counts `0.25*L + 0.5*L/speed + 0.25*L` for a source window of `L = out_s - in_s` seconds whose middle half plays at `middle_speed` (default 1.0, where it equals `L`) — the same total the director reports as `total_duration_s`.

## `--json` keys

`generated_at`, `tool`, `workspace`, `expect`, `edit`, `checks` (in YAML order; a must_include check carries `any_of`, `found` — which of the keys are in the edit — `passed`, `note`; a length_s check carries `target`, `tolerance`, `got`, `passed`, `note`), `passed` (true only when every check passed), `wall_time_s`, `llm_cost_usd`.

## Notes

- The expectations file holds only shot keys and numbers — no paths, no footage — so it is safe to commit (see `eval/kotor-edit.yaml`).
- A missing shot fails with `found: []`; a failing length shows the measured total in the row (`length_s 60+-3 (got 56.85)`), so the fix is usually obvious from the table alone.
- This tool only judges; it never edits `edit.json`. Fixing the edit to pass is a director/llm_director task.
