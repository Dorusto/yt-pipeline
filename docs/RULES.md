# RULES.md — rules in force

Current state, one line per rule, with the `DECISIONS.md` entry date it comes from. `DECISIONS.md` stays the history; when a decision changes a rule, update this file in the same commit (2026-10-14). Agents read this file plus the last 10 `DECISIONS.md` entries, not the whole log.

## Editing grammar

- Analysis tools label, never delete: one take can hold several intentional shots; the director decides what becomes a slot. — 2026-10-12
- Geometry `accidental` / `setup` / `teardown` are never slots, whatever the shotlog says; a vision verdict may relabel context but never re-admits the segment. — 2026-10-13 / 2026-10-14
- A move and the hold it lands on form one phrase: keep the move through its landing or use only the hold; never cut out of a move and into its own landing (no jump cut). — 2026-10-13
- Phrase window: the end cut lands inside the hold (`out >= landing_start + 0.75 s`); the head trims to the pacing target but never before the move's handle-in; a phrase never exceeds its target. — 2026-10-13
- Phrase score is the move's score; the phrase wins on a tie; the hold alone wins exactly when the hold is the better shot on its own. — 2026-10-13
- Two slots from one take need a different take between them unless they are source-contiguous (±0.5 s); contiguous neighbours merge into one slot. — 2026-10-13
- One candidate (segment or content beat) contributes at most one slot; a long take may contribute several beats as long as a different take sits between them (e.g. 0072 tunnel approach and exit). Within one candidate a higher-scoring blocked window replaces the conflicting slot (`same-take upgrade`). — 2026-10-13 / 2026-10-14
- No two visually similar segments in one edit unless the brief asks for it; never back to back, even with `--allow-similar`. — 2026-10-13
- Hero shots and complete semantic moves (orbit, reveal, tracking, pov, travel) keep their whole steady range up to the cap; pacing shortening applies only to ordinary shots. — 2026-10-13
- An edit must not miss an eligible hero when one exists (golden invariant, tested on fixtures). — 2026-10-13 / 2026-10-14
- Important-shot cap: default 6 s (was 8 s); the window ends where the steady motion ends, never on aimless movement after it. — 2026-10-14
- Ordinary slot length comes from the brief's pacing (calm vs energetic) with a min/max; cut inside the steady range. — 2026-10-13
- Time-overlapping shots (>50% of the shorter) keep the higher score; the loser is suppressed. — 2026-10-13
- Long segments (>20 s) split into content beats: `describe` samples a frame every ~5 s, marks scene changes; each beat becomes its own candidate. — 2026-10-14
- The screenwriter's menu is the cuttable pool: only windows the candidacy rule above allows. — 2026-10-13
- `edit.json` is the edit list and the only source of truth; render, preview, graphics and Kdenlive export are adapters that read it. — 2026-10-13
- Router is deterministic (brief keywords → workspace signals → vacation default); a route is a decision, not an analysis. — 2026-10-13
- LLM director: the model chooses shots and rough lengths, a code validator enforces the grammar above, the reviewer approves or sends fixes back (max 3 rounds); `director.py` is the fallback and the `--dry-run` baseline. — 2026-10-13
- New hand-written geometric rules only when a case recurs and the eval shows it hurts edits; the long tail goes to vision verdicts, not per-case code. — 2026-10-13

## Defaults and thresholds

- Motion floors: pan/tilt 0.03 fw/s, roll 5 deg/s, dolly 0.05 log/s plus 70% sign consistency; split on dominant-type change (0.6 s hysteresis, 0.4 s short-run merge). — 2026-10-12
- Parallax = `min(1, residual_norm / 0.02)`; ≥ 0.5 relabels lateral moves to `truck_*`; centred subject (centre-box residual ≤ 0.35 × periphery) proposes `orbit_candidate`. Precedence `travel_*` > `orbit_candidate` > `truck_*` > `pan_*`. — 2026-10-13
- `platform_bob` is an annotation (amplitude ≥ 0.008 of frame height, period 1.5–8 s), never a label; `travel_*` upgrades sustained smooth scale runs ≥ 8 s. — 2026-10-13
- Horizon: only a dominant long (≥ 25% width) near-horizontal (≤ 15°) line with angle MAD ≤ 3°, else `null`; the defect fires above 5° (= eval tolerance). — 2026-10-13
- Scene activity from residual local flow on the upper 70% of the frame (scene band); event noise floor 0.001, event start 0.001 above the floor. — 2026-10-13
- `accidental`: unstable run ≥ 5 s or ≥ 70% of the clip; whole-clip confidence 0.65, run 0.6; healthy travel shake 0.007–0.012 is stable content. — 2026-10-13
- Quality segments: head/tail are before the first / after the last content segment; slivers merge into a neighbour with `end_s > start_s`; a `static` label is never role `move`. — 2026-10-13
- Shotlog score = `0.55·vision + 0.45·labels`; vision = interest/100 (+0.15 hero); missing vision is a neutral 0.5 prior; the verdict wins on `uncertain` segments. — 2026-10-13
- Confident `accidental`/`setup`/`teardown` are `eligible: false` in the shotlog. — 2026-10-13
- Similarity: MobileNetV2 ONNX through `cv2.dnn` (hist backend offline); "similar" starts at 0.90 and stays a flag. — 2026-10-13
- Eval scoring: interval overlap (full credit exact, 0.5 geometric look-alike), horizon tolerance 5°, events one-to-one F1 within ±3 s. — 2026-10-13
- `describe`: 3 frames in order with timestamps + motion label, one-line summary (≤ 12 words) + tags, retry once on unparseable output, `max_tokens` 8000 for reasoning models. — 2026-10-13
- Render: 720p preview from proxies, final from the originals at native size (`--originals-dir`), crossfade 0.3 s, frame-inclusive Kdenlive `out` (`out_s - 1/fps`). — 2026-10-13

