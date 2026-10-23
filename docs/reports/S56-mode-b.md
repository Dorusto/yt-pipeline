# S56 — first Mode B slice (lots 1-3)

Draft report for Doru to confirm. Scope: talking-head + screen OBS recordings to a
FilmCraft rough cut a human finishes — `rough_cut.py` (cut model + paired slots),
`eval_rough_cut.py` (scored against Doru's hand edit, thresholds tuned) and
`mode_b.py` (one command: transcribe what is missing → rough cut → export,
skipping any step whose output already exists).

## Works

- **Cut model** (`rough_cut.py`, lot 1): pause gaps over `pause_gap_s` cut with a
  `handle_s` each side, standalone fillers dropped, restarted sentences cut keeping
  the last take; every cut run is a rejected `decisions[]` entry and the kept
  window's `reason` names the cut before it. Verified by `tests/test_rough_cut.py`
  (15 tests) and the reference eval below.
- **Paired-slot export** (`export_filmcraft.py`, lot 1): two consecutive video slots
  sharing one `id` (screen first, then camera) are placed at one shared timeline
  start, one audio clip and one marker per pair. Verified by
  `tests/test_export_filmcraft.py` and the `mode_b.py` smoke (the fixture's paired
  session produced slots `w001` screen + `w001` camera at the same start).
- **Eval numbers** (`docs/reports/S56-eval.md`, lot 2): before → after
  `precision_used` 0.6345 → 0.6413, `recall_used` 0.9928 → 0.9903, F1 (used)
  0.7742 → 0.7785; `doru kept` 1955 of 5321 words in the used sessions; bucket
  4419 words (2178 unused sessions + 2241 dropped sections); cross-check
  `subs_from_edit.py` `words_kept` 1891 = our merged count (agrees).
- **`mode_b.py` end-to-end** on an invented fixture folder (one camera + screen
  pair, one camera-only session, `--backend mock`): run 1 transcribed 2 and wrote
  `sessions.yaml`/`edit.json`/`project.fcproj`; run 2 transcribed 0 and skipped
  rough cut + export; `--force` re-ran everything. Verified by
  `tests/test_mode_b.py` (18 tests) and the manual smoke in the task reply.
- **Full suite**: `.venv/bin/python -m pytest tests/ -q` → 1469 passed.

## Mocked / not verified

- No real FilmCraft GUI open of a rough-cut project — headless only (the S54 spike
  already opened a project headless; this lot did not re-open one).
- No real OBS dual-file sync against real media: the container start-time path is
  unit-tested with missing media (the sync offset falls back to 0.0 with a warning).
- The `--llm` extra-cut pass only ran on the mock backend.
- Whisper transcription only ran on the mock backend here; the real `whisper`
  backend was not exercised.

## Doru, check this

- `docs/reports/S56-eval.md` — only the `ok` filler was kept and
  `retake_similarity` stayed at 0.75, plus the overfitting note.
- The paired-slot rule in `docs/DECISIONS.md` (`[2026-10-21] Mode B angles: paired
  edit.json slots sharing one id`) and the cut-rules entry.
- Run it on a real folder:
  `.venv/bin/python scripts/mode_b.py <folder> --json` (add `--backend mock` for an
  offline run, `--force` to re-run every step).
