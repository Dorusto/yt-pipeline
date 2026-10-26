# S1 — first end-to-end recap of the Kotor boat footage

Brief: *about 90 seconds, chronological, calm start, more energy towards the
end, ambient sound only*. Source: 21 DJI proxies in `~/media/yt-test/proxy`,
analysed with the D1a tools (`ingest` → `motion` → `quality`). No vision model
was used — every reason in `edit.json` comes from the labels. Phase LLM spend:
**0.00 USD**.

## Deliverable

**`/root/media/yt-test/workspace/recap_720p.mp4`** — 84.9 s (90 s of slots
minus 17 × 0.3 s crossfades), 1280x720, ambient sound from the proxies.

Pipeline used:

```bash
.venv/bin/python scripts/motion.py ~/media/yt-test/proxy --workspace ~/media/yt-test/workspace --force --json
.venv/bin/python scripts/director.py --workspace ~/media/yt-test/workspace \
  --brief "about 90 seconds, chronological, calm start, more energy towards the end, ambient sound only" \
  --target-duration-s 90 --order chronological --pacing calm-to-energy --json
.venv/bin/python scripts/render.py --edit ~/media/yt-test/workspace/edit.json \
  --out ~/media/yt-test/workspace/recap_720p.mp4 --json
```

## Timeline (edit.json, 18 slots)

Clip times are seconds inside the source clip. "why" is the `reason` field the
director wrote from the labels.

| # | clip | in | out | why |
|---|---|---|---|---|
| v1 | 0056 | 0.00 | 5.12 | hold · static · clean · calm opening |
| v2 | 0057 | 0.00 | 6.32 | hold · static · clean · calm opening |
| v3 | 0060 | 26.72 | 30.44 | hold · static · clean · calm opening |
| v4 | 0065 | 0.00 | 7.65 | hold · static · clean · calm opening |
| v5 | 0066 | 3.36 | 15.36 | hold · static · clean · calm opening |
| v6 | 0067 | 35.36 | 42.60 | hold · static · clean · calm opening |
| v7 | 0072 | 22.96 | 25.53 | move · dolly_in · no_shaking · clean · calm opening |
| v8 | 0072 | 106.71 | 112.71 | move · complex_motion · no_shaking · clean · calm opening |
| v9 | 0072 | 173.11 | 175.77 | move · truck_right · no_shaking · clean · calm opening |
| v10 | 0077 | 10.32 | 12.56 | hold · static · clean · calm opening |
| v11 | 0077 | 13.52 | 17.16 | hold · static · clean · calm opening |
| v12 | 0080 | 0.00 | 12.00 | move · travel_forward · no_shaking · clean · neutral |
| v13 | 0080 | 21.68 | 23.86 | hold · static · clean · neutral |
| v14 | 0081 | 30.88 | 34.16 | move · pan_right · no_shaking · clean · neutral |
| v15 | 0081 | 47.12 | 49.93 | move · dolly_out · no_shaking · clean · neutral |
| v16 | 0081 | 75.59 | 79.53 | move · complex_motion · minimal_shaking · clean · neutral |
| v17 | 0084 | 0.00 | 3.52 | move · travel_forward · no_shaking · clean · energy build |
| v18 | 0086 | 3.04 | 6.15 | hold · static · clean · late hold |

Clip 0051 is missing on purpose: `quality` labels the whole take `accidental`
and the director never picks accidental/setup/teardown. Window starts are
handle-in times of the steady ranges (`steady_start − 0.25 s`), so no cut lands
mid-acceleration.

## Works (verified in S1)

- **`schemas/edit.schema.json`** — video slots, audio plan, captions/graphics
  templates; 5 schema tests plus the real `edit.json` validate clean
  (`tests/test_schema.py`, `tests/test_director.py`).
- **Event noise floor** (`scripts/motion.py`) — spans whose peak stays below
  `floor + 0.001` emit nothing; starts are placed at the first sample at least
  0.001 above the floor. Verified by unit tests and a full re-run on all 21
  clips (51.7 s): every start note now reads ≥ 0.0013 (was 0.0000–0.0001 on
  0072/0083), blip spans (0063) gone, 36 events on 21 clips.
- **Event precision in `eval_labels.py`** — one-to-one matching within
  `--event-tol-s`; per-clip `recall`, `precision`, `f1` (the component score is
  F1), `meta.wall_time_s` / `meta.llm_cost_usd`. Unit tests plus the real run.
- **`scripts/director.py`** — rule-based, deterministic, no model: skips
  accidental/setup/teardown, uses handle windows inside steady ranges, calm
  start → energy end, content-time buckets (wall-clock gaps do not eat slots),
  fill pass to the target length. Result: 18 slots, exactly 90.0 s of material.
