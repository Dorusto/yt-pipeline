# D2-20 report — explain motion defects from the content, not from the camera alone

## Works

- `motion.py` writes `subject_ratio` (centre mean / periphery mean over the steady rows, null when undefined) and `subject_centred` (same 0.7 threshold as `_subject_is_centred`) on every move; the orbit_candidate assignment is unchanged. Both are OPTIONAL in `schemas/motion.schema.json` — old motion files still validate. 4 new tests in `tests/test_motion.py`.
- `index.py explained_defects(defect_names, move, semantic_motion)` returns the motion defects (`shake`, `horizon`) the content explains: the vision `semantic_motion` agrees with the move's geometric label (`orbit`↔`orbit_candidate`, `travel`↔`travel_forward`/`travel_backward`), or the move has `subject_centred: true`. Nothing is explained when `blur` is measured; obstruction and exposure are never explained. The agreement table is a module constant.
- Explained defects skip the label penalty and the `unsteady`/`very_unsteady` word with them, so shake counts once; when every defect is explained the clean `+0.10` bonus applies. With nothing explained the score is byte-identical to before (the existing golden-value tests pass unchanged).
- Evidence lands in `shot["intent"]` (`[{"defect", "because"}]`, optional in `schemas/shotlog.schema.json`) and `_reason` appends `intent: shake (orbit)`. `explain.html` shows `intent — shake: semantic_motion orbit matches orbit_candidate` next to each slot's reason.
- Consumer check (step 5): `llm_director.py` and `explain.py` do not recompute a defect penalty. `director.py score_candidate` (the no-shotlog fallback) does, and now skips the names in the candidate's `intent` plus the unsteady word with them; the candidate carries `intent` from the shotlog when one matches.
- Full suite green: **1174 passed**. Task LLM spend: **0** (no model calls; the real check is CPU only).
- Real check on kotor-demo (backup in `~/media/projects/kotor-demo/.backup-d2-20/`, `motion.py --force` then `index.py --force`, LLM cost 0):

  | shot | ratio | centred | semantic | explained | score before | score after |
  |---|---|---|---|---|---|---|
  | `0091/s1` | 0.25 | true | none | horizon, shake | 0.343 | **0.613** |
  | `0091/s2` | 0.39 | true | orbit | shake | 0.490 | **0.715** |

  `0091/s2` is explained by agreement (`semantic_motion orbit matches orbit_candidate`); `0091/s1` by `subject centred (ratio 0.25)` — the selfie hold keeps its face fixed in frame while the boat shakes the background.

  Every shot whose score changed by more than 0.001 (7 of 109):

  | key | before | after | intent | measured defect |
  |---|---|---|---|---|
  | `0060/s1` | 0.512 | 0.737 | subject centred (ratio 0.45) | shake 0.096 |
  | `0072/s3` | 0.347 | 0.546 | subject centred (ratio 0.65) | shake 0.036 |
  | `0081/s1` | 0.498 | 0.687 | subject centred (ratio 0.56) | horizon -9.6 deg |
  | `0082/s5` | 0.523 | 0.682 | subject centred (ratio 0.29) | horizon 7.6 deg |
  | `0082/s6` | 0.677 | 0.852 | subject centred (ratio 0.54) | horizon -8.7 deg |
  | `0091/s1` | 0.343 | 0.613 | subject centred (ratio 0.25) | shake 0.055, horizon -7.2 deg |
  | `0091/s2` | 0.490 | 0.715 | semantic orbit matches orbit_candidate | shake 0.065 |

  14 other shots with shake/horizon stay penalised (no evidence). Two of the seven changed look like they might be real defects despite the evidence: `0060/s1` (shake 0.096 is the strongest of the set; the tilt_up move does hold a subject at ratio 0.45) and `0081/s1` (horizon -9.6 deg is a strong tilt, explained by a centred subject on an orbit_candidate). They are listed for review, not re-penalised — the rule is general and has no per-shot exceptions.

## Mocked / not verified

- The unit tests are synthetic (`tmp_path` rows and moves); only the kotor-demo numbers above are real footage.
- `subject_centred` is the geometric centre-vs-periphery residual, not face detection: a centred rock or a wall can read as "subject". The vision `semantic_motion` agreement is the other path and does not depend on it.
- The `accidental` role from geometry still cannot be corrected by vision (no real case yet) — unchanged and still open.
- `serve.py` (Shots tab) does not show `intent` separately; it inherits the `intent: …` note through the reason string. Only `explain.html` has a dedicated line.

## Doru, check this

1. `python3 -c "import json; s=[x for x in json.load(open('/root/media/projects/kotor-demo/shotlog.json'))['shots'] if x['key'].startswith('0091')]; print(*[(x['key'], x['score'], x.get('intent')) for x in s], sep='\n')"` → `0091/s1` about 0.61, `0091/s2` about 0.71, both with `intent` filled.
2. Compare `~/media/projects/kotor-demo/.backup-d2-20/shotlog.json` with the new `shotlog.json` — 7 scores moved, listed in the table above.
3. `0060/s1` and `0081/s1` — the two flagged rows. If those are real defects for you, the evidence threshold (subject ratio 0.7) is where to tune, not a per-shot rule.
4. `python scripts/explain.py --workspace ~/media/projects/kotor-demo` then open `explain.html` — the `intent — …` line should sit under the reason of each affected slot.
5. `docs/RULES.md` new line "Intent before penalty (D2-20)" and the DECISIONS entry "Understand intent before penalising a motion defect (D2-20)".
