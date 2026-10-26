---
name: yt-eval
description: Score the workspace motion/ and quality/ outputs, or the final shotlog labels, against a ground-truth YAML written by the person who filmed the clips — per-clip and total scores from interval overlap of expected vs predicted move and role, horizon false positives and event precision/recall (F1). Use when the user asks how well the motion or quality tools match what was actually filmed, wants the eval baseline, wants the pipeline number after the vision verdicts, or needs a before/after number to judge a D1a-2 fix. Trigger phrases - "score the tools against ground truth", "rulează evaluarea", "cât de bune sunt etichetele", "eval baseline", "compară motion/quality cu ground truth", "cât câștigă verdictul", "score the shotlog labels". Labels only — it never edits, cuts or re-analyses footage, and it does not fix the tools.
compatibility: Run with the `yt` entry point on PATH. Needs the repo .venv with pyyaml and jsonschema; CPU only, no GPU and no network. Reads the ground-truth YAML and the workspace read-only; the only file written is --out. Fast (seconds for a handful of clips).
---

# yt-eval

Turn "the labels look plausible" into a number. The ground-truth YAML holds the move, role, horizon and event labels a human wrote from what they filmed; this tool matches every ground-truth clip to the workspace `motion/<stem>.json` and `quality/<stem>.json` by the 4-digit counter in the file name and scores interval overlap, horizon false positives and event precision/recall. `--labels` chooses which labels are scored: `geometry` (the motion/quality documents, the D1a-2 baseline) or `shotlog` (the effective labels of `WORKSPACE/shotlog.json`, where the vision verdict wins on uncertain segments — the pipeline number). It baselines the current tools: every later D1a-2 fix must move the score up. All commands run through `yt <tool>`.

## Step 1: score a ground-truth file against a workspace

```bash
yt eval_labels --ground-truth eval/ground_truth_boat_2026-10-12.yaml --workspace <workspace>
yt eval_labels --ground-truth eval/ground_truth_boat_2026-10-12.yaml --workspace <workspace> --labels shotlog
```

- `--ground-truth PATH` (required) is the YAML below. `--workspace DIR` (required) holds `motion/*.json` and `quality/*.json`.
- `--labels` (default `auto`) picks the label source: `auto` uses `WORKSPACE/shotlog.json` when it exists and geometry otherwise; `shotlog` always scores the shotlog's effective `role` / `motion_label` and exits 2 with an `[error]` line when `shotlog.json` is missing; `geometry` always scores the motion/quality labels and ignores any shotlog. Every clip's `notes` names the source that was scored (`labels: geometry` or `labels: shotlog (N of M segments by verdict)`).
- The workspace is read only; the source clips are never touched. A missing file for a clip is scored 0 with a note (`missing motion output` / `missing quality output`), it does not abort the run.
- `--horizon-tol-deg` (default 5.0) is the `|horizon_deg|` at or below which a quality segment claims a level horizon. `--event-tol-s` (default 3.0) is how close a predicted event must be to count as matched; matching is one-to-one (a predicted event matches at most one ground-truth event and the other way round), closest pairs first.
- Exit code 0 means the report was produced, even when every score is 0. Exit 2 means the ground-truth or workspace is missing/unreadable, the YAML does not parse, or `--labels shotlog` was asked without a shotlog; exit 3 means `--out` could not be written; one `[error] ...` line goes to stderr. It never asks questions.

## Step 2: read the result

```bash
yt eval_labels --ground-truth GT.yaml --workspace WS --json
yt eval_labels --ground-truth GT.yaml --workspace WS --out report.json
```

The default prints a fixed-width table, one row per ground-truth clip in ground-truth order, then a TOTAL row; `-` marks a component the ground truth does not ask for (never zero). `--json` prints the full report object (indent 2) instead — including `meta` with `wall_time_s` and `llm_cost_usd` (always 0.0 here, the tool calls no model) — and `--out FILE` additionally writes that object atomically (`.tmp` then `os.replace`), re-run safe and overwriting. The report is always validated against `schemas/eval.schema.json` before it is printed or written.

