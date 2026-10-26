# D2-12 report: normal speed by default, cuts centred on the best moment

## Works

- `--speed-ramp` (default off) on `director.py`, `llm_director.py`, `recap.py`; web Upload and Re-edit have a Speed ramp select (Auto = off / off / on). With the ramp off `apply_move_policy` never sets `middle_speed` but keeps the length policy (static/holds/photos <= `slot_max`, at most 15 % rounded of the slots up to `hero_max`). `meta.speed_ramp` is stored and a changed setting re-runs the edit.
- `scripts/common/peak.py`: peak choice (vision `peak_s` > motion maximum > window middle) and window centring, used by both directors. The llm validator, shot facts, editor and reviewer prompts carry `peak_s` and the window rule.
- `peak_s` plumbing: describe prompt and normaliser (validated inside the segment/beat), `describe.schema.json`, `index.py` into `shotlog.json` and `shotlog.schema.json`, `edit.schema.json` (`peak_s`, `peak_source`, `centred`, `meta.speed_ramp`). The mock vision backend returns a deterministic `peak_s`.
- `explain.py` shows "centred on the best moment at X s (from vision|motion|middle)".
- Full suite: 1081 passed, 1 skipped (`-m "not needs_melt and not needs_footage"`); `scripts/check_public.py --all` ok.
- End to end (8 synthetic 1280x720 clips: 3 s hold, 6-10 s pan over a distinct textured image, a bright object crossing at mid-pan; odd clips have a 1 s tail hold; 120 BPM click; mock backend; brief "About 40 seconds, chronological, calm start"), 8 slots:
  - Default: `middle_speed` 1.0 on all 8 slots; max cut-to-beat error 0.3 ms; the peak source is `vision` on every slot (mock peak).
  - Vision peaks stripped (motion path): source `motion` on all 8; max beat error 0.3 ms with `--beats`.
  - `--speed-ramp`: `middle_speed` 1.2 on 7 slots and 1.05 on 1; max beat error 0.4 ms.
- Golden fixtures: nine changed, only `expected_slots`, explained in the DECISIONS entry and the commit message.

## Mocked / not verified

- Real VLM `peak_s`: the mock peak is a deterministic hash (35-65 % of the span), not truthful, so it does not coincide with the real object crossing. Whether a real model picks a sensible peak is unverified.
- The motion peak uses 1 s bins and is coarse. In the e2e it hit the object crossing exactly on one clip (6.5 s) and was within about 0.5-1.5 s on others (the true crossing was often outside the candidate window, so the peak was clamped by the window).
- Landing phrases (a move followed by its landing hold, the even clips): the landing floor wins over the peak, so the cut ends at the floor and `centred` is false. This is deliberate (see DECISIONS) and produces a warning.
- Real footage, a real render inspected by eye on a phone, MobileNet similarity (hist backend used), the OpenRouter LLM path (the llm_director with the mock client falls back to the rule director, tool `director.py`).

## the owner, check this

- `docs/DECISIONS.md` last entry: decide whether, for a move + landing phrase, the best moment should win over the landing hold.
- Run `scripts/recap.py --source <folder> --brief "..."` on real footage: are long moves cut around the gate/subject? `explain.html` shows "centred on the best moment at X s (from ...)" per slot.
- Run once with `--speed-ramp` (or Speed ramp: on in the web form) and compare feel against the default.
- `prompts/describe_segment.md` section `peak_s`: check the wording against the real model's answers.
- `docs/RULES.md` lines "Length policy", "Important-shot cap" and "Peak-centred cuts" replaced the D2-11 wording.
