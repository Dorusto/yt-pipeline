# Review checklist — conformance reviewer

The `reviewer` agent (`.opencode/agents/reviewer.md`) answers these questions for one commit range, before a lot is reported done and before a PR opens. It reads only; it reports findings and questions, never fixes. The same checklist runs on a sprint brief (an issue) before the sprint starts.

## Sources of truth, in order

1. Doru's explicit words in the issue and its comments.
2. `docs/RULES.md` (rules in force) and the last 10 entries of `docs/DECISIONS.md`.
3. `docs/VISION.md` section 2 (principles and product principles from review).
4. The workflow facts below.

## Questions

1. **Already exists?** Does the change add a function, constant, parser, tool, file format or flag that already exists somewhere in `scripts/`, `scripts/common/`, `schemas/` or `prompts/`? Name both places.
2. **One place for shared logic?** Is the same logic now in two tools instead of `scripts/common/` (VISION principle 1, RULES "shared code only in scripts/common/")? Does a new value silently override what another tool computes?
3. **Contradicts a decision or rule?** Quote the RULES line or DECISIONS entry.
4. **Fits Doru's workflow?** Check against the workflow facts below. Does the change assume something the workflow does not do (folder layout, file names, which edit is final, how things are published)?
5. **Real data shape?** Do the tests use fixtures that mirror the real file names and folder layout? Is anything only proven on invented data that the real data would break?
6. **The issue's scope?** Is everything the lot asked for done, and nothing outside it added?
7. **Docs in step?** When behaviour changed: RULES/DECISIONS/ROADMAP, the user guide and the skill in `.claude/skills/` changed in the same commit range. Does a rule now describe something the code no longer does?
8. **Safety rules?** No personal recordings, transcript text, keys or private IPs committed; originals read-only; every tool keeps `--help`, `--json` (wall time + LLM cost) and re-run safety.
9. **Stable files?** Did the range change a file of a tool listed as stable in `docs/RELEASES.md` without a lot that names it (RULES "Releasing", `docs/RELEASING.md`)?

## Workflow facts (confirmed by Doru, 2026-10-21)

- One OBS Start writes two files: the screen in `ecran/` with the OBS default name (`YYYY-MM-DD HH-MM-SS.mp4`) and the camera in `camera/` as `camera YYYY-MM-DD HH-MM-SS.mp4`; Hybrid MP4. Both carry the voice; the camera starts a fraction of a second to ~2 s after the screen, the gap varies per session, so sync is by sound. Camera-only sessions (intro/outro) are normal. The single-file "Devlog" scene is no longer used.
- After recording, the files move to the clip folder (`~/Videos/<YYYY-MM-name>/camera/`, `ecran/`).
- The edit is done in FilmCraft on the laptop: a rough cut, then a content cut decided in conversation (target length, what stays), then Doru's own trims. The **published video is Doru's final FilmCraft export**, not the rough cut.
- Background removal is RVM through `matte.py` (licence allows commercial videos); MatAnyone2 only as an optional personal backend.
- Publishing is manual in YouTube Studio from the publish kit (no API upload, DECISIONS 2026-10-21); Shorts are uploaded by hand to YouTube Shorts and Instagram Reels, same vertical file.
- Subtitles: Whisper corrections list approved by Doru line by line, RO then EN, uploaded as separate `.srt` files.

## Report format

```
Verdict: OK | PROBLEME | ÎNTREBĂRI
Interval: <base>..<head> (<branch>)

Probleme
1. <ce> — <fișier:linie din diff> — încalcă: <regula / decizia / faptul, citat scurt>

Întrebări pentru Doru
1. <doar despre intenția lui sau felul în care lucrează: ce nu spun documentele și de ce contează>

De decis de arhitect
1. <puncte tehnice deschise care nu cer părerea lui Doru>

Verificat fără probleme
- <întrebările din checklist la care răspunsul e „da, e în regulă”, pe scurt>
```

The first line is the verdict so it fits in a phone notification.