## Ground-truth YAML format

```yaml
clips:
  "0051":
    summary: Unusable clip, makes no sense.
    segments:
      - {start: "00:00", end: "00:22", expect_role: accidental}
  "0067":
    summary: Behind the boat, moving away from the mountain.
    segments:
      - {start: "00:00", end: "00:30", expect_move: travel_backward, platform: boat, horizon: level, note: "..."}
  "0083":
    segments:
      - {start: "00:00", end: "00:39", expect_move: hold, platform: boat, note: "camera fixed"}
    events:
      - {at: "00:10", what: "faster boat enters frame and slowly approaches"}
```

- The clip key is the 4-digit counter; a segment may carry `expect_move`, `expect_role`, `horizon: level`, `platform` and `note`. Only the fields present are scored — `platform` and `note` are context.
- `start`, `end` and `at` are `MM:SS` (`"01:35"` = 95 s) or `HH:MM:SS` (`"01:02:03"` = 3723 s).
- `events` (point times) score against `events[].t_s` in the motion output (scene events detected from the activity series).

## How the scores are computed

- **move** — per `expect_move` segment, seconds of the expected interval covered by a predicted `moves[]` with full label credit count 1.0, geometric-family labels (`travel_forward`←`dolly_in`, `travel_backward`←`dolly_out`, `truck_left`←`pan_left`, `truck_right`←`pan_right`, `orbit`←`pan_left`/`pan_right`) count 0.5, everything else 0.0. `hold`≡`static` and `orbit`≡`orbit_candidate` are full credit. A `phases[]` entry of type `still` is a predicted `static` (so it satisfies `expect_move: hold` at full credit); where a move interval and a still phase overlap, the move wins and those seconds are counted once with the move's credit. Segment score = credit-weighted seconds / span; clip score = mean over its move segments. With `--labels shotlog` the predicted intervals are the shotlog `shots[]` instead — one `[start_s, end_s]` per shot labelled with its effective `motion_label` — and the same credit math applies, so the two numbers are comparable.
- **role** — same overlap against quality `segments[]`, full credit only when `role == expect_role` (no family credit). With `--labels shotlog` the predicted roles are the shotlog `shots[]` effective `role` values over the same intervals.
- **horizon** — per `horizon: level` segment, a quality segment claims *tilted* when it has a `horizon` defect or `metrics.horizon_deg` is non-null and above tolerance, *level* when non-null and within tolerance, and *abstains* when `horizon_deg` is null with no defect. Segment score = `level_rate * (1 - fp_rate)`. Reporting `null` everywhere is not a false positive but still scores 0 — abstention is not success. Horizon and events are measurements, not labels, so `--labels` does not change them.
- **events** — one-to-one matching of predicted `events[].t_s` to ground-truth event times within `--event-tol-s` (closest pairs first, each side used at most once). `recall` = matched / ground-truth events, `precision` = matched / predicted events (0 when nothing was predicted), the component `score` is their F1. So a clip that fires 11 events to cover 3 real ones no longer scores 1.00.
- **overall** — per clip, the mean of the non-null components; per run, `totals` averages each component over the clips that have it, and `totals.overall` is the mean of the per-clip overalls. A clip with no shotlog rows keeps `null` for move and role when `--labels shotlog` is used.

## Notes

- Gotcha: matching is only by the `_(\d{4})_` counter in the file name, so two clips with the same 4-digit counter collide; the first sorted name wins and a `multiple outputs matched` note is added.
- `--labels geometry` is the D1a-2 baseline number; `--labels shotlog` is the pipeline number after the vision verdicts. Compare the two to see what the adjudication buys.
- This is the scorer for the D1a-2 round; it never changes `motion.py`/`quality.py` and never re-analyses clips. Fixing the tools to raise the score is a separate task.