- **`scripts/render.py`** — one ffmpeg filter graph, 1280x720, 0.3 s crossfades
  (audio `acrossfade`), ambient audio kept, atomic write, re-run safe. Output
  84.9 s verified with ffprobe; frames at 0.1/2.5/6.0 s are clean, the frame at
  4.95 s shows the expected two-scene blend (transition window 4.82–5.12 s).
- 301 tests green (`.venv/bin/python -m pytest -q`).

Eval on the six ground-truth clips, before / after S1:

| | move | role | horizon | events | overall |
|---|---|---|---|---|---|
| D1a-2 (events = recall, match reuse) | 0.53 | 1.00 | 0.97 | 1.00 | 0.69 |
| S1 (events = F1, one-to-one) | 0.53 | 1.00 | 0.97 | 0.67 | 0.66 |

0083's events are now matched 10↔12 s and 32↔35 s (F1 0.67); the old "1.00"
counted one predicted event (35 s) as a match for two ground-truth events.

## Mocked / not verified

- **Vision descriptions** — optional in the brief; `description` is `null` on
  every slot. The 2 USD vision budget was not spent; reasons come from labels.
- **Music, SFX, captions, graphics** — defined in the edit schema and ignored
  by the S1 render (warns when present). Untested beyond fixtures.
- **Final render from originals** — `render.py` cut from the 720p proxies;
  full-quality final is D2.
- **`edit.json` → Kdenlive export** — not in S1 scope.

## Known issues (analysis, carried from D1a)

- 0072 still reports 11 events (4 excursions × start/peak/end). Onsets are
  grounded now, but the count stays high and only 0083 has ground-truth events,
  so precision is measurable on one clip only.
- 0083's peak event lands at 28 s vs ground truth 32 s (4 s > 3 s tolerance).
- 0067 `travel_backward` (stern-facing camera) is still mislabelled — per
  DECISIONS left to D1b vision (or shore-region scale flow).
- 0081 interior horizon false positives and the short orbit — D1b vision veto.

## Labels on the clips without ground truth (overfitting check)

The thresholds were tuned on the six ground-truth clips (0051, 0067, 0072,
0081, 0083, 0091). These are the labels on the other 15, so they can be checked
against what is on screen while watching the recap. Seconds per role, the move
labels the motion tool produced, measured defects and the event count:

| clip | dur | roles (s) | move labels | defects | events |
|---|---|---|---|---|---|
| 0056 | 30.8 | hold 6.1, move 24.6 | complex_motion, pan_right, truck_right | blur, shake | 3 |
| 0057 | 13.8 | hold 6.9, move 6.8 | orbit_candidate, static | — | 0 |
| 0058 | 3.9 | move 3.9 | complex_motion | — | 0 |
| 0060 | 30.4 | move 23.2, hold 7.2 | tilt_down, tilt_up, truck_left | shake | 0 |
| 0061 | 14.2 | move 9.7, hold 4.5 | orbit_candidate | obstruction | 2 |
| 0063 | 9.0 | move 4.0, hold 5.0 | dolly_out | obstruction | 0 |
| 0064 | 7.0 | move 5.7, hold 1.4 | truck_left | shake | 2 |
| 0065 | 19.8 | hold 18.5, move 1.3 | — | horizon | 0 |
| 0066 | 16.2 | move 3.4, hold 12.8 | tilt_up | — | 0 |
| 0071 | 10.2 | hold 2.5, move 7.8 | orbit_candidate | — | 0 |
| 0077 | 17.2 | move 11.3, hold 5.8 | orbit_candidate, pan_right | — | 0 |
| 0080 | 35.4 | move 21.4, hold 13.8 | static, travel_forward | — | 2 |
| 0082 | 30.1 | hold 11.3, move 18.8 | complex_motion, static | horizon | 0 |
| 0084 | 18.8 | move 17.1, hold 1.7 | travel_forward | — | 0 |
| 0086 | 16.6 | hold 15.3, move 1.2 | — | — | 0 |

## the owner, check this

1. Watch `~/media/yt-test/workspace/recap_720p.mp4` (85 s) — is it watchable
   end to end, and does the calm→energy arc read?
2. Compare the timeline table above with what you would have picked — the
   first wrong pick is the most useful signal for D1b/D2.
3. While watching, glance at the labels table for the 15 clips without ground
   truth — any role or move label that is obviously wrong on screen?
4. `.venv/bin/python scripts/eval_labels.py --ground-truth eval/ground_truth_boat_2026-10-12.yaml --workspace ~/media/yt-test/workspace`
   — events 0.67 is F1 (precision+recall), the old 1.00 was recall-only with
   match reuse; 0083's peak is 4 s early.
5. 0072's 11 scene events — real activity or wake noise? (decides how hard to
   push the event detector at D1b)
