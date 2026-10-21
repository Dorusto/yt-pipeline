#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Generates 9:16 Shorts / Reels from 1920x1080 videos.
Run from the shorts-generator folder — no arguments needed.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import pysubs2
import yaml

from common.ffmpeg_tools import available_video_encoders, pick_video_encoder, video_encode_args

CROP_W = 608
CROP_H = 1080
SRC_W = 1920
REFRAME_SCRIPT = Path(__file__).resolve().parent / "reframe.py"

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "shorts_config.yaml"

YAML_TEMPLATE = """\
video: "MyVideo.mp4"
srt: "MyVideo_RO.srt"
# youtube_url: "https://youtu.be/..."

segments:
  - name: "Hook"
    start: "00:00:00"
    end: "00:00:56"
"""


# ---------------------------------------------------------------------------
# Setup & validation
# ---------------------------------------------------------------------------

def load_and_validate_config(interactive: bool = True) -> dict:
    if not CONFIG_PATH.exists():
        if not interactive:
            raise RuntimeError(f"{CONFIG_PATH.name} not found; create it before running non-interactively")
        CONFIG_PATH.write_text(YAML_TEMPLATE)
        print(f"Created {CONFIG_PATH.name} from template.")
        print("Fill in your video/audio/srt filenames and segments, then run again.")
        sys.exit(0)

    config = yaml.safe_load(CONFIG_PATH.read_text())
    errors = []

    if not config.get("video") or config["video"] == "MyVideo.mp4":
        errors.append("  - 'video:' missing or not filled in")
    if not config.get("srt") or config["srt"] == "MyVideo_corrected.srt":
        errors.append("  - 'srt:' missing or not filled in")
    if not config.get("segments"):
        errors.append("  - 'segments:' missing or empty")

    if errors:
        message = f"{CONFIG_PATH.name} is not complete:\n" + "\n".join(errors)
        if not interactive:
            raise RuntimeError(message)
        print(message)
        print("Fill it in and run again.")
        sys.exit(0)

    return config


def ask_video_path() -> Path:
    print("\nPath to the video file:")
    print("  (e.g. ~/Videos/MyClip/Export/video/MyClip.mp4)")
    raw = input("> ").strip()
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)
    return path


def find_file(base_path: Path, filename: str, subfolder: str, interactive: bool = True) -> Path:
    """Look for filename in: same dir, parent/subfolder/, parent/."""
    candidates = [
        base_path.parent / filename,
        base_path.parent.parent / subfolder / filename,
        base_path.parent.parent / filename,
    ]
    for c in candidates:
        if c.exists():
            return c

    if not interactive:
        raise RuntimeError(f"could not find {filename} next to {base_path}")

    print(f"\nCould not find {filename} automatically.")
    print(f"Path to {filename}:")
    raw = input("> ").strip()
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)
    return path


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def parse_time(t: str) -> float:
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def fixed_crop_filter(x_offset: int) -> str:
    return f"crop={CROP_W}:{CROP_H}:{x_offset}:0"


def piecewise_expression(points, key):
    """ffmpeg expression of a piecewise-linear curve through the points' t_s and key."""
    if not points:
        return "0"
    if len(points) == 1:
        return f"{float(points[0][key]):.2f}"
    segments = []
    for index in range(len(points) - 1):
        t0 = float(points[index]["t_s"])
        t1 = float(points[index + 1]["t_s"])
        v0 = float(points[index][key])
        v1 = float(points[index + 1][key])
        if t1 <= t0:
            continue
        slope = (v1 - v0) / (t1 - t0)
        if abs(slope) < 1e-6:
            expr = f"{v0:.2f}"
        else:
            expr = f"{v0:.2f}+(t-{t0:.3f})*{slope:.4f}"
        segments.append((t1, expr))
    result = f"{float(points[-1][key]):.2f}"
    for t1, expr in reversed(segments):
        result = f"if(lt(t,{t1:.3f}),{expr},{result})"
    return result


def simplify_points(points, max_points=40):
    """Thin the crop path to at most max_points keyframes, keeping both ends."""
    if len(points) <= max_points:
        return list(points)
    step = (len(points) - 1) / (max_points - 1)
    picked = [points[int(round(index * step))] for index in range(max_points)]
    picked[0] = points[0]
    picked[-1] = points[-1]
    return picked


