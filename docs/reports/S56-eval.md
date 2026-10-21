# S56 — Mode B rough-cut eval (lot 2, follow-up 02)

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

`--until-s S` (commit 3d269b4) scores only the finished stretch of the reference:
the reference clips are trimmed at `S` on the timeline, and a word counts only
when its stem is used in those minutes and its midpoint lies in that stem's
covered source span. Stems used only after `S` land in the session bucket. The
`subs_from_edit.py` cross-check stays full-sequence in every run.

The eval is a measuring tool: no pass/fail gate, exit `0` on success, `2` on a
user error, no model (`llm_cost_usd` is always `0.0`).

## Headline: the finished stretch (`--until-s 150`)

Reference: the personal `Second Brain` project, sequence `Second Brain - 15 min`,
223 audio clips, read-only. Only the first 150 s of the reference timeline is
really edited; the rest is close to raw. Verified on the reference: the source
material inside the early covered spans but outside the kept windows is never
used later in the sequence (0 s overlap), which is why `--until-s` restricts each
used source to the envelope of its counted windows.

Final config **A1** (`retake_similarity: 0.75`, `fillers.always` with `ok`):

| metric | A1 `--until-s 150` |
|:---|---:|
| ours kept | 933 |
| doru kept | 377 |
| intersect | 363 |
| precision | 0.3891 |
| recall | 0.9629 |
| **precision_used** | **0.5619** |
| **recall_used** | **0.9629** |
| F1 (used) | 0.7097 |
| bucket session_words | 6220 |
| bucket section_words | 296 |
| cross-check `words_kept` | 1891 |
| cross-check agrees | yes |

`doru.sessions_used` = 4 of `doru.sessions_total` = 31 on this slice. These are
the honest ceiling while the reference is only edited for its first 150 s.

## Full sequence (reference unfinished — indicative only)

The same final config **A1** over the whole sequence:

| metric | A1 full |
|:---|---:|
| ours kept | 7330 |
| doru kept | 1955 |
| intersect | 1941 |
| precision | 0.2648 |
| recall | 0.9928 |
| **precision_used** | **0.6362** |
| **recall_used** | **0.9928** |
| F1 (used) | 0.7755 |
| bucket session_words | 2178 |
| bucket section_words | 2241 |
| cross-check `words_kept` | 1891 |
| cross-check agrees | yes |

`doru.sessions_used` = 20 of 31 full. These numbers are **not a ceiling**: most
of the sequence is close to raw, so the reference side is not a finished edit.

## Threshold decision

Four cut-rule configs were run (`rough_cut.py` on the same 31-session manifest,
then the eval twice per edit — with `--until-s 150` and full):

- A = lot 1 defaults before tuning: `retake_similarity: 0.75`, `fillers.always`
  without `ok`
- A1 = only the `ok` filler added: `retake_similarity: 0.75`, `fillers.always`
  with `ok`
- B1 = only the retake change: `retake_similarity: 0.55`, `fillers.always`
  without `ok`
- B = both (the S56 lot 2 state): `retake_similarity: 0.55`, `fillers.always`
  with `ok`

| cfg | scope | ours kept | doru kept | intersect | precision | recall | precision_used | recall_used | F1_used | bucket session | bucket section | cross-check agrees |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | --until-s 150 | 934 | 377 | 363 | 0.3887 | 0.9629 | 0.5611 | 0.9629 | 0.7090 | 6220 | 296 | yes |
| A1 | --until-s 150 | 933 | 377 | 363 | 0.3891 | 0.9629 | 0.5619 | 0.9629 | 0.7097 | 6220 | 296 | yes |
| B1 | --until-s 150 | 918 | 377 | 358 | 0.3900 | 0.9496 | 0.5629 | 0.9496 | 0.7068 | 6220 | 296 | yes |
| B | --until-s 150 | 917 | 377 | 358 | 0.3904 | 0.9496 | 0.5638 | 0.9496 | 0.7075 | 6220 | 296 | yes |
| A | full | 7344 | 1955 | 1941 | 0.2643 | 0.9928 | 0.6345 | 0.9928 | 0.7742 | 2178 | 2241 | yes |
| A1 | full | 7330 | 1955 | 1941 | 0.2648 | 0.9928 | 0.6362 | 0.9928 | 0.7755 | 2178 | 2241 | yes |
| B1 | full | 7261 | 1955 | 1936 | 0.2666 | 0.9903 | 0.6396 | 0.9903 | 0.7772 | 2178 | 2241 | yes |
| B | full | 7247 | 1955 | 1936 | 0.2671 | 0.9903 | 0.6413 | 0.9903 | 0.7785 | 2178 | 2241 | yes |

The decision rule was: a tuned value is kept only if on the `--until-s 150` slice
it does not lose recall (beyond 0.002) — precision/F1 decides between equals.

**Verdict: A1 is the final config.**

- `fillers.always` + `ok` **stays**: precision_used +0.0008, recall_used flat,
  best F1_used on the finished stretch (0.7090 → 0.7097).
- `retake_similarity` 0.55 is **reverted to 0.75** (commit cf1b59f): it buys
  +0.0018 precision_used but loses -0.0133 recall_used and lowers F1_used
  0.7090 → 0.7068 on the finished stretch. It only looked good on the unfinished
  full sequence. The value may come back after the reference edit is final.

## Honest notes

- `pause_gap_s` and `handle_s` are **not tunable by this word-set metric**: they
  change the cut timing, not which words are kept, so they stay at 0.6 / 0.12.
- Tuning on a single reference risks **overfitting**. The kept change is small and
  plausible, but the gain (F1_used 0.7090 → 0.7097) is within noise; a second
  reference would be needed to confirm it.
- **The eval must be rerun when the reference edit is final.** Until then every
  threshold claim is provisional — the finished stretch is only the first 150 s
  and the full-sequence numbers are not a real ceiling.
- The cross-check compares like-for-like: `subs_from_edit.py` glues hyphen
  clitics onto the previous word before counting, so its `words_kept` is 1891
  while the raw kept-word count is 1955 (the number in issue #56).
  `eval_rough_cut.py` applies the same glue in reference timeline order before
  comparing, and the two agree (1891 = 1891).
