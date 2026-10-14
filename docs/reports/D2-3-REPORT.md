# D2-3 — agent-ready: a fresh agent goes from folder + brief to MP4

Branch `d2-3-agent-ready`, four tasks (one commit each): `scripts/proxy.py`,
`scripts/recap.py`, `scripts/doctor.py`, `yt-recap` skill + 10-line quickstart.
Acceptance: on `~/media/yt-test/ws-d23` (fresh workspace, original Kotor
footage), `doctor.py` then `recap.py --source ~/media/yt-test/raw` with the S1
brief produced `recap_720p.mp4` **with no other command**. Phase LLM spend:
**$0.1651** (budget 1 USD): describe $0.1156 + llm_director $0.0495 on the
acceptance run, $0.0763 on a 2-clip smoke run during the audit.

## Works (verified in D2-3)

- **`scripts/proxy.py`** — 21 originals → 21 proxies in
  `~/media/yt-test/ws-d23/proxy/` (402 MB, 251 s): 1280x720 h264 CFR 25/1 +
  aac 48k stereo, same format as the hand-made set. Originals verified
  unchanged by md5 (before/after the whole phase). Refuses `--out` that would
  land on an original (audit found and fixed an overwrite bug in the single-file
  case; `tests/test_proxy.py` covers it). 8 tests.
- **`scripts/recap.py`** — the acceptance run: 10 stations in 1967 s
  (proxy 251 s → motion 55 s → quality 34 s → describe 1062 s → llm_director
  449 s → render 32 s), one progress line each, `recap_720p.mp4` 84.3 s
  1280x720. Re-run: all ten `skip` in 0.008 s, $0. `--final` on the same
  workspace: stations 1–10 skipped, `recap_final.mp4` 1920x1080 from the
  originals (84 s) + `recap.kdenlive` (melt-validated), no model calls.
  **Frame check:** all 15 slots of the recap were matched back to their source
  windows — every slot finds its exact source frame at the expected recap time
  (mean abs diff ≈ 1–2 of 255, i.e. re-encode noise; the one fast pan matches at
  −40 ms). 11 tests (station order, skip rules, new-brief-only-redoes-the-edit,
  failure stops the chain, JSON summary, real subprocess seam).
- **`scripts/doctor.py`** — 10 checks (deps, ffmpeg, ffprobe, config, models,
  API key, disk, workspace writable, workspace outside source, originals
  read-only), prints what to fix, exit 0 with warnings / 1 on failure. The API
  key is never printed (tested). On the acceptance machine it reports exactly
  one warning: the originals folder is writable (mode 755) → `chmod -R a-w`.
  11 tests (root-safe mode-bit check, monkeypatched config and env).
- **`yt-recap` skill + quickstart** — `.claude/skills/yt-recap/SKILL.md` is the
  front door (doctor → recap → review, `--final`, re-run rules, gotchas);
  the same 10-line quickstart sits at the top of `README.md` and
  `docs/guides/user-guide.md` (byte-identical). Every flag in the skill was
  cross-checked against the tools' `--help`.
- **Suite:** 653 tests green, `scripts/check_public.py --all` clean (no footage,
  frames or thumbnails in git).

## Mocked / not verified

- The unit tests drive `recap.py` through a fake station runner (no model calls
  in CI); the real chain is verified by the acceptance run above, not by CI.
- Budget exhaustion (`BudgetExceeded` stopping the chain) is covered by the
  stations' own tests, never triggered live.
- `proxy.py`'s `h264_nvenc` path is untested here — the build machine has no
  GPU (the rule is `libx264` + runtime retry, which is what ran).
- `recap.kdenlive` passes `melt` validation but was not opened in the Kdenlive
  GUI.

## Doru, check this

1. Watch `/root/media/yt-test/ws-d23/recap_720p.mp4` (84 s) — is this the recap
   quality you want for the publish-gate demo?
2. A/B against `/root/media/yt-test/workspace/recap_d2_720p.mp4` (D2 run, same
   footage, different director run: 15 slots now vs 18 then).
3. `.venv/bin/python scripts/doctor.py --source ~/media/yt-test/raw` — the
   "originals writable" warning: should it become a hard failure?
4. `README.md` (top) and `.claude/skills/yt-recap/SKILL.md` — the 10-line
   quickstart and the front door a fresh agent reads.
5. Cost per full run: **$0.17** (ledger `~/media/yt-test/ws-d23/llm_spend.json`).
