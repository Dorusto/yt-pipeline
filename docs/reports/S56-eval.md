# S56 — Mode B rough-cut eval (lot 2)

Draft report for Doru to confirm. Scope: does `rough_cut.py` keep the words a
human kept in a hand-made FilmCraft edit of the same material, and can the lot 1
thresholds be tuned on that number?

## What the eval measures

`scripts/eval_rough_cut.py` compares the words `rough_cut.py` kept with the words
a human kept in a reference FilmCraft project of the same recordings. Both sides
are mapped with the same midpoint rule (`(start + end) / 2` in `[in, out)`); a
word is identified by `(stem, flat index)`. It reports:

- `precision` / `recall` over all words;
- `precision_used` / `recall_used`, the same numbers minus the **bucket** — the
  words in sessions the reference did not use at all, plus the words in dropped
  runs spanning at least `--section-min-s` (default 30 s). These are the headline
  numbers: recall on content Doru chose to drop is not expected;
- the ten biggest disagreement runs (`ours_only` / `doru_only`) with timecodes;
- a cross-check of `subs_from_edit.py`'s `words_kept` against our `|doru|`.

The eval is a measuring tool: no pass/fail gate, exit `0` on success, `2` on a
user error, no model (`llm_cost_usd` is always `0.0`).

## Real run

Reference: the personal `Second Brain` project, sequence `Second Brain - 15 min`,
read-only. The sessions manifest was built in `/tmp` over every camera word JSON
stem (31 sessions; the camera paths do not exist, which is a warning, not an
error). `rough_cut.py` was run with the lot 1 defaults, then the eval.

| metric | before | after |
|:---|---:|---:|
| ours kept | 7344 | 7247 |
| doru kept | 1955 | 1955 |
| intersect | 1941 | 1936 |
| precision | 0.2643 | 0.2671 |
| recall | 0.9928 | 0.9903 |
| **precision_used** | **0.6345** | **0.6413** |
| **recall_used** | **0.9928** | **0.9903** |
| F1 (used) | 0.7742 | 0.7785 |
| bucket session_words | 2178 | 2178 |
| bucket section_words | 2241 | 2241 |
| bucket total | 4419 | 4419 |
| cross-check `words_kept` | 1891 | 1891 |
| cross-check agrees | yes | yes |

`doru.sessions_used` = 20 of `doru.sessions_total` = 31 (11 sessions the
reference did not use at all). `wall_time_s` ≈ 0.25 s, `llm_cost_usd` = 0.0.

## Chosen thresholds

Only two values changed; both are in `scripts/rough_cut.py` `DEFAULT_CONFIG` and
`scripts/rough_cut_config_example.yaml`.

- `retake_similarity`: **0.75 → 0.55** — catches more restarted sentences; on this
  reference `recall_used` moves only 0.9928 → 0.9903 while `precision_used` rises
  0.6345 → 0.6413.
- `fillers.always`: **+ `ok`** — a standalone interjection; dropping it whenever it
  stands alone raises `precision_used` without lowering `recall_used`.

`retake_window_s` (60 s), `retake_min_words` (3) and `repeat_window_s` (3.0 s)
were left unchanged: sweeping them (window 60/90/120 s, min words 2/3, repeat
window 2/4/5 s) did not move `precision_used`/`recall_used` on this reference.

## What the biggest disagreements show

All ten biggest disagreement runs are `ours_only` (words we keep that the
reference dropped), and every one of them sits in a session the reference did not
use at all — i.e. they are already inside the session bucket, not counted against
`precision_used`. The largest runs, by source timecode and word count:

| start_s | end_s | words |
|---:|---:|---:|
| 28.08 | 394.64 | 491 |
| 3.90 | 218.00 | 362 |
| 120.62 | 267.52 | 223 |
| 2.86 | 187.62 | 219 |
| 3.84 | 123.06 | 213 |
| 105.24 | 246.70 | 173 |
| 4.86 | 116.89 | 162 |
| 97.70 | 192.60 | 161 |
| 268.32 | 359.16 | 156 |
| 515.02 | 599.44 | 143 |

The remaining false positives inside used stems (1118 words) are almost all
common function words from content the reference trimmed sentence by sentence;
the pause/filler/retake rules cannot reach them, which is why the tuning gain is
small.

## Honest notes

- `pause_gap_s` and `handle_s` are **not tunable by this word-set metric**: they
  change the cut timing, not which words are kept, so they stay at 0.6 / 0.12.
- Tuning on a single reference risks **overfitting**. The two changes above are
  small and plausible, but the gain (F1 0.7742 → 0.7785) is within noise; a second
  reference would be needed to confirm them.
- The cross-check compares like-for-like: `subs_from_edit.py` glues hyphen
  clitics (`-uri`, `-am`) onto the previous word before counting, so its
  `words_kept` is 1891 while the raw kept-word count is 1955 (the number in
  issue #56). `eval_rough_cut.py` applies the same glue in reference timeline
  order before comparing, and the two agree (1891 = 1891).
