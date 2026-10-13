---
name: yt-director
description: Turn the workspace motion/ and quality/ labels into an edit list (edit.json) with a rule-based, deterministic director — no LLM and no vision model. It picks cut windows from holds, moves and transitions, scores them from role, confidence, steadiness and defects, applies a calm-to-energy or uniform pacing, lays them on a chronological or score-ordered timeline and writes edit.json validated against schemas/edit.schema.json. Use when the user asks to build an edit, a rough cut or a recap from analysed footage, wants an edit.json, or asks which moments the director would pick. Trigger phrases - "fă edit.json", "construiește montajul din footage", "directorul alege cadrele", "build an edit from the analysis", "pick the cut windows", "generează lista de montaj". Not for analysing footage (that is `ingest`/`motion`/`quality`), describing content (that is `describe`) or rendering the MP4 (that is `render`).
compatibility: Run from the repository root. Needs the repo .venv; CPU only, no GPU and no network. Reads ingest.json, motion/<stem>.json and quality/<stem>.json from the workspace read-only and writes edit.json (or --out) atomically. Deterministic: the same inputs and flags always produce the same edit.
---

# yt-director

Pick the cut windows of an edit from the labels the analysis tools already wrote. This is the S1 minimal director: rule-based and deterministic, no model is called, every `reason` is built from the labels. It reads the workspace only and writes `edit.json`, the single source of truth every output adapter reads. All commands run from the repository root.

## Step 1: direct an edit

```bash
.venv/bin/python scripts/director.py --workspace /path/to/workspace \
  --brief "about 90 seconds, calm start, more energy towards the end" \
  --target-duration-s 90 --order chronological --pacing calm-to-energy --json
```

- `--workspace` is required and must hold `ingest.json`, `motion/<stem>.json` and `quality/<stem>.json`. A missing `ingest.json` is fatal; a clip without a `quality` document is skipped with a warning; a clip without a `motion` document still contributes hold windows and uses the quality segment bounds for moves.
- `--out` (default `WORKSPACE/edit.json`) is the edit list to write. `--brief` is stored verbatim in `edit.json`; it is not parsed into flags.
- `--target-duration-s` (default 90) sets the edit length. `--order` is `chronological` (default) or `score`. `--pacing` is `calm-to-energy` (default) or `uniform`.
- `--min-slot-s` (default 2.0) drops shorter windows, `--max-slot-s` (default 12.0) caps a slot, `--xfade-s` (default 0.3) is the crossfade written into every non-first slot.
- `--force` rewrites even when the output is up to date. `--json` prints the machine-readable summary.
- On any error (missing workspace, no usable candidates, schema violation, write failure) it prints one `[error] ...` line to stderr and exits 1. It never asks questions.

## Step 2: read the result

```bash
.venv/bin/python scripts/director.py --workspace /path/to/workspace --json
```

The `--json` output is a single object, e.g. `{"status": "written", "out": "/abs/workspace/edit.json", "slots": 12, "total_duration_s": 87.4, "target_duration_s": 90.0, "warnings": [], "wall_time_s": 0.51, "llm_cost_usd": 0.0}`. `status` is `"written"` or `"skipped"`; `wall_time_s` is the seconds the run took and `llm_cost_usd` is always `0.0` (this tool never calls a model). Without `--json` it prints one line per slot (`v1  clip.mp4  12.50-21.00  0.72  hold · static · clean · calm opening`), then `total X.Xs of target Y.Ys`, then the warnings.

## How the director decides

- **Candidates.** Every quality segment with role `hold` becomes its own window; `move`, `transition_in` and `transition_out` use the overlapping motion move's handle window (falling back to the steady range, then the move bounds, then the segment bounds). `accidental`, `setup` and `teardown` segments are never used and are counted in a warning. Windows shorter than `--min-slot-s` are dropped, and overlapping windows inside one clip keep the higher-scored one.
- **Score.** Base by role (hold 0.55, move 0.50, transition 0.35) plus `0.25 * confidence`, a steadiness bonus (`no_shaking +0.15`, `minimal_shaking +0.08`, `static +0.10`, `unsteady -0.10`, `very_unsteady -0.20`), a defect penalty (`-min(0.4, sum(value * weight))`, horizon weighted 0.5) and `+0.10` when the segment is clean. The result is clamped to 0..1.
- **Pacing.** With `calm-to-energy`, clips in the first 40% of the footage favour holds (`+0.15`) and penalise moves (`-0.05`); clips in the last 40% reward energetic labels (`travel_*`, `pan_*`, `truck_*`, `dolly_*`, `orbit_candidate`) and `regular`/`fast` moves (`+0.15`) and penalise holds (`-0.05`). `uniform` adds nothing.
- **Timeline.** `n_slots = clamp(round(target / 6), 4, 24)`. The clips are laid end-to-end in chronological order and the total content time is split into `n_slots` equal buckets; each bucket takes its highest-scored unused candidate whose content window overlaps it, trimmed to the slot's target duration (shorter windows stay whole, never padded). Wall-clock gaps between clips do not create empty buckets. An empty bucket is skipped with a warning. `--order score` instead fills the slots highest-score first.
- **Fill pass.** When the picked slots sum to less than the target duration and unused candidates remain, the director keeps adding the highest-scored unused candidate (tie: earliest content position), trimmed to `min(window, --max-slot-s, remaining to target)` and checked by the overlap guard, until the sum reaches `--target-duration-s` or runs out. Chronological order inserts each fill slot at its content position; `--order score` appends it. A candidate dropped by the overlap guard counts as used.
- **Reason.** Built from the labels joined with ` · `: role, motion label, steadiness, `clean` or the defect names, and a pacing word (`calm opening` when the wall-clock position is `<= 0.4`, `energy build` when it is `>= 0.6` and the role is not `hold`, `late hold` when it is `>= 0.6` and the role is `hold`, `neutral` otherwise). Example: `hold · static · clean · calm opening`.

## Notes

- The edit list is `edit.json`; `render.py` and the optional Kdenlive export are adapters that read it. This tool never renders and never touches the source clips.
- Re-run safety: when `--out` exists, validates against the edit schema and is newer than every input file, the tool writes nothing and reports `status: "skipped"`.
- The director is deliberately crude: it only sees labels, so its faults (short windows, empty buckets when a stretch of footage has no usable segment) are the priorities for the later director phases.
