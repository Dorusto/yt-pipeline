# RISKS — what can go wrong and what we do about it

Current state only. Each risk has an owner phase; when a mitigation ships, update the row.

| # | Risk | Effect | Mitigation | When |
|---|---|---|---|---|
| 1 | **Agent context grows**: `DECISIONS.md` passed 1 000 lines; agents re-read it every task | slower, costlier runs; old decisions contradict new ones | `docs/RULES.md`: short digest of the rules in force; agents read RULES + the last 10 DECISIONS entries; DECISIONS stays the history | D3-00 |
| 2 | **Overfitting to one shoot**: thresholds tuned on 21 boat clips from one day | other trips (motorcycle, city, night, A7 III, phone) edit badly | second and third test sets before D3 ends (Dolomites motorcycle, a phone set); eval per set | D3 |
| 3 | **Regressions**: a fix breaks an earlier fix (0051 came back as an opener in D2-01) | silent quality loss | golden tests: frozen `edit.json` expectations per test set (no jump cuts, no accidental, heroes present), run in CI next to the label eval | D3 |
| 4 | **Different cameras**: phone video has variable frame rate; D-Log / HDR 10-bit looks flat; A7 III has no gyro; DJI clock vs local time zone | wrong cut points, washed-out previews, wrong chronological order | proxies always constant frame rate; tone-map log/HDR for proxies and vision; order by creation time with per-camera offset in config; gyro optional | D3 |
| 5 | **Long footage**: hours of 4K on a 4-core LXC | analysis takes hours, disk fills | proxies first, cached analysis, run on the 3090 server; watchdog also checks free disk space | D3 / server |
| 6 | **Privacy**: family and other people sent as frames to a cloud vision model | personal data leaves home | per-project `vision_backend: local` (Qwen on the 3090); tailnet-only web editor; never commit footage, frames or thumbnails | D5 / server |
| 7 | **Model price or availability changes** (OpenRouter) | edits fail or cost more | backends are config; mock + local fallback; spend cap per run; watchdog daily cap | in place |
| 8 | **Agent loops or overspends** | wasted credit, no progress | watchdog: spend, stall, no-commit checks; architect stops after a tool fails twice | in place (2026-10-14) |
| 9 | **Originals damaged** | lost footage | tools refuse to write inside the originals folder; mount `raw/` read-only on the LXC | partly (render refuses); read-only mount: D3 |
| 10 | **Licences**: music/SFX models, Remotion, dependencies when the project goes public or earns money | legal exposure | licence checked per model in `docs/research/rendering-and-audio.md`; choose repo licence (MIT vs AGPL) before promoting | before going public |
| 11 | **Feedback files grow without bound** | slow loads | append-only per project, compacted into a per-user profile; size limits in DECISIONS 2026-10-14 | D3 |
| 12 | **Doru is the bottleneck**: every checkpoint needs his eyes | phases wait | contact sheet + phone editor; short reports with ≤ 5 checks; publish gate keeps scope small | ongoing |
