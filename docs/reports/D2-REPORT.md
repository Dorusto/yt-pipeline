# D2 — router + LLM director + render, recap from a brief

Date: 2026-10-14, branch `d2-director`. **Checkpoint report** — all eleven D2
tasks are delivered; the ~80% checkpoint was notified on 2026-10-13.

Delivered: the five D1b recap-feedback tasks, then the D2 proper — the router,
the LLM director loop (screenwriter / editor / reviewer), the final render from
the originals, the Kdenlive export adapter. `recap_d2_720p.mp4` (92.5 s) and
`recap_d2_final.mp4` (1920x1080) are rendered with the S1 brief; `shotlog.html`
is the phone contact sheet.

## Recap comparison (D1b vs D2)

Brief: *about 90 seconds, chronological, calm start, more energy towards the
end, ambient sound only*. Source: the same 21 DJI clips.

| | D1b (`recap_d1b_720p.mp4`) | D2 (`recap_d2_720p.mp4`) |
|---|---|---|
| Length / slots | 81.2 s / 28 slots | **92.5 s / 15 slots** |
| Who picked | rule-based director on the shotlog | **LLM director** (screenwriter → editor → reviewer, 3 rounds), rule-based as fallback |
| Selection signal | fused shotlog score | shotlog menu of the 40 **cuttable** windows + a story plan (4 beats: casting off → fjord → caves → ashore) |
| Jump cuts | 3 move→own-landing pairs (0060, 0080, 0081) | **0** — phrases kept through their landing |
| Same-take adjacency | 5 adjacent non-contiguous pairs (0081 ×4) | **0** — a different take between same-take slots |
| Best shots | cave + tunnel present, 0091 orbit only 2.5 s | **tunnel v10, cave v12, cliffs v14, chapel v16, orbit v18 at 8 s each** |
| Hero lengths | pacing 2.5–6 s | **hero / semantic moves keep their window up to 8 s** (`--important-max-s`) |
| Junk take 0051 | absent | **absent** (geometry `accidental` never becomes a slot) |
| Descriptions | GLM on every slot | GLM + **one-line summary and tags** on every slot |
| Review artifact | `shotlog.json` only | **`shotlog.html`** contact sheet (90 cards, thumbnails, hero badges) |
| Output | 720p from proxies | **720p preview + 1920x1080 final from the originals** |
| Kdenlive | not exported | **`fragments.yaml` → `recap_d2.kdenlive`** (melt validation OK) |
| LLM spend | 0.13 USD | **0.64 USD** of the 3.00 USD phase budget (108 vision + 78 director calls) |

Rule-based baseline on the same brief (the `--dry-run` comparison): 16 slots,
92.6 s — the LLM edit is one slot shorter and tells a story instead of filling
buckets, but both land on the same length.

## Works (verified in D2)

- **Move + landing phrases and no same-take jump cuts** (`director.py`,
  435→460 tests). A move and its landing hold are one phrase: the out point
  lands inside the hold (floor `hold.start + 0.75 s`), the head is trimmed for
  pacing, and the phrase competes with the hold alone on the **move's** score.
  Adjacent same-take slots must be source-contiguous or they merge; a better
  candidate replaces a weaker same-take slot. Verified on the real workspace:
  0 phrase violations, 0 adjacent same-take pairs, 0051 out.