def crop_filter_from_path(record) -> str:
    """Build the ffmpeg crop filter for a reframe crop path record."""
    points = simplify_points(record.get("points") or [])
    if not points:
        raise RuntimeError("crop path has no points")
    crop_w = int(record["crop_w"])
    crop_h = int(record["crop_h"])
    if crop_w != CROP_W or crop_h != CROP_H:
        raise RuntimeError(f"crop path is {crop_w}x{crop_h}, expected {CROP_W}x{CROP_H}")
    xs = {int(point["x"]) for point in points}
    ys = {int(point["y"]) for point in points}
    x_expr = str(points[0]["x"]) if len(xs) == 1 else f"'{piecewise_expression(points, 'x')}'"
    y_expr = str(points[0]["y"]) if len(ys) == 1 else f"'{piecewise_expression(points, 'y')}'"
    return f"crop={crop_w}:{crop_h}:{x_expr}:{y_expr}"


def reframe_crop_path(video_path: str, start: float, end: float, out_path) -> dict:
    """Run scripts/reframe.py for one segment and return its crop path record."""
    command = [
        sys.executable,
        str(REFRAME_SCRIPT),
        "--video",
        str(video_path),
        "--start",
        str(start),
        "--end",
        str(end),
        "--aspect",
        "9:16",
        "--out",
        str(out_path),
        "--force",
        "--json",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"reframe failed: {result.stderr.strip() or 'unknown error'}")
    return json.loads(Path(out_path).read_text(encoding="utf-8"))


def parse_srt_for_alignment(srt_path: str, start: float, end: float) -> list:
    """
    Parse corrected SRT and return WhisperX-format segments
    with timestamps relative to the segment start (0-based).
    """
    subs = pysubs2.load(srt_path)
    segments = []
    for event in subs:
        ev_start = event.start / 1000.0
        ev_end = event.end / 1000.0
        if ev_end <= start or ev_start >= end:
            continue

        text = event.plaintext.strip()

        # Entry straddles segment start — trim from first sentence boundary after proportional cut
        if ev_start < start:
            ws = text.split()
            fraction = (start - ev_start) / (ev_end - ev_start)
            n_est = int(len(ws) * fraction)
            cut = n_est
            for i in range(n_est, len(ws)):
                if ws[i].rstrip("\"'").endswith((".", "!", "?", "...")):
                    cut = i + 1
                    break
            text = " ".join(ws[cut:])
            if not text:
                continue

        # Entry straddles segment end — trim at sentence boundary near proportional cut
        if ev_end > end:
            ws = text.split()
            fraction = (end - max(ev_start, start)) / (ev_end - ev_start)
            n_est = int(len(ws) * fraction)  # floor — slightly under
            cut = n_est
            for i in range(n_est - 1, -1, -1):
                if ws[i].rstrip("\"'").endswith((".", "!", "?", "...")):
                    cut = i + 1
                    break
            text = " ".join(ws[:max(1, cut)])

        segments.append({
            "text": text,
            "start": max(0.0, ev_start - start),
            "end": min(end - start, ev_end - start),
        })
    return segments


def run_forced_alignment(audio_path: str, srt_path: str, start: float, end: float, tmpdir: str) -> list:
    """
    Extract audio segment, then use WhisperX forced alignment
    on the corrected SRT to get accurate word-level timestamps.
    Returns [{word, start, end}] with timestamps relative to segment start.
    """
    import torch
    import whisperx

    device = "cuda" if torch.cuda.is_available() else "cpu"

    seg_audio = str(Path(tmpdir) / "seg.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", audio_path, seg_audio],
        check=True, capture_output=True,
    )

    audio = whisperx.load_audio(seg_audio)
    segments = parse_srt_for_alignment(srt_path, start, end)

    if not segments:
        print("     [warn] no SRT entries found for this segment")
        return []

    model_a, metadata = whisperx.load_align_model(language_code="ro", device=device)
    result = whisperx.align(segments, model_a, metadata, audio, device=device, return_char_alignments=False)

    return [
        {"word": w["word"].strip(), "start": round(w["start"], 3), "end": round(w["end"], 3)}
        for w in result.get("word_segments", [])
        if w.get("word", "").strip()
    ]


def merge_hyphenated(words: list) -> list:
    """Merge hyphenated fragments: ["task", "-ul"] → ["task-ul"]."""
    merged = []
    for w in words:
        if w["word"].startswith("-") and merged:
            prev = merged[-1]
            merged[-1] = {"word": prev["word"] + w["word"], "start": prev["start"], "end": w["end"]}
        else:
            merged.append(dict(w))
    return merged


