# D1a-04 — motion.py round 2 fixes (architect audit findings)

The first implementation followed the brief faithfully; the brief's segmentation
and label rules were wrong on real footage. Fix exactly these three things in
`scripts/motion.py` and `tests/test_motion.py`. Files touched: those two only.
The skill only if behavior described there changes.

## Audit evidence (real run, 21 DJI proxies)

- 39 moves, labels: `static` 3, `dolly_in` 16, `dolly_out` 20 — zero pans/tilts
  on action footage that contains them.
- Clip `...0072...` (176 s): one `steady_move` phase from 10.4 s to 167.1 s
  labelled `dolly_in`, while its own sliding windows show fast pans inside it
  (t=84: `dx -0.34`, t=92-94: `dx +0.34..+0.52`, t=104: `dx -0.42`) and long
  scale drift (`scl +0.2/s` while walking).
- Root cause 1: a moving run is labelled **once** from the mean over the whole
  run, so one label swallows every change inside it. The D1 research asks for
  "split where the dominant motion type changes (pan becomes tilt, orbit stops)".
- Root cause 2: the dolly score `|log(scale) rate| / 0.25` activates at just
  1.25 %/s of scale change — below the slow drift that parallax, walking and
  electronic stabilisation produce — so dolly beats real pans.

## Fix 1 — split moving runs on dominant-type change

Label each trajectory sample individually (from the smoothed series, the same
rate math as today) and split moving runs into sub-runs of the same dominant
component+sign. Rules:

- Per-sample dominant component = argmax of `|rate_dx|`, `|rate_dy|`,
  `|rate_roll|/45`, `|rate_scale|` (the four per-sample rates, all per second),
  **after fix 3's significance gates**. When no component is significant the
  sample is `still`/`static`.
- Use the component **name with sign** as the run key
  (`pan_right`, `tilt_up`, `roll_cw`, `dolly_in`, ...) — do **not** use
  `complex_motion` as a run key.
- **Hysteresis:** a new run key starts only after it has been the dominant key
  for `0.6 s` continuously (7-8 samples at 12.5 fps); before that the sample
  stays with the previous key. The first run needs no hold.
- Then apply the existing short-run merge (`< 0.4 s` into the previous run) and
  the existing still/moving run structure on top of this: still runs are `still`
  phases; each labelled moving sub-run gets the ramp_up / steady_move /
  ramp_down treatment exactly as now (including the `<= 1.0 s` → `irregular` and
  the high-shake → `irregular` rules).

## Fix 2 — phase and move labels use the complex rule on the window, not the run key

- `complex_motion` is a **label of a window** (phase or move), computed from that
  window's mean rates as now: top two scores within a factor of 2 **and** both
  above `0.05` → `complex_motion` with `semantic_check: true`.
- Remove `label == complex_motion` from the "phase is irregular" rule. Irregular
  is now only: run shorter than `1.0 s`, or nothing reaches the ramp band, or
  mean shake `> 0.08`.
- A `complex_motion` steady phase still yields a move (`semantic_check: true`).

## Fix 3 — significance gates for the four channels

A channel only competes for dominance / `static` when its window rate is real:

- pan: `|rate_dx| >= 0.03` frame-widths per second
- tilt: `|rate_dy| >= 0.03` frame-heights per second
- roll: `|rate_roll| >= 5.0` degrees per second
- dolly: `|rate_scale| >= 0.05` log-units per second (about 5 %/s), **and** the
  per-sample `log(scale)` values in the window must be sign-consistent: at least
  70 % of them have the same sign as the window mean. Without this consistency
  test, parallax/stabilisation drift masquerades as dolly.

Apply the same gates to the per-sample keys (fix 1) and to `label_window`'s
scores (a non-significant channel scores 0 for the dominance contest and cannot
make the window non-static). A window where no channel is significant is
`static`. `semantic_check` on `dolly_in`/`dolly_out` stays `true` (dolly vs zoom
is still ambiguous).

**Still vs moving is now the significance test, not a speed threshold:** a sample
is `moving` when at least one channel is significant at that sample (per-sample
rates against the same floors, with the dolly sign-consistency test taken over
the neighbouring `0.5 s`), else `still`. The old `speed_score < 0.05` rule is
retired for this decision.

**Speed score** (for `slow` / `regular` / `fast` only) = max over **significant**
channels of the normalised rates: `|rate_dx|`, `|rate_dy|`, `|rate_roll| / 45`,
`|rate_scale| / 0.25`, where a significant channel's rate is reduced by its
floor first (so a 6 %/s drift scores `0.01/0.25`, not `0.24`). Zero when nothing
is significant. The `slow`, `regular` and `fast` thresholds (`0.15`, `0.6`) stay
as they are.

## Keep unchanged

Interface, flags, output JSON shape, schema validation, skip/`--force`, atomic
writes, `analysis.*` fields, handles and confidence formulas, shake definition,
steadiness words, performance (the whole 21-clip folder must stay under ~2 min).

## Tests to add or adjust

1. A fabricated still → pan → dolly → still series must now yield **separate**
   phases and moves for the pan and the dolly (currently one `steady_move`).
2. A fabricated series with `log(scale)` noise (alternating ±0.01, mean ~ 0) and
   no translation must label `static`, not `dolly_*`.
3. A fabricated series with consistent `log(scale)` = +0.01 per sample at 10 fps
   (rate 0.1/s) labels `dolly_in` with `semantic_check` true.
4. Hysteresis: a one-sample blip of a different dominant component does not split
   a run; a change that persists 0.6 s does.
5. Existing label/word/phase/move/CLI tests must keep passing (adjust only what
   fix 1-3 legitimately changes).

## Acceptance (the architect will re-run)

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/motion.py ~/media/yt-test/proxy --workspace /tmp/opencode/motion_r2 --json
```

then a label census over `/tmp/opencode/motion_r2/motion/*.json` must show a mix
of labels (at least `static` and pan/tilt/dolly variants), and clip `...0072...`
must have more than one move and phases that change dominant type during the long
walking stretch.
