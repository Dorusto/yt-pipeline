# D2-11 report: move length policy and vertical 9:16

## Works

- Part 1: `common/moves.apply_move_policy` runs in both directors before the beat snap; `middle_speed` (1.0-1.25) in `edit.json`; render builds a ramp from three trims (audio `atempo` on the middle). Verified by unit, director, render (duration within 0.1 s, audio continuous), serve, explain and export tests.
- Part 2: `render.py` / `recap.py --format 16x9|9x16|both`; 9:16 slots follow the clip's reframe crop path; web Format select, vertical player and served files. Render tests: 720x1280 output, a moving red square stays near the frame centre at 0.3/1.5/2.7 s, `both` gives equal durations, reframe called once per clip and cached, failure falls back to a centred crop with a warning, landscape photo cropped around its subject.
- Full suite: 1006 passed, 1 skipped (`-m "not needs_melt and not needs_footage"`).
- End to end (synthetic 16 clips, 120 BPM click, mock backend, `--format both`, 60 s brief, slot_max 3.93 s): 12 slots, 2 over slot_max (16.7 %), every static hold at most 3.5 s, max cut-to-beat error 0.4 ms, 16:9 1280x720 and 9:16 720x1280, both 37.8 s.

## Mocked / not verified

- Real YuNet face detection: the model cannot download here (HTTP 403). The e2e crop paths came from reframe's real saliency/tracking code with a no-face stub detector; face-following on real footage is unverified.
- Real VLM descriptions (mock backend), MobileNet similarity (hist backend used), real Dolomites footage, the web app on a phone.
- With few slots `ceil(15 %)` allows more than 15 % (7 slots -> 2 = 28 %; 12 slots -> 2 = 16.7 %); the 80-90 % rule holds from about 14 slots up.
- The synthetic target was 60 s but only about 38 s of edit came out (little distinct material); lengths below the target are a footage limit, not a policy effect.

## Doru, check this

- `docs/DECISIONS.md` last two entries (move policy budget for ordinary moves; vertical format).
- Run `scripts/recap.py --source <folder> --brief "..." --format both` on real footage and watch `recap_720p_9x16.mp4`: does the window stay on the subject?
- Web app: Upload -> Format 9:16, then the Recap tab shows the vertical player.
- Kdenlive export of a ramped slot stays real time (warning in the export summary).
- Decide whether ordinary moves may grow to `slot_max` (one line in `apply_move_policy`).
