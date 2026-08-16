#!/usr/bin/env python3
"""
Generates 9:16 Shorts / Reels from 1920x1080 videos.
Run from the shorts-generator folder — no arguments needed.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import pysubs2
import torch
import whisperx
import yaml

CROP_W = 608
CROP_H = 1080
SRC_W = 1920
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

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

def load_and_validate_config() -> dict:
    if not CONFIG_PATH.exists():
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
        print(f"{CONFIG_PATH.name} is not complete:\n" + "\n".join(errors))
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


def find_file(base_path: Path, filename: str, subfolder: str) -> Path:
    """Look for filename in: same dir, parent/subfolder/, parent/."""
    candidates = [
        base_path.parent / filename,
        base_path.parent.parent / subfolder / filename,
        base_path.parent.parent / filename,
    ]
    for c in candidates:
        if c.exists():
            return c

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


def detect_face_offset(video_path: str, start: float, end: float) -> int:
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    x_centers = []
    total_frames = 0

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    t = start
    while t < end:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ret, frame = cap.read()
        if not ret:
            break
        total_frames += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
        if len(faces) > 0:
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            x_centers.append(x + w / 2)
        t += 2.0

    cap.release()

    print(f"     face detected in {len(x_centers)}/{total_frames} sampled frames")

    if not x_centers:
        print("     [warn] no face detected — using center (set x_offset manually in config)")
        return (SRC_W - CROP_W) // 2

    avg_x = sum(x_centers) / len(x_centers)
    return max(0, min(int(avg_x - CROP_W / 2), SRC_W - CROP_W))


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

    model_a, metadata = whisperx.load_align_model(language_code="ro", device=DEVICE)
    result = whisperx.align(segments, model_a, metadata, audio, device=DEVICE, return_char_alignments=False)

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
    LINE_WORDS = 5
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


def render_short(
    video_path: str, x_offset: int, start: float, end: float,
    ass_path: str, output_path: str,
) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(start), "-to", str(end), "-i", video_path,
            "-vf", f"crop={CROP_W}:{CROP_H}:{x_offset}:0,ass={ass_path}",
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23",
            "-c:a", "aac", "-b:a", "192k",
            output_path,
        ],
        check=True,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--video", type=str, default=None)
    parser.add_argument("--audio", type=str, default=None)
    parser.add_argument("--srt", type=str, default=None)
    parser.add_argument("--skip-alignment", action="store_true",
                        help="Use existing _words.json instead of re-running alignment")
    args, _ = parser.parse_known_args()

    print("=== Shorts Generator ===")

    config = load_and_validate_config()

    if args.video:
        video_path = Path(args.video).expanduser().resolve()
        if not video_path.exists():
            print(f"File not found: {video_path}")
            sys.exit(1)
    else:
        video_path = ask_video_path()

    if args.audio:
        audio_path = Path(args.audio).expanduser().resolve()
    elif config.get("audio") and config["audio"] not in ("MyVideo.mp3",):
        audio_path = find_file(video_path, config["audio"], "audio")
    else:
        audio_path = video_path  # extract audio directly from video

    srt_path = (
        Path(args.srt).expanduser().resolve()
        if args.srt
        else find_file(video_path, config["srt"], "subtitles")
    )

    out_dir = video_path.parent
    shorts_dir = out_dir / "shorts"
    auto_dir = out_dir / "auto"
    shorts_dir.mkdir(exist_ok=True)
    auto_dir.mkdir(exist_ok=True)

    print(f"\nVideo:  {video_path}")
    print(f"Audio:  {audio_path}")
    print(f"SRT:    {srt_path}")
    print(f"Output: {shorts_dir}")

    segments = config["segments"]
    for i, seg in enumerate(segments, 1):
        name = seg["name"]
        start = parse_time(seg["start"])
        end = parse_time(seg["end"])

        print(f"\n[{i}/{len(segments)}] {name}  ({seg['start']} → {seg['end']})")

        if "x_offset" in seg:
            x_offset = int(seg["x_offset"])
            print(f"  → manual x_offset = {x_offset}px (from config)")
        else:
            print("  → detecting face...")
            x_offset = detect_face_offset(str(video_path), start, end)
            print(f"     x_offset = {x_offset}px")

        words_json = auto_dir / f"{name}_words.json"
        if args.skip_alignment and words_json.exists():
            print("  → loading existing words JSON (--skip-alignment)...")
            words = json.loads(words_json.read_text())
        else:
            print("  → forced alignment (WhisperX)...")
            with tempfile.TemporaryDirectory() as tmpdir:
                words = run_forced_alignment(str(audio_path), str(srt_path), start, end, tmpdir)
            words_json.write_text(json.dumps(words, ensure_ascii=False, indent=2))
        print(f"     {len(words)} words → {words_json.name}")

        ass_path = auto_dir / f"{name}_karaoke.ass"
        generate_karaoke_ass(words, str(ass_path))
        print(f"     ASS generated → {ass_path.name}")

        output = shorts_dir / f"Short{i}-{name}.mp4"
        print(f"  → render → {output.name}")
        render_short(str(video_path), x_offset, start, end, str(ass_path), str(output))
        print(f"  ✓ {output}")

    print(f"\nDone! {len(segments)} short(s) in {shorts_dir}")


if __name__ == "__main__":
    main()