def generate_karaoke_ass(words: list, output_path: str) -> None:
    LINE_WORDS = 3
    words = merge_hyphenated(words)

    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = "608"
    subs.info["PlayResY"] = "1080"
    subs.styles["Default"] = pysubs2.SSAStyle(
        fontname="Arial",
        fontsize=70,
        primarycolor=pysubs2.Color(255, 255, 255, 0),
        outlinecolor=pysubs2.Color(0, 0, 0, 0),
        outline=3,
        shadow=1,
        bold=True,
        alignment=2,
        marginv=60,
    )

    for i in range(0, len(words), LINE_WORDS):
        line = words[i : i + LINE_WORDS]
        for j, active in enumerate(line):
            if j < len(line) - 1:
                end_ms = int(line[j + 1]["start"] * 1000)
            elif i + LINE_WORDS < len(words):
                end_ms = int(words[i + LINE_WORDS]["start"] * 1000)
            else:
                end_ms = int(active["end"] * 1000)
            parts = []
            for k, w in enumerate(line):
                if k == j:
                    parts.append(r"{\c&H00FFFF&}" + w["word"] + r"{\c&HFFFFFF&}")
                else:
                    parts.append(w["word"])
            subs.append(
                pysubs2.SSAEvent(
                    start=pysubs2.make_time(ms=int(active["start"] * 1000)),
                    end=pysubs2.make_time(ms=end_ms),
                    text=" ".join(parts),
                )
            )

    subs.save(output_path)


def words_from_srt_segments(segments: list) -> list:
    """
    Construiește o listă de cuvinte cu timpi estimați (interpolare uniformă
    în intervalul [start,end] al fiecărei linii SRT), fără WhisperX — sursa
    e direct textul .srt deja corectat, deci nu poate pierde cuvinte.
    Precizia per-cuvânt e aproximativă, nu sincronizată exact pe audio.
    """
    words = []
    for seg in segments:
        text = seg["text"].replace("\n", " ").replace("\r", " ")
        toks = text.split()
        if not toks:
            continue
        dur = seg["end"] - seg["start"]
        step = dur / len(toks)
        for i, tok in enumerate(toks):
            words.append({
                "word": tok,
                "start": seg["start"] + i * step,
                "end": seg["start"] + (i + 1) * step,
            })
    return words


def generate_plain_ass(segments: list, output_path: str) -> None:
    """
    Fallback fără highlight per-cuvânt: text direct din .srt corectat (deja
    verificat de om), fără WhisperX. Nu poate pierde cuvinte, pentru că nu
    depinde de alinierea audio — doar de segmentele SRT deja parsate.
    Randare libass face wrap automat la PlayResX, nu tăiem noi liniile.
    """
    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = "608"
    subs.info["PlayResY"] = "1080"
    subs.styles["Default"] = pysubs2.SSAStyle(
        fontname="Arial",
        fontsize=60,
        primarycolor=pysubs2.Color(255, 255, 255, 0),
        outlinecolor=pysubs2.Color(0, 0, 0, 0),
        outline=3,
        shadow=1,
        bold=True,
        alignment=2,
        marginv=60,
        marginl=20,
        marginr=20,
    )
    for seg in segments:
        subs.append(
            pysubs2.SSAEvent(
                start=pysubs2.make_time(s=seg["start"]),
                end=pysubs2.make_time(s=seg["end"]),
                text=seg["text"].replace("\n", " ").replace("\r", " "),
            )
        )
    subs.save(output_path)


