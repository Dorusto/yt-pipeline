# D2-7 — web app + explanations

Branch `d2-7-web-app`. Two parts, as specified: explanations first (why the
edit looks the way it does), then one web app in `serve.py`. The architect did
T1 (schema), T6 (music auto) and T7 (upload) itself — the coder returned empty
replies on those three tasks (two in a row twice, notified *Struggling* both
times); T2, T3, T4, T5 came from the coder and passed audit.

## Works (verified)

- **Explanations.** The directors record a reason for every candidate they do
  not use (`edit.json` `decisions[]`, 109 rows on the acceptance edit) with the
  fixed vocabulary: *near-duplicate of X*, *defect: …*, *setup/teardown*,
  *accidental*, *low score*, *did not fit the length*. Used slots carry the
  short id (`shot_key`, e.g. `0071/s2`). `scripts/explain.py` writes a
  self-contained `explain.html`: brief + music/ambient, the screenwriter plan in
  words (logline, beats, slot plan), the timeline (thumbnail per slot, `cut
  4.00–9.50 s of 0.00–14.20 s`, the reason) and every candidate used or
  rejected. 12 tests; verified end-to-end on the acceptance workspace.
- **`edit.json` records the music mix** (`audio.music`, `audio.ambient_under_music_db`)
  when it comes from flags — the D2-6 gap is closed; the render falls back to
  the edit's ambient level when the flag is absent.
- **`llm_director --feedback "drop 0071/s2, tunnel longer"`** (repeatable)
  appends to `feedback.jsonl`, forces the re-run and puts the feedback + the
  previous edit's ids into the screenwriter, editor **and** reviewer messages;
  `recap.py --feedback` re-runs only `llm_director` → `render`.
- **Pacing table.** Shot length scales with the target length: 20 s → 8–10
  shots of 1.5–2.5 s, 90 s → 20–25 of 2–5 s, 3 min → up to 6 s; heroes stay the
  longest but shrink (`hero_cap = min(6, slot_target × 1.75)`). Verified with a
  dense fixture (20 s → 10 slots in band, 90 s → 25).
- **Web app (`serve.py`).** Top menu Projects / Upload; Projects lists every
  workspace with `running` / `failed` / `done` (from `.run.json` + pid); the
  project page has the tabs Recap / Why? / Shots (player, `explain.html`,
  `shotlog.html`) and a Re-edit form (same options + a feedback box). Upload
  takes clips and photos from the phone browser: **streaming** multipart (a
  728 MB clip uploaded in the acceptance), allowlisted extensions, per-file and
  per-request size caps, atomic `.part` writes, never overwrites (409), never
  outside the inbox. `Start` writes `brief.txt`, writes `.run.json` and spawns
  `recap.py` in the background. Every option defaults to **Auto**.
- **`--music auto`** (`scripts/music_pick.py`): scores every track in
  `~/media/music` by BPM band and energy target from the brief's pace words,
  name-vs-tag matches and length, measured with `beats.py` (cached in
  `<library>/.yt-beats/`). On the real library: calm brief → *Travelling Light*
  (67 BPM), energetic → *Gasoline* (140 BPM); the acceptance brief picked
  *Be With You* (107.7 BPM, neutral band).
- **Acceptance on real footage** (closes the D2-6 acceptance too). 21 Kotor
  clips (2.9 GB) + 4 photos **copied through `POST /upload` with curl** into a
  new inbox folder, brief `about 60 seconds, chronological`, `music=auto`,
  `length=60s`:
  - **Order:** 14 slots in capture order; the photo slot (`09:50`) sits between
    the `09:42` and the `09:56` clips — interleaved, not appended.
  - **Beats:** all 13 cuts within **0.4 ms** of a beat (target ≤ 80 ms).
  - **Pages:** `/`, `/upload`, `/kotor-demo/`, `?tab=why`, `?tab=shots`,
    `recap_720p.mp4`, `explain.html`, `shotlog.html` all **200** and list what
    they should; `edit.json`, `.run.json`, `proxy/*` all **404**.
  - Output measured: 1280×720 + audio, 62.0 s, every slot frame decodes
    (photo slot renders with the Ken Burns move).
  - LLM spend for the whole acceptance run: **$0.43** (describe $0.12,
    director $0.15, re-runs) — budget $2.
- **Suite:** 910 passed, 1 deselected (`needs_melt`/`needs_footage`).

## Fixed during acceptance

- **Beat snap could leave cuts off the music.** Heroes and slots whose window
  was full refused to move, so 11/14 cuts stayed 20–160 ms from a beat. The
  snap may now push a slot's edge by up to half a beat past its window —
  14/14 cuts snap, max 0.4 ms. (Rule wording updated in DECISIONS.)
- **`@music@` never reached render**: the station argv is a list of command
  lists and the flat substitution missed it. Fixed; both directors now also
  refuse a `--music` path that does not exist, so this class of bug fails
  loudly.
- `recap.py --no-audio-clean` (the web app's audio option) forwarded to render.

## Mocked / not verified

- The web app was exercised over `127.0.0.1` and the tailnet bind is enforced
  in code (`0.0.0.0` refused) but **not** opened from a real phone.
- The 4 acceptance photos are synthetic (cropped proxy frames with EXIF times,
  generated at test time — `~/media/yt-test/raw` has no photos). Only **1 of 4**
  made the edit: one is a near-duplicate of a clip frame and two lost on score
  to better video shots (fair competition, but a "more photos than clips" demo
  day is not reproduced yet).
- The LLM loop **fell back to the rule-based director** on both
  `llm_director --force` runs (the reviewer sent fixes back three times); the
  edit, decisions and beat snap therefore come from `director.py` in this
  acceptance. The feedback flag's mock tests cover the LLM path.
- `render_final` / Kdenlive export not run in this acceptance.
- The user profile (`feedback.jsonl` is written, never read) stays future work.

## Doru, check this

1. Open `http://10.10.1.61:<port>/` after `git pull` and
   `.venv/bin/python scripts/serve.py --workspaces ~/media/projects --inbox ~/media/inbox` —
   upload a few phone clips and watch the Projects badge.
2. The Why? tab on any project: `http://<ip>:<port>/<project>/?tab=why` — is the
   timeline + rejected list what you wanted?
3. Re-edit with feedback (`drop <id>, tunnel longer`) — does the new edit obey?
4. The pacing: a 20 s brief now gives 8–10 short shots instead of 4 long ones.
5. `--music auto` picks *Be With You* for a neutral brief; agree with the pick?
