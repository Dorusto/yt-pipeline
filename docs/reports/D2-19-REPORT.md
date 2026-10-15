# D2-19 report — edit expectations as a test; the model keeps the top heroes

## Works

- `eval/kotor-edit.yaml` records Doru's v4 review as a file any agent can run: `must_include` (`0072/s5`, and `0072/s7b1` OR `0072/s7b5`) and `length_s: {target: 60, tolerance: 3}`. No footage, no paths — safe to commit.
- `scripts/eval_edit.py` (standalone, `--help`/`--json`/`--out`, read-only) prints PASS/FAIL per expectation and exits 1 on any fail. 14 tests in `tests/test_eval_edit.py` on tiny fake edits; skill `.claude/skills/yt-eval-edit/SKILL.md`.
- `llm_director.validate_edit` raises a blocking `hero_missing` when a top star (hero or score >= `STAR_MIN_SCORE` 0.85, from `common/moves.py`) from the top `STAR_SHARE` (40 %) of the slots is left out. `repair_draft` swaps it in for the weakest non-star slot of a compatible length, never creating a same-take jump cut, never falling back to the rules. A dropped hero photo stays the non-blocking `hero` warning. 6 new tests in `tests/test_llm_director.py`.
- `STAR_MIN_SCORE`, `STAR_SHARE` and `is_star` moved to `scripts/common/moves.py`; `director.py` and `llm_director.py` both import them (the "no tool imports another tool" rule).
- `prompts/editor.md` rule 9 and `prompts/reviewer.md` check 3: keep the top-scored heroes unless the brief excludes them; say which and why in notes if one is dropped.
- Synthesised video slots now carry `shot_key` (they did not, so swapped-in heroes were invisible to `eval_edit`).
- `fill_to_target` grows the edit back to the brief's length with the best unused cuttable shots in chronological order, after the move length policy shortens slots (that policy undoes a fill that runs first).
- Real check on kotor-demo (`recap.py --new-edit`, 60 s, chronological, format both, `--music auto`): `eval_edit.py` reports **3 passed, 0 failed** and `meta.director.by` is `llm`. The repair swapped in `0072/s5`, `0072/s7b1`, `0072/s7b5`, `0080/s1`, `0081/s15b1`, `0081/s7`; total on-screen length 57.89 s. Renders (`recap_720p.mp4`, `recap_720p_9x16.mp4`) rebuilt from that edit (v007).
- Full suite green: `1159 passed`. LLM spend of the task: about 0.29 USD (three director runs), under the 1.00 budget.

## Mocked / not verified

- The LLM loop itself is stubbed in the unit tests (StubClient); the real kotor-demo run is the only live verification of the screenwriter/editor/reviewer path.
- `fill_to_target` was verified on the real workspace and in one unit test; it has no test for the "no unused candidate left" edge.
- Vision/eval on the *visual* quality of the new cut (does the story still read?) — not checked here; only the mechanical expectations.

## Doru, check this

1. `python scripts/eval_edit.py --workspace ~/media/projects/kotor-demo --expect eval/kotor-edit.yaml` → `3 passed, 0 failed`.
2. `python -c "import json; print(json.load(open('/root/media/projects/kotor-demo/edit.json'))['meta']['director'])"` → `"by": "llm"`, notes list the swaps.
3. Watch `~/media/projects/kotor-demo/recap_720p.mp4` (v007): are the tunnel approach (`0072/s5`) and the cave-exit hero in, and does the 58 s cut feel right?
4. `eval/kotor-edit.yaml` — are these the expectations you want, and should more of your review (intentional moves, 9:16 crop) become checks here?
5. `docs/RULES.md` new lines "Top heroes are kept by the model" and "Edit expectations are tests" (D2-19).
