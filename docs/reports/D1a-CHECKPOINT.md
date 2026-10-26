# D1a checkpoint — the owner's review (2026-10-13)

Ground truth from the owner for 6 clips is in [`eval/ground_truth_boat_2026-10-12.yaml`](../../eval/ground_truth_boat_2026-10-12.yaml). Verdict: the tools work end to end, but the labels miss what matters on footage shot from a moving boat. D1a is not approved yet; one more round (D1a-2) with the fixes below, then a short re-check, then D1b.

## What the comparison shows

| Clip | Truth | Tools said | Lesson |
|---|---|---|---|
| 0081 00:20–01:35 | boat enters a cave, holds, backs out; camera fixed on the boat, boat bobs | `tilt_down` 00:22–00:52, `hold`, `complex_motion` 01:00–01:48 | **Platform motion is not camera operation.** Wave bob reads as tilt; travel reads as dolly or complex. |
| 0072 00:31–02:43 | travel through a tunnel on the boat | one `dolly_in` of 2 min | Geometrically right, semantically incomplete: it is `travel_forward` on a boat, with internal events. |
| 0072 02:46–02:55 | lateral slide (truck right) with parallax | `pan_right` | **Pan vs truck** needs a parallax check. |
| 0091 00:05–00:12 | orbit | `pan_left` 00:03–00:13 | Timing is good; **orbit** = pan + strong parallax, subject stays centred. |
| 0067 00:00–00:54 | level horizon, moving away with parallax | `hold`, **horizon 1.00** on most segments | **Horizon detector gives false positives** (mountain slopes and wake lines, not the sea line). The D1a report's "cross-check" used the same kind of edges, so it confirmed the error. |
| 0083 | locked-off hold; another boat enters, approaches, nearly exits | `hold` 39 s, nothing else | Camera label right, but **events inside a hold** are invisible. |
| 0051 | junk | several `move`s, shake ~1.0 | Whole-clip junk should be `accidental`. |

## D1a-2 work list (in this order)

1. **Eval tool first** — `scripts/eval_labels.py`: reads a ground-truth YAML like the one above plus the workspace outputs, scores per clip (interval overlap of expected vs predicted move/role, horizon false positives, event recall) and prints a table; `--json`. Baseline the current tools, then every fix below must move the score. Own skill, tests with a tiny fixture.
2. **External audit findings on PR #7:** setup/teardown only before the first / after the last `move`/`hold`; merge sub-second slivers and enforce `end_s > start_s` in the schema; spend ledger safe across concurrent processes (`fcntl.flock`, re-read before check); report typo, `move · static` contradiction, commit timezone `+02:00`.
3. **Platform motion** (`motion`): detect periodic low-frequency tilt/roll oscillation (wave bob, period ~1.5–8 s) and report it as `platform_bob` with amplitude, not as `tilt_up/down`; label sustained forward/backward scale flow as `travel_forward` / `travel_backward` with `platform: vehicle` instead of `dolly_in/out` when it lasts long and is smooth. Keep the CameraBench label as a secondary field.
4. **Parallax** (`motion`): homography-fit residual (pure rotation fits a homography; translation with depth does not) → `parallax` score per segment; use it to separate `pan` from `truck`, and to flag `orbit_candidate` (pan + strong parallax + stable centred subject region). D1b's vision step confirms orbit; motion only proposes it.
5. **Horizon** (`quality`): measure only from a dominant long near-horizontal line (sea/sky boundary); if none is found, report `null`, not a value. Acceptance: 0067 00:00–00:30 reports level.
6. **Scene activity** (`motion` or `quality`, architect decides): residual local flow after removing global motion → per-second `scene_activity` and detected events (subject enters / approaches / leaves). Acceptance: 0083 shows activity starting ~00:10 and a peak near 00:32–00:38.
7. **`accidental`** (`quality`): a whole clip (or long run) with very high shake and no stable move or hold → `accidental`. Acceptance: 0051.
8. Re-run all tools on all clips, re-run the eval, update `D1a-REPORT.md` with the before/after eval table and the new timelines for the 6 ground-truth clips.

## Decisions taken at this checkpoint

- Docstrings are allowed; inline comments are not (CLAUDE.md rule 5 updated).
- `raw/` originals stay: they carry the DJI `djmd` metadata stream the proxies lost; a gyro/telemetry path will read them later (`telemetry-parser`).