## Model routing

- Architect: MiMo V2.6 Pro via OpenRouter; coder: DeepSeek V4.1 Flash (fallback GLM 5.3 Flash). Do not switch coder models mid-task. — 2026-10-12
- Vision default: GLM 5.3 Flash (fallback DeepSeek V4.1 Flash); the backend is a config value, never code. — 2026-10-13
- Local Qwen3.8 27B (4-bit, RTX 3090, vLLM) takes over for private footage. — 2026-10-12
- Every LLM call goes through `scripts/common/llm.py`: `usage.cost` charged per call (missing cost raises), a per-project spend ledger caps the whole job; stop and report at the budget cap. — 2026-10-12
- `config.yaml` holds model ids per role and the per-run budget; `llm_backend: mock` for offline runs; no secrets in the file. — 2026-10-12
- Music: YuE2 first (editable ABC score → cuts land on beats by construction), ACE-Step 1.5 fallback; excluded for licence: MusicGen, AudioGen, MMAudio, HunyuanVideo-Foley. — 2026-10-13
- No ComfyUI: music, SFX and clean-up are plain Python tools, one model loaded at a time. — 2026-10-13
- No torch unless needed; the encoder is `h264_nvenc` only when a GPU is present, else `libx264` with a runtime retry. — 2026-10-12

## Safety rules

- Originals are read-only; outputs go to the project workspace; tools refuse to write inside the originals folder. — 2026-10-13
- Private by default: the web editor and its services are tailnet-only; footage leaves the homelab only as frames to the configured vision model. — 2026-10-13
- Never commit footage, frames, transcripts, subtitles, model weights or keys: `scripts/check_public.py` runs as pre-commit hook and in CI; `tests/fixtures/` and `examples/` hold synthetic data only. — 2026-10-14
- Feedback is append-only JSON lines per workspace (`feedback.jsonl`, ~200 bytes/action), never footage or frames; the per-user profile is rebuilt from those files. — 2026-10-14
- Every `--json` summary reports wall time and LLM cost (USD). — 2026-10-13
- Watchdog every 30 min: $3/day spend, 2 h stall, no-commit alerts; the architect notifies on the second failed audit of a task or two empty coder replies. — 2026-10-13
- Skills live in `.claude/skills/` and change together with their scripts in the same commit. — 2026-09-22
- Short docstrings on public functions and modules allowed; inline comments not. — 2026-10-13
- WhisperX forced alignment on the corrected SRT is the word-timestamp source of truth; never re-run Whisper for timings. — 2026-06-18
- Face detection: OpenCV Haar cascades, one static crop offset per segment; `x_offset` override skips detection. — 2026-06-18
- Every tool is a standalone script in `scripts/` with `--help`, `--json` and re-run safety; shared code only in `scripts/common/`; files validated against `schemas/` are the interfaces. — 2026-10-12
- Commit backdating after 18:00 with non-round times; the repo is public. — CLAUDE.md
- `--skip-alignment` only when the JSON is already correct and only ASS + video re-render is needed. — CLAUDE.md
- `scripts/recap.py` is the single entry point for agents and humans; it only chains the station CLIs as subprocesses. — 2026-10-14
- Recap skip rule: per-station output/input mtimes plus the brief text recorded in `route.json`/`edit.json`; a new brief redoes only router → llm_director → render, `--force` re-runs everything. — 2026-10-14
- Proxies keep the source filename and live in `workspace/proxy/`; `proxy.py` refuses an `--out` that would write onto an original. — 2026-10-14
- Preview services are tailnet-only: `serve.py` binds one address, never the wildcard, and serves only `recap_720p.mp4`, `recap_final.mp4`, `shotlog.html` per project (allowlist routes, Range supported). — 2026-10-14
- `recap.py --notify` posts the preview link (`<preview-base>/<workspace name>/recap_720p.mp4`) on success only; a failed run never notifies and a notify error never fails the recap. — 2026-10-14
- Inbox grouping: a `YYYY-MM-DD` folder in the inbox wins over the filename date, then mtime; one workspace per day under `--projects`; a day whose clips share one folder is passed to `recap.py` directly, otherwise a rebuilt symlink farm (the inbox is read-only). — 2026-10-14
- `inbox.py` waits for the inbox to be quiet (`--quiet-minutes`, default 5) and re-invokes `recap.py` on a new clip, a changed brief or `--force`; the station skip rule stays the only caching. — 2026-10-14
- Phone sync is Syncthing: phone send-only camera folder → LXC receive-only `~/media/inbox`; setup steps live in `docs/guides/phone-flow.md`, installs are Doru's machine steps. — 2026-10-14