- **Hero and semantic moves keep their range up to 8 s** (`--important-max-s`).
  The 0091 orbit went from 2.5 s (Doru's "shows only its start") to 8 s; the
  0072 tunnel, 0080/0081 cave approaches, 0082 cliffs and 0084 chapel each
  keep their whole window or 8 s. Ordinary slots keep the pacing.
- **`describe` summary and tags** — `summary` ≤ 12 words and a short lowercase
  tag list per segment (schemas + prompt + `index` carry both into the shotlog).
  Re-ran `describe` on all 21 clips: 88 segments, every summary ≤ 12 words,
  0.063 USD.
- **`shotlog.html` contact sheet** — one self-contained file in the workspace,
  90 cards in score order with a base64 thumbnail, the time range, summary,
  tags, score, hero badge and eligibility; 1.3 MB, no external URLs. Generated
  only in the workspace, never committed.
- **`eval_labels.py --labels {auto,shotlog,geometry}`** — scores the final
  shotlog label (the verdict where the geometry was uncertain). On the boat
  ground truth: geometry 0.66 (unchanged baseline), shotlog 0.61 — **move 0.53
  → 0.67** (the verdicts help) and role 1.00 → 0.14 (the verdict re-labels
  0051's junk take a `move`; see the known issue below).
- **`router.py` + `schemas/route.schema.json`** — the brief is parsed (duration,
  order, pacing, music file, ambient-only), the content type is detected from
  the brief or the workspace, and `route.json` lists every station with
  `enabled`, `settings` and a reason. The S1 brief routes to vacation with
  beats off (ambient only); tutorial/presentation/talking-head turn on
  graphics, chapters and shorts.
- **`llm_director.py`** — screenwriter (`shot_plan.json`, 4 beats and 15 slots
  with reasons), editor (exact cut windows), a **code validator** (phrase,
  landing, same-take, similar, keep-whole, window) and a reviewer that sends
  fixes back, max 3 rounds. Model and budget from config, prompts in
  `prompts/`, the rule-based director as fallback and `--dry-run` baseline.
  On the real workspace: 92.5 s, 15 slots, **zero grammar violations**, 0.05
  USD per full run.
- **`render.py --originals-dir`** — the final renders from
  `~/media/yt-test/raw` at the sources' native 1920x1080, read-only (mtimes
  unchanged), next to the 720p preview from the proxies. `--out` inside the
  originals folder is refused.
- **`edit_to_fragments.py`** — `edit.json` → `fragments.yaml` with
  frame-inclusive out timecodes; `kdenlive_from_fragments.py` opens it
  (15 fragments, 15 AV groups, melt validation OK).
- **`audio_clean.py`** — measures a clip's ambient sound (per-2s-window RMS,
  wind band ratio, stationary noise floor, one-word verdict) and cleans it with
  a wind high-pass, `arnndn`/`afftdn` and `loudnorm`, reporting before and
  after. On 0080's ambient: `windy+noisy` → `noisy`, wind ratio 0.84 → 0.23.
- **`beats.py`** — pure-numpy onset detection and autocorrelation tempo on a
  music file (no new dependency), downbeats at 4/4, an energy curve and a
  `cut_grid` whose slot lengths land on whole beats. 120 BPM fixture detected
  as 120.03 BPM with the grid within 15 ms of the beat lines.

## Mocked / not verified

- **`audio_clean` applied to the render's ambient mix** — the tool works
  standalone (on the recap's own audio: `windy+noisy` → `noisy`, wind ratio
  0.67 → 0.25, RMS −11.2 → −19.0 dBFS) but is not yet wired into `render.py`,
  so `recap_d2_720p.mp4` still carries the raw mix (mean −10.1 dB, peak
  −0.4 dB).
- **Beat-aligned cuts in the director** — `beats.py` detects tempo, beats,
  downbeats and energy and writes the cut grid, but `director.py` /
  `llm_director.py` do not consume it yet. The S1 brief has no music file, so
  the deliverable does not exercise it either.
- **`arnndn` (RNNoise)** — no `.rnnn` model on this machine, so the real
  `arnndn` pass is unexercised; the tool falls back to `afftdn` with a warning
  and refuses `--denoiser arnndn` cleanly when no model is given. RNNoise is
  mono — multi-channel `arnndn` behaviour is untested.
- **The LLM reviewer does not approve** (`approved: false` after 3 rounds).
  Its objections are concrete and mostly about the `keep_whole` rule (below);
  the kept draft is the one the code validator cleared.
- **Per-mode score weights** — the router writes a `weight` per content type
  but `index.py` still fuses 0.55/0.45 for everything.
- **Local vision backend** (Qwen3.8 on the 3090) — never run here (no GPU).
- **Kdenlive itself** — the project is validated with melt's XML check and
  opens as a file, but no human has opened it in the Kdenlive GUI.

## Doru, check this

1. Watch `~/media/yt-test/workspace/recap_d2_720p.mp4` (92.5 s) next to
   `recap_d1b_720p.mp4` (81 s) — are the tunnel, cave, cliffs, chapel and the
   8 s closing orbit where you wanted them, and is the pacing (calm open,
   full-length heroes at the end) right?
2. Open `~/media/yt-test/workspace/shotlog.html` on the phone — can you now
   tell which shots are hero and what the director had to choose from?
3. `~/media/yt-test/workspace/recap_d2_final.mp4` — 1920x1080 from the
   originals; is the quality jump worth the 3.5x render time?
4. **The keep_whole conflict:** the reviewer wanted the closing orbit (0091)
   at ~5 s and the chapel walk at ~5.5 s; the rule keeps them at 8 s ("2–2.5 s
   is too short for a stable, important move"). Which do you want — the rule,
   the reviewer's taste, or a shorter cap (`--important-max-s 5`)?
5. `.venv/bin/python scripts/eval_labels.py --ground-truth
   eval/ground_truth_boat_2026-10-12.yaml --workspace ~/media/yt-test/workspace
   --labels shotlog` — move 0.67 (up from 0.53) but role 0.14 because the
   vision calls 0051's junk take a `move`. The director already refuses it;
   should `describe` also refuse to rescue a geometry `accidental`?
