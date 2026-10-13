# D1b vision bake-off — DeepSeek V4.1 Flash vs GLM 5.3 Flash

Date: 2026-10-13. Purpose: pick the default vision model for `describe` (VISION
§6, "Vision, now … chosen by the D1 bake-off"). Run by the architect on the
Kotor boat proxies through `scripts/describe.py`, same prompt
(`prompts/describe_segment.md`), same three frames per segment, same workspace.

## Setup

| | |
|---|---|
| Clips | 5 varied: **0067** stern wake + strait, **0072** tunnel transit + lateral slide, **0081** sea cave + orbit opening, **0083** locked-off with an approaching boat, **0091** orbit around the group |
| Segments | 32 kept (all quality segments of the five clips; the set contains no confident setup/teardown/accidental) |
| Of which `uncertain` | 8 — every one carries a verdict |
| Frames | 3 per segment, interior spacing `(i+0.5)/3`, resized to 640 px, JPEG 85 |
| Budget | 3.00 USD phase cap via `llm_spend.json`; actual spend well below |
| Runs | `scripts/describe.py --clips 0067,0072,0081,0083,0091 --model <id> --backend openrouter --force --json` |

Raw outputs per model: `~/media/yt-test/workspace/bakeoff_deepseek/` and
`~/media/yt-test/workspace/bakeoff_glm/` (outside the repo — no frames, no
footage, ever committed).

## Cost and time per model

| | deepseek/deepseek-v4.1-flash | z-ai/glm-5.3-flash |
|---|---|---|
| Segments described / skipped | 30 / 2 | **32 / 0** |
| Parse failures (`content: null` or unparseable) | 2 | 0 |
| Wall time (32 segments) | 702 s | 665 s |
| LLM cost (32 segments) | **0.0590 USD** | **0.0066 USD** |
| Cost per described segment | 0.0020 USD | **0.0002 USD** |
| Output tokens per call | up to 8000 (burned on reasoning) | ~700–1100 |
| Needs `max_tokens` | **8000** — at 3000 the reasoning trace eats the whole budget and `content` comes back `null` | fine at 3000 |

Phase spend after both runs, debug calls and the earlier crash: 0.109 USD of
the 3.00 USD budget (`~/media/yt-test/workspace/llm_spend.json`).

**DeepSeek reliability note.** `deepseek/deepseek-v4.1-flash` is a reasoning
model: a single segment produced 13–25 k characters of reasoning before the
answer. At the original 3000-token cap it returned `content: null` on half the
segments (the run crashed before the `None`-guard landed; after the guard those
segments became `unparseable model output` and were skipped). At 8000 tokens it
still failed 2 of 32 — including **0067 s1**, the wake segment whose verdict is
the whole point of the D1b uncertainty work.

## Side by side — the uncertain segments (verdicts)

Ground truth is Doru's `eval/ground_truth_boat_2026-10-12.yaml` (times in the
source clip).

| segment | geometry said | ground truth | DeepSeek verdict | GLM verdict | better |
|---|---|---|---|---|---|
| **0067 s1** 0.0–35.4 (wake + strait) | `complex_motion` uncertain | `travel_backward` 00:00–00:30 | **failed** (unparseable) | `travel_backward` 0.8 — "the shores and mountain converge to a vanishing point and steadily recede… the boat's own wake is not camera flow" | **GLM** |
| **0067 s3** 43.6–45.0 | `pan_right` uncertain (role conflict) | `pan_right` 00:44–00:55 | `travel_backward` 0.7 | `pan_right` 0.7 — "one continuous rightward sweep… the wake is the boat's, not the swing" | **GLM** |
| **0067 s5** 54.0–54.9 | `pan_left` uncertain | tail of `pan_right` | `travel_backward` 0.75 | `travel_backward` 0.7 | tie (both plausible on a 0.9 s sliver) |
| **0072 s9** 165.8–175.9 | `truck_right` uncertain | `truck_right` 02:46–02:55 | `travel_forward` 0.7 | `travel_forward` 0.7 | tie (both wrong) |
| **0081 s3** 9.8–11.7 | `complex_motion` uncertain | part of `orbit` 00:00–00:20 | `pan_right` 0.8 | `hold`/`minor_motion` 0.6 | tie (short slice) |
| **0081 s7** 30.1–34.2 | `pan_right` uncertain (parallax band) | `travel_forward` 00:20–00:40 | `truck_right` 0.7 | `travel_forward` 0.7 — "the cliff and cave mouth expand… parallax from the boat moving toward the cave" | **GLM** |
| **0081 s15** 74.3–108.3 | `complex_motion` uncertain | `travel_backward` 01:10–01:35 | `complex_motion` 0.8 (keeps geometry) | `travel_backward` 0.65 — "the cliff and cave shrink toward a vanishing point" | **GLM** |
| **0072 s9** covered above | | | | | |

