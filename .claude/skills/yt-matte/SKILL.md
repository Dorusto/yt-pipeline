---
name: yt-matte
description: Remove the background of a clip with RobustVideoMatting (RVM) and write a ProRes 4444 .mov with alpha, ready to place over screen capture. Use when the user wants the background removed from a clip, a talking-head cut-out with alpha, a ProRes 4444 with transparency, or a matted range of a clip. Trigger phrases - "scoate fundalul", "decupează persoana", "matte", "remove the background", "cut out the subject", "alpha video", "ProRes 4444 cu alpha". Not for still photos (that is `thumbnail`) and not for placing the clip on a timeline (the editor's job).
compatibility: Run from the repository root. Needs the repo .venv with torch + torchvision (CPU is fine) and ffmpeg/ffprobe on PATH. The first run downloads the RVM code and the rvm_resnet50.pth weights through torch.hub into ~/.cache/torch/hub/ (never into the repository). Makes no LLM calls, so the run costs 0 USD.
---

# yt-matte

Cut a talking-head clip out of its background and write a ProRes 4444 `.mov` with
alpha, so the editor can drop it over screen capture. RVM (RobustVideoMatting,
resnet50) runs through `torch.hub` on CUDA when a GPU is present, else on CPU.
Only the requested time range is processed. Placing the matte on a timeline is the
editor's job — this tool only produces the file.

## Step 1: write the matte

```bash
.venv/bin/python scripts/matte.py /path/to/clip.mp4 --json
.venv/bin/python scripts/matte.py /path/to/clip.mp4 --start 10 --end 40 --ratio 0.25 --out /tmp/clip_matte.mov
```

- `VIDEO` is the input clip (`.mp4`/`.mov`) and is read-only.
- `--start` / `--end` are float seconds and limit the range (end exclusive;
  default: the whole clip). `0 <= start < end <= duration`, else exit 2 naming
  the values and the duration.
- `--ratio` is RVM's `downsample_ratio`, `> 0` and `<= 1`, an explicit override.
  Its default is auto from the input height: `0.125` at 4K (`>= 1600`), `0.25`
  at 1080p (`>= 900`), `0.4` at 720p and smaller. The `--json` summary reports
  the effective value actually used.
- `--out` is the output `.mov` (default `<video parent>/<stem>_matte.mov`). An
  `--out` that resolves to the input is refused (exit 2) — an original is never
  overwritten. Re-running overwrites the output file (a derived artifact).
- `--json` prints one summary line: `output`, `frames`, `width`, `height`, `fps`,
  `start_s`, `end_s`, `downsample_ratio`, `device` (`cuda`|`cpu`), `wall_time_s`,
  `llm_cost_usd` (always 0.0). Without it, one human line with the path and the
  frame count.

Exit codes: `0` success, `2` user/environment error (missing input, bad range,
missing ffmpeg/ffprobe, missing torch), `1` unexpected runtime failure (e.g.
ffmpeg failed mid-run, with the tail of its stderr).

## What the output is

A ProRes 4444 `.mov` with alpha, **video only** — the editor keeps the original's
audio. Resolution and fps match the input range. The per-frame foreground and
alpha are written as two PNG sequences in a temp folder and merged with one
ffmpeg command (`alphamerge` → `format=yuva444p10le` → `prores_ks -profile:v 4444`).

## How to check the alpha

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=pix_fmt -of csv=p=0 clip_matte.mov
ffmpeg -i clip_matte.mov -vf alphaextract,signalstats -f null -
```

The first command must print a `yuva444p*` pixel format (4:4:4 with alpha). On
ffmpeg 6.0+ a ProRes 4444 stream is decoded as 12-bit, so the probe prints
`yuva444p12le`; older ffmpeg prints `yuva444p10le`. Both are the same ProRes 4444
with alpha. The second command shows the alpha plane's min/max/mean.

## Licence

RVM is GPL-3.0. Its code is never vendored into this repository: `torch.hub`
fetches it (and the weights) into `~/.cache/torch/hub/` on first run, so the GPL
obligations stay with the downloaded code. The videos this tool produces are not
GPL.
