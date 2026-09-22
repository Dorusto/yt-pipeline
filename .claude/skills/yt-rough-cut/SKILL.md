---
name: yt-rough-cut
description: Assemble a rough-cut Kdenlive project from raw footage and a list of chosen fragments, retime the subtitles to match, and summarise raw transcripts day by day. Use when the user has a lot of uncut footage (multi-day trips, vlogs) with raw .srt transcripts and wants a .kdenlive timeline, a subtitle file for it, or a per-day summary of what was said. Trigger phrases - "fă rough-cut din footage brut", "generează proiectul Kdenlive din fragmente", "rezumat pe zile din transcripturi", "subtitrare pentru rough-cut", "build a rough cut from these fragments". Not for editing a finished video or for Shorts.
compatibility: Run from the repository root. Needs the repo .venv, ffprobe, and melt (MLT). The day summary also needs OPENROUTER_API_KEY. Footage is assumed to be 25 fps with video stream 0 and audio stream 1.
---

# yt-rough-cut

Turn raw footage plus a list of chosen moments into an editable Kdenlive timeline, so nobody scrubs through every source file by hand. The result is a starting point: trimming, reordering and adding material continue by hand in Kdenlive. All commands run from the repository root.

## Prerequisites

- Raw video files in one folder (`source_dir`), and next to them, in a transcripts folder, one raw `.srt` per video with the same file name and a `.srt` extension (`DJI_..._D.MP4` needs `DJI_..._D.srt`).
- `ffprobe` and `melt` on the PATH. Kdenlive 23.04 or newer to open the result.
- The generator assumes 25 fps, a 1920x1080 profile, video stream index 0 and audio stream index 1 for every source (the layout of DJI action cameras). Footage with another frame rate or stream layout gives wrong timing or silence.

## Step 1 (optional): summary per day

Useful when there is a lot of raw footage and the user needs a map of what was said when.

```bash
.venv/bin/python scripts/daily_summary_srt.py /path/to/transcripts_raw/ /path/to/summary.md
```

Read this before offering it:
- Files are grouped by day using 14 consecutive digits in the file name (`YYYYMMDDHHMMSS`, as in `DJI_20250810123456_0001_D.srt`). Files without them are skipped and listed as `[atenție]`.
- The prompt inside the script is written in Romanian for a solo motorcycle trip dictated on the road or in a tent. The summary is a first-person Romanian diary, 150-400 words per day. For other kinds of footage the framing will be wrong.
- Only the first 80,000 characters of each day are summarised. When it prints `Transcript lung ... trimit primele 80000 chars`, whatever was said later that day is not in the summary. Tell the user.
- Needs `OPENROUTER_API_KEY`, one API call per day. The output file is written only at the end; an API error leaves nothing.
- Without the second argument the output is `rezumat_pe_zile.md` in the parent folder of the transcripts folder.
- The summary is a map for navigation, not a source for quotes: it cleans up hesitations. Take exact quotes only from the raw `.srt`.

## Step 2: choose the fragments (the user's decision)

The user decides which moments go in and in what order. Do not choose them. You can help find exact timecodes: search the raw `.srt` files for the quote the user names, take the start of the first subtitle line and the end of the last one, and ask whether they want padding.

## Step 3: write `fragments.yaml`

```yaml
source_dir: /abs/path/to/raw/clips
output: /abs/path/to/Project_generated.kdenlive
fragments:
  - file: DJI_20250810123456_0001_D.MP4
    in: "00:11:16.320"
    out: "00:11:50.680"
    label: short-note-for-humans
```

- Fragments land on the timeline in the order listed.
- `in` and `out` are `HH:MM:SS.mmm`: two digits per part and exactly three decimals, with a dot (the raw `.srt` uses a comma). The subtitle script fails on any other shape.
- Always quote them. Unquoted, YAML reads `00:11:16.320` as a number.
- `label` is only a note for humans; neither script writes it anywhere.
- Name the output `*_generated.kdenlive` so it never collides with the file the user edits by hand.

## Step 4: generate the project

```bash
.venv/bin/python scripts/kdenlive_from_fragments.py fragments.yaml
```

- Every fragment becomes a video entry and an audio entry on separate tracks, linked as one group so moving or trimming one moves the other.
- If the output file already exists it is first copied to `<output>.bak-<timestamp>` next to it, then overwritten.
- It runs `melt` on the result. Expect `written: ... (N fragments, M source clips, N AV groups)` and `melt validation OK`. If validation fails the file stays on disk and the script exits with an error.
- A missing source file stops the script with an ffprobe error.

Before regenerating over an existing project, ask: "Have you edited this project by hand since the last generation?" Regeneration replaces the file. The backup keeps only the previous version each time. Once serious manual editing starts, the user should Save As under a different name in Kdenlive, so the `_generated` file stays script output only.

## Step 5: generate the retimed subtitles

```bash
.venv/bin/python scripts/srt_from_fragments.py fragments.yaml /path/to/transcripts_raw/ /path/to/output.srt
```

- For each fragment it takes the raw subtitle lines that overlap it, clips them to the fragment and moves them to the fragment's place on the new timeline. Lines outside every fragment are dropped.
- It prints `written: ... (N lines, timeline length X s)`.
- A fragment whose `.srt` is missing from the transcripts folder stops the script with FileNotFoundError.
- Possible drift, not tested: this script places fragment k at the sum of the earlier `out - in` durations, while the Kdenlive entries are frame-inclusive (one extra frame each). Subtitles may therefore run ahead of the video by about 0.04 s per fragment at 25 fps, adding up over a long list. After importing, check the last fragments; if they are off, tell the user.

## Step 6: import the subtitles (manual, in Kdenlive)

Open the `.kdenlive` file, then Project, Subtitles, Import Subtitle File, and pick the retimed `.srt`. This cannot be done from the command line.