Score on the six segments with a clear ground truth: **GLM 4 correct / 1 wrong
/ 1 tie, DeepSeek 1 correct / 2 wrong / 1 tie / 2 failures.** The two DeepSeek
failures are the highest-value adjudications (the wake and the cave pull-back).

## Side by side — content scoring (interest, hero)

Doru's S1 review named the missing best moments: *orbit 0091, approaching boat
0083, cave 0081, tunnel 0072*.

| clip / segment | DeepSeek interest / hero | GLM interest / hero | Doru's list |
|---|---|---|---|
| 0072 s5 tunnel approach | 55 / no | 65 / **hero** | tunnel ✓ |
| 0072 s7 tunnel transit | 65 / no | 72 / **hero** | tunnel ✓ |
| 0081 s5 cave approach | 55 / no | 70 / **hero** | cave ✓ |
| 0081 s9 into the cave | 55 / no | 65 / **hero** | cave ✓ |
| 0081 s13 into the cave | 45 / no | 72 / **hero** | cave ✓ |
| 0083 s1 approaching boat | **failed** | 50 / no | boat — neither flags it |
| 0091 s2 orbit | 55 / no | 50 / no | orbit — neither flags it |

GLM flags the tunnel and the cave as hero shots and scores them at the top of
the range (65–72); DeepSeek never sets `hero: true` on any of the 32 segments
and compresses interest into 25–65. Neither model spontaneously flags the
0091 orbit or the 0083 approaching boat as hero — that is what the S1 review
wants, so it stays a known gap for the director's content score (it can also
use `semantic_motion: orbit` and the scene events).

## Description quality (spot check)

Both models write fluent, accurate English. GLM is consistently more specific
and notices what happens *between* the frames; DeepSeek is more generic and
more willing to overwrite confident geometry.

- 0067 s3: GLM "a fast-moving boat… sweeps rightward from a green shoreline…
  ending on a red-and-white excursion boat cruising off the starboard side" vs
  DeepSeek "a ferry appears on the right in the final frame".
- 0072 s7: GLM "a boat travels forward into a dark, graffiti-covered concrete
  tunnel… then emerges through the far mouth" vs DeepSeek "a boat moves forward
  through a graffiti-lined concrete canal and dark tunnel".
- 0091 s1: GLM "a finger briefly covers the lens mid-segment" (it does) —
  DeepSeek does not mention it.

## Decision

**Default vision model: `z-ai/glm-5.3-flash`** (config `models.vision`).
`deepseek/deepseek-v4.1-flash` stays available as `models.vision_fallback` for
segments where a more forceful second opinion helps.

Reasons: 100% parse rate vs 94%, the correct verdict on the wake segment that
motivated the whole uncertainty work, correct verdicts on 0081 s7 and s15, hero
flags that match the moments Doru named, 9x cheaper and no reasoning-token
landmine. DeepSeek's reasoning budget is a maintenance hazard: it needs 8000
output tokens per segment and still loses the answer sometimes.

Recorded in `docs/DECISIONS.md`. `config.yaml.example` and the local
`config.yaml` updated to the winner.
