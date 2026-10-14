# D2-2 — the four fixes Doru asked for after watching `recap_d2_720p.mp4`

Date: 2026-10-14, branch `d2-2-fixes`. Budget: **0.32 USD** of the 1 USD
(28 calls: 9 vision for the 0072 beats, 19 director for three loop runs — the
first was killed by a too-short timeout, the second stopped on the cumulative
ledger budget, see Mocked). Deliverable: `recap_d2_720p.mp4` re-rendered with the
S1 brief, the 0072 tunnel approach now in the edit.

## Works (verified in D2-2)

- **Content beats for long takes** (`describe` + `index` + both directors,
  schemas, prompt). A segment longer than 20 s is sampled one frame every ~5 s
  and the vision model splits it at the scene changes; each beat is its own
  shotlog candidate (`0072/s7b1`..) with the parent's labels and its own
  summary/interest/hero. On 0072's 134 s transit (s7, 28.48–162.72) the model
  found 6 beats — `approach` (hero, score 0.934), `enter`, `inside`,
  `passengers`, `exit`, `emerge` (hero, 0.961) — and both ends are eligible.
  The directors build one cut window per beat (beat range ∩ the move's steady
  window). Tests: `test_two_beats_of_one_take_with_another_take_between` (score
  order takes both with another take between), beat tiling/normalisation,
  one-shot-per-beat in the shotlog, per-beat facts in the LLM director.
- **The same-take rule now checks the finished slot's window** — it used to
  check the candidate window before trimming, so a trim could create a jump
  cut. Verified on the real workspace: 0 non-contiguous same-take adjacencies.
- **Important-shot cap 6 s + windows end at the steady motion** (both
  directors + editor/screenwriter/reviewer prompts). `--important-max-s`
  default 8.0 → 6.0; a move's window ends at `steady_end_s`, never on the
  aimless movement after it. The closing orbit (0091) went from 8 s to
  6.88–12.88 (6.0 s), the chapel walk to 6.0 s. The reviewer prompt states the
  same rules so it can approve.
- **Geometry `accidental` is final** (`index.py`). A junk take keeps its role
  whatever the vision verdict says and is never eligible. On the boat ground
  truth: `eval_labels.py --labels shotlog` role **0.14 → 1.00**, move 0.68,
  overall 0.75 (0051 predicted `accidental`, not `move`).
- **`audio_clean` wired into `render.py`** on the ambient bus (shared chain in
  `scripts/common/ambience.py`): wind `highpass` + `arnndn` (RNNoise) +
  `loudnorm`, on by default (`--no-audio-clean` for the raw mix, plus
  `--denoiser` / `--model` / `--target-lufs` / `--wind-hz`); `--json` reports
  `audio_clean: {enabled, denoiser, filters}`. The `.rnnn` model is
  `marathon-prescription` from `GregorR/rnnoise-models` (rnnoise-nu format,
  models not subject to copyright per that repo's README; compared with
  `dbklim/RNNoise_Wrapper` and the gone `werman/noise-suppression-rnnoise` —
  see DECISIONS) at `~/.cache/yt-pipeline/models/arnndn.rnnn`, sha256
  `4e84a448…`. Rendered clean: `highpass, arnndn, loudnorm`.
- **Recap re-rendered.** `~/media/yt-test/workspace/recap_d2_720p.mp4` — 93.4 s,
  15 slots, 1280x720, ambient cleaned. The **0072 approach is in the edit**: v7
  (source 22.96–28.48, the dolly-in to the tunnel mouth through its landing)
  plays at 40.4–45.9 s. Frame measurement: the recap frame at v7's midpoint
  correlates 0.874 with the source approach frame and 0.042 with a wrong
  source moment (inside the tunnel). The screenwriter plan (after the prompt
  fix) even covers the whole tunnel arc — approach beat + emerge beat — and the
  kept draft is validator-clean (0 violations).

## Mocked / not verified

- **How the cleaned ambience sounds** — the chain runs (filters reported,
  wind ratio measured by `audio_clean`) but no human listened; the raw D2 mix
  is kept as `recap_d2_720p_pre_d2_2.mp4` for an A/B.
- **The LLM reviewer still does not approve** after 3 rounds (same as D2); the
  kept draft is the one the code validator cleared (0 violations). Its taste
  objections are in the run log if needed.
- **Strict-chronological rule-based director** can still keep only one beat of
  a take (the same-take upgrade keeps the better one); the approach+exit arc
  with another take between is available to the LLM loop and to `--order
  score`, which is what "may use both with another take between" means.
- **The 1920x1080 final from the originals** was not re-rendered in this round
  (the 720p preview was the deliverable); the clean chain applies to it the
  same way.
- **Local GPU vision backend** — unchanged, never run here.

## Doru, check this

1. `~/media/yt-test/workspace/recap_d2_720p.mp4` — jump to **~40 s** (the
   tunnel approach, v7) and to the last shot (the orbit, now 6 s instead of
   8 s): is the approach what you wanted, and is the closing shot right?
2. A/B the sound: `recap_d2_720p.mp4` (cleaned: wind high-pass + RNNoise +
   loudnorm) vs `recap_d2_720p_pre_d2_2.mp4` (the raw D2 mix) — better or too
   processed?
3. `~/media/yt-test/workspace/shotlog.html` — the 0072 transit is now 6 cards
   (approach / enter / inside / passengers / exit / emerge) instead of one.
4. `.venv/bin/python scripts/eval_labels.py --ground-truth eval/ground_truth_boat_2026-10-12.yaml --workspace ~/media/yt-test/workspace --labels shotlog` — role 1.00 (was 0.14).
5. Budget: 0.32 USD of the 1 USD — `python3 -c "import json; d=json.load(open('/root/media/yt-test/workspace/llm_spend.json')); print(round(sum(r['cost_usd'] for r in d['entries'] if str(r['ts']).startswith('2026-10-14')),4))"`.