def render_short(
    video_path: str, crop_filter: str, start: float, end: float,
    ass_path: str, output_path: str, encoder: str | None = None,
) -> str:
    encoder = encoder or pick_video_encoder()

    def command(enc: str) -> list:
        return [
            "ffmpeg", "-y",
            "-ss", str(start), "-to", str(end), "-i", video_path,
            "-vf", f"{crop_filter},ass={ass_path}",
            *video_encode_args(enc),
            "-c:a", "aac", "-b:a", "192k",
            output_path,
        ]

    try:
        subprocess.run(command(encoder), check=True)
    except subprocess.CalledProcessError:
        if encoder != "libx264" and "libx264" in available_video_encoders():
            print(f"     [warn] {encoder} render failed, retrying with libx264")
            encoder = "libx264"
            subprocess.run(command(encoder), check=True)
        else:
            raise
    return encoder


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate 9:16 Shorts with karaoke subtitles.")
    parser.add_argument("--video", type=str, default=None)
    parser.add_argument("--audio", type=str, default=None)
    parser.add_argument("--srt", type=str, default=None)
    parser.add_argument("--encoder", type=str, default=None,
                        help="force a video encoder (default: auto, h264_nvenc with an NVIDIA GPU, h264_videotoolbox on macOS, else libx264)")
    parser.add_argument("--yes", action="store_true",
                        help="never prompt; fail instead when a file or config entry is missing")
    parser.add_argument("--skip-alignment", action="store_true",
                        help="Use existing _words.json instead of re-running alignment")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def main(argv=None) -> int:
    args, _ = build_parser().parse_known_args(argv)
    interactive = not args.yes

    def log(message=""):
        if not args.json:
            print(message)

    log("=== Shorts Generator ===")

    try:
        config = load_and_validate_config(interactive=interactive)

        if args.video:
            video_path = Path(args.video).expanduser().resolve()
            if not video_path.exists():
                raise RuntimeError(f"file not found: {video_path}")
        elif interactive:
            video_path = ask_video_path()
        else:
            raise RuntimeError("--video is required with --yes")

        if args.audio:
            audio_path = Path(args.audio).expanduser().resolve()
        elif config.get("audio") and config["audio"] not in ("MyVideo.mp3",):
            audio_path = find_file(video_path, config["audio"], "audio", interactive=interactive)
        else:
            audio_path = video_path

        srt_path = (
            Path(args.srt).expanduser().resolve()
            if args.srt
            else find_file(video_path, config["srt"], "subtitles", interactive=interactive)
        )

        encoder = args.encoder or os.environ.get("YT_VIDEO_ENCODER") or pick_video_encoder()
    except RuntimeError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"[error] {exc}", file=sys.stderr)
        return 1

    out_dir = video_path.parent
    shorts_dir = out_dir / "shorts"
    auto_dir = out_dir / "auto"
    shorts_dir.mkdir(exist_ok=True)
    auto_dir.mkdir(exist_ok=True)

    log(f"\nVideo:   {video_path}")
    log(f"Audio:   {audio_path}")
    log(f"SRT:     {srt_path}")
    log(f"Output:  {shorts_dir}")
    log(f"Encoder: {encoder}")

    segments = config["segments"]
    results = []
    for i, seg in enumerate(segments, 1):
        name = seg["name"]
        start = parse_time(seg["start"])
        end = parse_time(seg["end"])

        log(f"\n[{i}/{len(segments)}] {name}  ({seg['start']} → {seg['end']})")

        if "x_offset" in seg:
            x_offset = int(seg["x_offset"])
            log(f"  → manual x_offset = {x_offset}px (from config)")
            crop_filter = fixed_crop_filter(x_offset)
            crop_path_file = None
        else:
            log("  → reframe (subject tracking)...")
            crop_path_file = auto_dir / f"{name}_reframe.json"
            try:
                record = reframe_crop_path(str(video_path), start, end, crop_path_file)
                crop_filter = crop_filter_from_path(record)
                moving = ":'" in crop_filter
                log(
                    f"     crop path → {crop_path_file.name} "
                    f"({len(record['points'])} points, {'moving' if moving else 'fixed'})"
                )
                x_offset = None
            except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
                log(f"     [warn] {exc} — using centre crop")
                crop_filter = fixed_crop_filter((SRC_W - CROP_W) // 2)
                crop_path_file = None
                x_offset = (SRC_W - CROP_W) // 2

        log("  → cuvinte din .srt corectat (fără WhisperX — nu pierde cuvinte)...")
        srt_segments = parse_srt_for_alignment(str(srt_path), start, end)
        words = words_from_srt_segments(srt_segments)
        log(f"     {len(words)} cuvinte (timing interpolat, nu aliniat pe audio)")

        ass_path = auto_dir / f"{name}_karaoke.ass"
        generate_karaoke_ass(words, str(ass_path))
        log(f"     ASS generat → {ass_path.name}")

        output = shorts_dir / f"Short{i}-{name}.mp4"
        log(f"  → render → {output.name}")
        used_encoder = render_short(str(video_path), crop_filter, start, end, str(ass_path), str(output), encoder)
        log(f"  ✓ {output}")
        results.append({
            "name": name,
            "start": seg["start"],
            "end": seg["end"],
            "x_offset": x_offset,
            "crop_path": str(crop_path_file) if crop_path_file else None,
            "words": len(words),
            "output": str(output),
            "encoder": used_encoder,
        })

    log(f"\nDone! {len(segments)} short(s) in {shorts_dir}")

    if args.json:
        print(json.dumps({"ok": True, "shorts": results, "output_dir": str(shorts_dir)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
