# S57 — Mode B, second slice (lots 1-3)

Draft report for Doru to confirm. Scope: the phone cut review (lot 1), topic
chapters (lot 2) and Shorts ranking (lot 3) on top of the S56 Mode B flow.

## Works

- **Phone cut review** (lot 1, commit `5771d25`): `serve.py` Cuts tab
  (`?tab=cuts`, Mode B projects), `POST /<project>/cut-review` →
  `<workspace>/cut_review.yaml` (atomic, idempotent per cut id),
  `GET /<project>/cut-audio?id=` (cached wav of the cut span ±1 s under
  `cut_previews/`), `rough_cut.py --review` (a `keep` row stays in, `reason:
  kept by review` in the marker, run ids stable) and `eval_rough_cut.py
  --review` (rows as extra ground truth winning over the `--fcproj` reference in
  their span). 138 targeted tests, full suite 1494 at that point; smoke verified
  the keep tap → rerun path end to end.
- **Chapters** (lot 2, commits `89e0a14`, `d779382`): `schemas/chapters.schema.json`,
  `scripts/chapters.py` + `prompts/chapters.md` (kept transcript in timeline
  time, one `metadata`-role pass, retry once, first chapter snapped to 0,
  validated atomic `chapters.json`), `export_filmcraft.py --chapters` (one Green
  section marker per chapter, `markers.sections`) and the `mode_b.py` chapters
  step (skip-on-exists, `--force` re-runs, tests offline via `YT_CONFIG` mock).
  61 targeted tests, full suite 1515 at that point; smoke: 4 words mapped over 2
  windows, export markers `{green: 3, sections: 1}`.
- **Shorts ranking** (lot 3): `scripts/shorts_rank.py` + `prompts/shorts_rank.md`
  (sentence-aligned 20-60 s candidates inside one chapter, one `director` pass on
  hook/payoff, top 5 by `(hook + payoff) / 2` into a validated atomic
  `shorts_candidates.json`), `shorts_generator.py --candidates` (segments from
  the file instead of `shorts_config.yaml`) and `export_filmcraft.py
  --shorts-candidates` (one Mango marker per top-3 candidate, `markers.shorts`).
  51 targeted tests in the three files (`tests/test_shorts_rank.py` 16,
  `tests/test_export_filmcraft.py` +3, `tests/test_shorts_generator.py` 6), full
  suite 1541. Smoke on an invented fixture with `llm_backend: mock`:
  `shorts_rank.py` scored 14 candidates and wrote the top 5;
  `export_filmcraft.py --shorts-candidates` placed 3 Mango markers
  (`markers.shorts == 3`, `short 1`…`short 3`); `shorts_generator.py
  --candidates` accepted the file and derived 5 segments (`short1`…`short5`).

## Mocked / not verified

- Real OpenRouter model calls: tests and smoke used `llm_backend: mock`; the
  `director` pass never hit the network here.
- No phone browser session: the Cuts tab was checked through HTTP requests, not
  on a real phone.
- No real FilmCraft GUI open of a rough-cut project (headless only).
- `shorts_generator.py --candidates` was exercised on a synthetic fixture only
  and stopped before the render (the fixture has no real video); the derived
  segment list was verified, not the rendered Shorts.

## Doru, check this

- The Cuts tab on the phone: `serve.py` → `?tab=cuts` on a Mode B project.
- `rough_cut.py --review cut_review.yaml` rerun behaviour (a `keep` row stays in
  with `reason: kept by review`).
- `docs/DECISIONS.md` entries of 2026-10-21 (cut review, chapters, Shorts
  ranking).
- `chapters.json` on a real Mode B run (`mode_b.py <folder> --json`).
- `shorts_candidates.json` top 5 on real footage:
  `.venv/bin/python scripts/shorts_rank.py edit.json words/ --chapters chapters.json --json`.
