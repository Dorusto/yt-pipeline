#!/usr/bin/env python3
"""
Generează Shorts / Reels din videouri 1920×1080.
Rulează din ~/Proiecte-AI/YouTube/shorts-generator/ fără argumente.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import pysubs2
import yaml

CROP_W = 608
CROP_H = 1080
SRC_W = 1920

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "shorts_config.yaml"

YAML_TEMPLATE = """\
video: "NumeVideo.mp4"
audio: "NumeVideo.mp3"

segments:
  - name: "Hook"
    start: "00:00:00"
    end: "00:00:56"

  - name: "Segment2"
    start: "01:02:00"
    end: "01:52:00"
"""


# ---------------------------------------------------------------------------
# Setup & validare
# ---------------------------------------------------------------------------

def load_and_validate_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(YAML_TEMPLATE)
        print(f"Am creat {CONFIG_PATH.name} cu un template.")
        print("Completează-l cu numele fișierelor și segmentele dorite, apoi rulează din nou.")
        sys.exit(0)

    config = yaml.safe_load(CONFIG_PATH.read_text())
    errors = []

    if not config.get("video") or config["video"] == "NumeVideo.mp4":
        errors.append("  - 'video:' lipsește sau e necompletat")
    if not config.get("audio") or config["audio"] == "NumeVideo.mp3":
        errors.append("  - 'audio:' lipsește sau e necompletat")
    if not config.get("segments"):
        errors.append("  - 'segments:' lipsește sau e gol")

    if errors:
        print(f"{CONFIG_PATH.name} nu e completat:\n" + "\n".join(errors))
        print("Completează-l și rulează din nou.")
        sys.exit(0)

    return config


def ask_video_path() -> Path:
    print("\nCalea către fișierul video:")
    print("  (ex: ~/Videos/Lenea/Export/video/Lenea.mp4)")
    raw = input("> ").strip()
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        print(f"Fișierul nu există: {path}")
        sys.exit(1)
    return path


def find_audio(video_path: Path, audio_name: str) -> Path:
    """
    Caută audio_name în: același folder, folderul audio/ din parent, parent direct.
    """
    candidates = [
        video_path.parent / audio_name,
        video_path.parent.parent / "audio" / audio_name,
        video_path.parent.parent / audio_name,
    ]
    for c in candidates:
        if c.exists():
            return c

    print(f"\nNu am găsit {audio_name} automat.")
    print("Calea către fișierul audio:")
    raw = input("> ").strip()
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        print(f"Fișierul nu există: {path}")
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

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    t = start
    while t < end:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ret, frame = cap.read()
        if not ret:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
        if len(faces) > 0:
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            x_centers.append(x + w / 2)
        t += 2.0

    cap.release()

    if not x_centers:
        print("     [warn] față nedetectată — folosesc centru")
        return (SRC_W - CROP_W) // 2

    avg_x = sum(x_centers) / len(x_centers)
    return max(0, min(int(avg_x - CROP_W / 2), SRC_W - CROP_W))


def run_whisper(audio_path: str, start: float, end: float, tmpdir: str) -> list:
    seg_audio = str(Path(tmpdir) / "seg.wav")

    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", audio_path, seg_audio],
        check=True, capture_output=True,
    )
    subprocess.run(
        [
            "whisper", seg_audio,
            "--output_format", "json",
            "--output_dir", tmpdir,
            "--language", "ro",
            "--model", "turbo",
            "--word_timestamps", "True",
        ],
        check=True,
    )

    data = json.loads((Path(tmpdir) / "seg.json").read_text())
    return [
        {"word": w["word"].strip(), "start": w["start"], "end": w["end"]}
        for seg in data.get("segments", [])
        for w in seg.get("words", [])
        if w.get("word", "").strip()
    ]


def merge_hyphenated(words: list) -> list:
    """Reunește fragmentele cu cratimă: ["task", "-ul"] → ["task-ul"]."""
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
            # Extind până la start-ul cuvântului următor (elimină clipirea)
            if j < len(line) - 1:
                end_ms = int(line[j + 1]["start"] * 1000)
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
    parser.add_argument("--skip-whisper", action="store_true")
    args, _ = parser.parse_known_args()

    print("=== Shorts Generator ===")

    config = load_and_validate_config()

    if args.video:
        video_path = Path(args.video).expanduser().resolve()
        if not video_path.exists():
            print(f"Fișierul nu există: {video_path}")
            sys.exit(1)
    else:
        video_path = ask_video_path()

    if args.audio:
        audio_path = Path(args.audio).expanduser().resolve()
    else:
        audio_path = find_audio(video_path, config["audio"])

    # Shorts și auto lângă fișierul video
    out_dir = video_path.parent
    shorts_dir = out_dir / "shorts"
    auto_dir = out_dir / "auto"
    shorts_dir.mkdir(exist_ok=True)
    auto_dir.mkdir(exist_ok=True)

    print(f"\nVideo:  {video_path}")
    print(f"Audio:  {audio_path}")
    print(f"Output: {shorts_dir}")
    print(f"Config: {len(config['segments'])} segmente\n")

    segments = config["segments"]
    for i, seg in enumerate(segments, 1):
        name = seg["name"]
        start = parse_time(seg["start"])
        end = parse_time(seg["end"])

        print(f"[{i}/{len(segments)}] {name}  ({seg['start']} → {seg['end']})")

        print("  → detectez fața...")
        x_offset = detect_face_offset(str(video_path), start, end)
        print(f"     x_offset = {x_offset}px")

        words_json = auto_dir / f"{name}_words.json"
        if args.skip_whisper and words_json.exists():
            print("  → Citesc JSON existent (--skip-whisper)...")
            words = json.loads(words_json.read_text())
        else:
            print("  → Whisper word timestamps...")
            with tempfile.TemporaryDirectory() as tmpdir:
                words = run_whisper(str(audio_path), start, end, tmpdir)
            words_json.write_text(json.dumps(words, ensure_ascii=False, indent=2))
        print(f"     {len(words)} cuvinte → {words_json.name}")

        ass_path = auto_dir / f"{name}_karaoke.ass"
        generate_karaoke_ass(words, str(ass_path))
        print(f"     ASS generat → {ass_path.name}")

        output = shorts_dir / f"Short{i}-{name}.mp4"
        print(f"  → render → {output.name}")
        render_short(str(video_path), x_offset, start, end, str(ass_path), str(output))
        print(f"  ✓ {output}\n")

    print(f"Gata! {len(segments)} short-uri în {shorts_dir}")


if __name__ == "__main__":
    main()
