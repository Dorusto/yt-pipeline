# D2-6 — photos + music

Branch `d2-6-photos-music`. Built by the architect agent (the DeepSeek coder
returned empty replies on three tasks; the architect did those itself). The run
was stopped after it stalled on the last beat-snap task; Claude (reviewer)
finished it, found two bugs on a synthetic end-to-end run, fixed them and
merged master (D2-4, D2-5).

## Works (verified)

- **Photos as candidates:** ingest reads JPEG/HEIC with EXIF capture time
  (Pillow + pillow-heif), proxy copies JPEGs byte-for-byte and converts HEIC to
  JPEG with EXIF kept, describe covers each photo with one frame
  (`subject_position`), similarity dedups bursts, index writes them with
  `media_type: "photo"`.
- **Photo slots:** 2–4 s, Ken Burns move from the subject position, ffmpeg
  `zoompan`; portrait photos on a blurred background, no black bars (checked
  on output frames).
- **Chronology:** photos interleave with clips by capture time. Synthetic run
  (4 clips + 4 photos, times spread over 18 min): order
  `p1 v1 v2 p2 v3 v4 p3 p4`, as captured.
- **Music:** `--music FILE` on `recap.py` and `render.py`; `beats.py` gives the
  grid, both directors snap every cut to a beat (downbeats at structure
  changes). Synthetic run, 120 BPM click track: **all 8 cuts within 0 ms of a
  beat** (target ≤ 80 ms).
- **Mix rule (the owner):** the music is the bed, normalised to the loudness
  target; the cleaned ambient stays audible 12 dB under it
  (`--ambient-under-music-db`), never removed; a loud ambient event dips the
  music gently. Measured: ambient 11.3 dB under the music (unit test), and on
  the synthetic recap the music level stays constant across clip and photo
  slots while the ambient sits ~26 dB below the click peaks.
- **Suite:** 837 passed, 2 skipped after the merge with master.

## Fixed by the reviewer

- `proxy.py` dropped the clips' `creation_time` (no `-map_metadata 0`). Clips
  whose names carry no date (Sony `C0001.MP4`, phone) lost their time, so all
  photos landed after all clips. DJI names hid the bug on the Kotor test set.
- `render.py` mixed the music 12 dB under the raw track level and ducked it
  further under a loudness-normalised ambient bus: the opposite of the rule
  above. Now inverted as described.

## Mocked / not verified

- The end-to-end run used synthetic clips and photos with the mock vision
  backend and the offline `hist` similarity backend (the build sandbox cannot
  download the MobileNet model). **Real-footage acceptance (Kotor day with
  more photos, real music track) still has to run on the LXC.**
- `edit.json` does not record the music track when it comes from
  `recap.py --music` (render gets it as a flag). To fix in D2-7, where the web
  app passes options through `edit.json`.
- `--music auto` (pick a track from `~/media/music` by brief and tags) is not
  built; D2-7 or later.

## the owner, check this

1. On the LXC, after `git pull`: `recap.py --source <Kotor photo day> --music ~/media/music/<track>.mp3 --brief "about 60 seconds, chronological"` and watch the preview.
2. Ken Burns on your real photos: does the move feel calm (8 % zoom/pan)?
3. Ambient under the music: 12 dB right, or should the default be closer (8) or further (16)?
