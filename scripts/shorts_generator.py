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
import time
from pathlib import Path

import cv2
import pysubs2
import yaml

from common.brand import BrandError, load_brand, parse_hex_color, resolve_font
from common.corrections import (
    CORRECTIONS_EXAMPLE_FILE,
    CORRECTIONS_FILE,
    CorrectionsError,
    apply_corrections,
    load_corrections,
    resolve_corrections_file,
)
from common.ffmpeg_tools import available_video_encoders, pick_video_encoder, video_encode_args
from common.schema import SchemaError, validate_or_raise
from common.summary import summary_fields
from common.timeline_words import (
    TimelineWordsError,
    timeline_words_from_file,
    word_in_window,
)


class UsageError(RuntimeError):
    """A required flag is missing and the prompt that would ask for it has no TTY."""


CROP_W = 608
CROP_H = 1080
SRC_W = 1920
SRC_H = 1080
REFRAME_SCRIPT = Path(__file__).resolve().parent / "reframe.py"

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "shorts_config.yaml"

PHRASE_END_CHARS = (".", "!", "?", "…")
PHRASE_GAP_S = 0.6
LINE_MAX_CHARS = 18
CAPTION_MAX_LINES = 2
DEFAULT_SAFE_ZONE_FRAC = 0.33
DEFAULT_SCALE_ACTIVE_WORD = False

LAYOUTS = ("face", "split")
DEFAULT_SPLIT = {
    "face_h_frac": 0.45,
    "face_cell_frac": 0.33,
    "screen_h_frac": 0.75,
    "screen_x_frac": 0.5,
    "screen_y_frac": 0.5,
}

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

def load_and_validate_config(interactive: bool = True, require_segments: bool = True) -> dict:
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
    if require_segments and not config.get("segments"):
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
    if not sys.stdin.isatty():
        raise UsageError("--video is required when stdin is not a terminal")
    print("\nPath to the video file:")
    print("  (e.g. <clip-folder>/Export/video/<clip>.mp4)")
    raw = input("> ").strip()
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)
    return path


def find_file(base_path: Path, filename: str, subfolder: str, flag: str, interactive: bool = True) -> Path:
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
    if not sys.stdin.isatty():
        raise UsageError(f"could not find {filename} next to {base_path}; pass {flag}")

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


def format_time(seconds: float) -> str:
    """Render timeline seconds as ``HH:MM:SS.d``, the shape ``parse_time`` reads."""
    total = max(0.0, float(seconds))
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    return f"{hours:02d}:{minutes:02d}:{total % 60:04.1f}"


def load_candidates_segments(path: str) -> list:
    """Read a shorts_candidates.json and turn its candidates into segment dicts."""
    try:
        document = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError(f"candidates file not found: {path}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"candidates file is not valid JSON: {exc}")
    except OSError as exc:
        raise RuntimeError(f"cannot read candidates file '{path}': {exc}")
    try:
        validate_or_raise(document, "shorts_candidates")
    except SchemaError as exc:
        raise RuntimeError(f"invalid candidates file '{path}': {exc}")
    return [
        {
            "name": f"short{candidate['rank']}",
            "start": format_time(candidate["start_s"]),
            "end": format_time(candidate["end_s"]),
            **({"layout": candidate["layout"]} if candidate.get("layout") else {}),
        }
        for candidate in sorted(document["candidates"], key=lambda item: item["rank"])
    ]


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


def centre_crop_record() -> dict:
    """A still crop path record on the source centre, the split layout's fallback."""
    return {
        "crop_w": CROP_W,
        "crop_h": CROP_H,
        "points": [{"t_s": 0.0, "x": (SRC_W - CROP_W) // 2, "y": (SRC_H - CROP_H) // 2}],
    }


def _coord_expr(points, key):
    """Format a crop coordinate: a bare number when constant, else a quoted expression."""
    values = {round(float(point[key]), 3) for point in points}
    if len(values) == 1:
        return f"{float(points[0][key]):.2f}"
    return f"'{piecewise_expression(points, key)}'"


def split_filter_from_path(record, split) -> str:
    """Build the face-over-screen vstack filtergraph for a reframe crop path record."""
    points = simplify_points(record.get("points") or [])
    if not points:
        raise RuntimeError("crop path has no points")
    face_cell_h = round(CROP_H * split["face_cell_frac"])
    screen_cell_h = CROP_H - face_cell_h
    face_h = round(SRC_H * split["face_h_frac"])
    face_w = round(face_h * CROP_W / face_cell_h)
    screen_h = round(SRC_H * split["screen_h_frac"])
    screen_w = round(screen_h * CROP_W / screen_cell_h)

    face_points = []
    for point in points:
        centre_x = float(point["x"]) + CROP_W / 2
        centre_y = float(point["y"]) + CROP_H / 2
        face_points.append({
            "t_s": point["t_s"],
            "x": min(max(centre_x - face_w / 2, 0), SRC_W - face_w),
            "y": min(max(centre_y - face_h / 2, 0), SRC_H - face_h),
        })
    face_x = _coord_expr(face_points, "x")
    face_y = _coord_expr(face_points, "y")

    screen_x = min(max(SRC_W * split["screen_x_frac"] - screen_w / 2, 0), SRC_W - screen_w)
    screen_y = min(max(SRC_H * split["screen_y_frac"] - screen_h / 2, 0), SRC_H - screen_h)

    return (
        f"split=2[fa][sc];"
        f"[fa]crop={face_w}:{face_h}:{face_x}:{face_y},scale={CROP_W}:{face_cell_h}[fc];"
        f"[sc]crop={screen_w}:{screen_h}:{screen_x:.2f}:{screen_y:.2f},scale={CROP_W}:{screen_cell_h}[sc2];"
        f"[fc][sc2]vstack"
    )


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


def load_brand_kit(path=None):
    """Load the brand kit, turning a missing or invalid kit into a user error."""
    try:
        return load_brand(path)
    except BrandError as exc:
        raise RuntimeError(str(exc))


def resolve_caption_font_dir(brand, brand_dir):
    """Return the caption font directory, or raise naming the path and the brand key."""
    try:
        return resolve_font(brand, brand_dir, "caption").parent
    except BrandError as exc:
        raise RuntimeError(str(exc))


def hex_to_ass_color(value):
    """Turn a ``#RRGGBB`` brand colour into a pysubs2 colour (ASS stores BGR)."""
    red, green, blue = parse_hex_color(value)
    return pysubs2.Color(red, green, blue, 0)


def hex_to_ass_tag(value):
    """Turn a ``#RRGGBB`` brand colour into an inline ASS ``&HBBGGRR&`` tag."""
    red, green, blue = parse_hex_color(value)
    return f"&H{blue:02X}{green:02X}{red:02X}&"


def caption_style(brand, safe_zone_frac):
    """Build the ASS caption style from the brand kit and the safe-zone fraction."""
    caption = brand["fonts"]["caption"]
    fontsize = round(caption["size_px"] * 1080 / brand["canvas"]["height"])
    return pysubs2.SSAStyle(
        fontname=caption["family"],
        fontsize=fontsize,
        primarycolor=hex_to_ass_color(brand["colors"]["ink"]),
        outlinecolor=hex_to_ass_color(brand["colors"]["night"]),
        outline=3,
        shadow=1,
        bold=True,
        alignment=2,
        marginv=round(1080 * safe_zone_frac),
    )


def split_phrases(words: list) -> list:
    """Split a merged word list into phrases on sentence ends and pauses over 0.6 s."""
    phrases = []
    current = []
    for word in words:
        if current and word["start"] - current[-1]["end"] > PHRASE_GAP_S:
            phrases.append(current)
            current = []
        current.append(word)
        if word["word"].endswith(PHRASE_END_CHARS):
            phrases.append(current)
            current = []
    if current:
        phrases.append(current)
    return phrases


def pack_lines(phrase: list) -> list:
    """Greedily pack a phrase's words into lines of at most 18 characters."""
    lines = []
    current = []
    length = 0
    for word in phrase:
        text = word["word"]
        if len(text) > LINE_MAX_CHARS:
            if current:
                lines.append(current)
                current = []
                length = 0
            lines.append([word])
            continue
        added = len(text) if not current else len(text) + 1
        if current and length + added > LINE_MAX_CHARS:
            lines.append(current)
            current = [word]
            length = len(text)
        else:
            current.append(word)
            length += added
    if current:
        lines.append(current)
    return lines


def caption_lines(phrase: list) -> list:
    """Split a phrase into captions of at most two lines each."""
    lines = pack_lines(phrase)
    return [
        lines[index:index + CAPTION_MAX_LINES]
        for index in range(0, len(lines), CAPTION_MAX_LINES)
    ]


def words_for_segment(words: list, start: float, end: float) -> list:
    """Select the kept words of ``[start, end)`` (the shared midpoint rule) and shift them to segment time."""
    return [
        {
            "word": word["word"],
            "start": word["start"] - start,
            "end": word["end"] - start,
        }
        for word in words
        if word_in_window(word, start, end)
    ]


def karaoke_settings(config: dict):
    """Return ``(safe_zone_frac, scale_active_word)`` from the config's ``karaoke:`` mapping."""
    karaoke = config.get("karaoke")
    if karaoke is None:
        return DEFAULT_SAFE_ZONE_FRAC, DEFAULT_SCALE_ACTIVE_WORD
    if not isinstance(karaoke, dict):
        raise RuntimeError("'karaoke:' must be a mapping with safe_zone_frac and scale_active_word")
    return (
        karaoke.get("safe_zone_frac", DEFAULT_SAFE_ZONE_FRAC),
        karaoke.get("scale_active_word", DEFAULT_SCALE_ACTIVE_WORD),
    )


def split_settings(config: dict) -> dict:
    """Return the ``split:`` fractions, filling missing keys with the defaults."""
    split = config.get("split")
    if split is None:
        return dict(DEFAULT_SPLIT)
    if not isinstance(split, dict):
        raise RuntimeError(
            "'split:' must be a mapping with face_h_frac, face_cell_frac, screen_h_frac, "
            "screen_x_frac and screen_y_frac"
        )
    return {key: split.get(key, default) for key, default in DEFAULT_SPLIT.items()}


def resolve_layout(segment: dict, config: dict) -> str:
    """Resolve a segment's layout: segment, then top-level config, then the face default."""
    value = segment.get("layout") or config.get("layout") or "face"
    if value not in LAYOUTS:
        raise RuntimeError(f"unknown layout '{value}' (expected one of: {', '.join(LAYOUTS)})")
    return value


def generate_karaoke_ass(
    words: list,
    output_path: str,
    brand: dict | None = None,
    safe_zone_frac: float = DEFAULT_SAFE_ZONE_FRAC,
    scale_active_word: bool = DEFAULT_SCALE_ACTIVE_WORD,
) -> None:
    """Write the karaoke ASS: one event per active word, styled from the brand kit."""
    if brand is None:
        brand, _ = load_brand_kit()
    words = merge_hyphenated(words)

    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = "608"
    subs.info["PlayResY"] = "1080"
    subs.styles["Default"] = caption_style(brand, safe_zone_frac)

    active_tag = r"{\c" + hex_to_ass_tag(brand["colors"]["warm"]) + "}"
    reset_tag = r"{\c" + hex_to_ass_tag(brand["colors"]["ink"]) + "}"
    scale_tag = r"{\fscx106\fscy106}"
    reset_scale_tag = r"{\fscx100\fscy100}"

    for phrase in split_phrases(words):
        for caption in caption_lines(phrase):
            flat = [word for line in caption for word in line]
            for active_index, active in enumerate(flat):
                if active_index < len(flat) - 1:
                    end_ms = int(flat[active_index + 1]["start"] * 1000)
                else:
                    end_ms = int(active["end"] * 1000)
                line_texts = []
                index = 0
                for line in caption:
                    word_texts = []
                    for word in line:
                        if index == active_index:
                            prefix = active_tag + (scale_tag if scale_active_word else "")
                            suffix = (reset_scale_tag if scale_active_word else "") + reset_tag
                            word_texts.append(prefix + word["word"] + suffix)
                        else:
                            word_texts.append(word["word"])
                        index += 1
                    line_texts.append(" ".join(word_texts))
                subs.append(
                    pysubs2.SSAEvent(
                        start=pysubs2.make_time(ms=int(active["start"] * 1000)),
                        end=pysubs2.make_time(ms=end_ms),
                        text=r"\N".join(line_texts),
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


def generate_plain_ass(
    segments: list,
    output_path: str,
    brand: dict | None = None,
    safe_zone_frac: float = DEFAULT_SAFE_ZONE_FRAC,
) -> None:
    """
    Fallback fără highlight per-cuvânt: text direct din .srt corectat (deja
    verificat de om), fără WhisperX. Nu poate pierde cuvinte, pentru că nu
    depinde de alinierea audio — doar de segmentele SRT deja parsate.
    Randare libass face wrap automat la PlayResX, nu tăiem noi liniile.
    """
    if brand is None:
        brand, _ = load_brand_kit()
    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = "608"
    subs.info["PlayResY"] = "1080"
    style = caption_style(brand, safe_zone_frac)
    style.marginl = 20
    style.marginr = 20
    subs.styles["Default"] = style
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
    ass_path: str, output_path: str, encoder: str | None = None, font_dir=None,
) -> str:
    encoder = encoder or pick_video_encoder()
    ass_filter = f"ass={ass_path}"
    if font_dir:
        ass_filter += f":fontsdir={font_dir}"

    def command(enc: str) -> list:
        return [
            "ffmpeg", "-y",
            "-ss", str(start), "-to", str(end), "-i", video_path,
            "-vf", f"{crop_filter},{ass_filter}",
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
    parser.add_argument("--candidates", type=str, default=None,
                        help="shorts_candidates.json; use its candidates as the segments instead of shorts_config.yaml")
    parser.add_argument("--words", type=str, default=None,
                        help="timeline _words.json (subs_from_edit.py output); real word times instead of SRT interpolation")
    parser.add_argument("--corrections", type=str, default=None,
                        help="wrong|correct corrections file applied to --words text (default: scripts/corrections.txt)")
    parser.add_argument("--brand", type=str, default=None,
                        help="brand kit JSON (default: brand.json at the repo root)")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def main(argv=None) -> int:
    started = time.monotonic()
    args, _ = build_parser().parse_known_args(argv)
    interactive = not args.yes

    def log(message=""):
        if not args.json:
            print(message)

    log("=== Shorts Generator ===")

    try:
        config = load_and_validate_config(interactive=interactive, require_segments=args.candidates is None)

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
            audio_path = find_file(video_path, config["audio"], "audio", "--audio", interactive=interactive)
        else:
            audio_path = video_path

        srt_path = (
            Path(args.srt).expanduser().resolve()
            if args.srt
            else find_file(video_path, config["srt"], "subtitles", "--srt", interactive=interactive)
        )

        encoder = args.encoder or os.environ.get("YT_VIDEO_ENCODER") or pick_video_encoder()
        segments = load_candidates_segments(args.candidates) if args.candidates else config["segments"]
        safe_zone_frac, scale_active_word = karaoke_settings(config)
        split = split_settings(config)
        for segment in segments:
            resolve_layout(segment, config)
        brand, brand_path = load_brand_kit(args.brand)
        font_dir = resolve_caption_font_dir(brand, brand_path.parent)

        timeline_words = None
        corrections = []
        if args.words:
            timeline_words = timeline_words_from_file(args.words)
            corrections_path = resolve_corrections_file(
                args.corrections or CORRECTIONS_FILE, CORRECTIONS_EXAMPLE_FILE
            )
            corrections = load_corrections(corrections_path) if corrections_path else []
    except UsageError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc), **summary_fields(started)}))
        else:
            print(f"[error] {exc}", file=sys.stderr)
        return 2
    except (RuntimeError, TimelineWordsError, CorrectionsError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc), **summary_fields(started)}))
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

    results = []
    for i, seg in enumerate(segments, 1):
        name = seg["name"]
        start = parse_time(seg["start"])
        end = parse_time(seg["end"])

        log(f"\n[{i}/{len(segments)}] {name}  ({seg['start']} → {seg['end']})")

        layout = resolve_layout(seg, config)

        if layout == "split":
            if "x_offset" in seg:
                log("  → [warn] x_offset ignored in split layout")
            log("  → reframe (subject tracking, split layout)...")
            crop_path_file = auto_dir / f"{name}_reframe.json"
            try:
                record = reframe_crop_path(str(video_path), start, end, crop_path_file)
                crop_filter = split_filter_from_path(record, split)
                log(f"     crop path → {crop_path_file.name} ({len(record['points'])} points, split)")
                x_offset = None
            except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
                log(f"     [warn] {exc} — using centre crop")
                crop_filter = split_filter_from_path(centre_crop_record(), split)
                crop_path_file = None
                x_offset = None
        elif "x_offset" in seg:
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

        if timeline_words is not None:
            log("  → cuvinte din timeline _words.json (timpi reali)...")
            words = words_for_segment(timeline_words, start, end)
            words = merge_hyphenated(words)
            words = [
                {"word": apply_corrections(word["word"], corrections), "start": word["start"], "end": word["end"]}
                for word in words
            ]
            words_source = "words.json"
            log(f"     {len(words)} cuvinte (timpi din _words.json)")
        else:
            log("  → cuvinte din .srt corectat (fără WhisperX — nu pierde cuvinte)...")
            srt_segments = parse_srt_for_alignment(str(srt_path), start, end)
            words = words_from_srt_segments(srt_segments)
            words_source = "srt"
            log(f"     {len(words)} cuvinte (timing interpolat, nu aliniat pe audio)")

        ass_path = auto_dir / f"{name}_karaoke.ass"
        generate_karaoke_ass(words, str(ass_path), brand, safe_zone_frac, scale_active_word)
        log(f"     ASS generat → {ass_path.name}")

        output = shorts_dir / f"Short{i}-{name}.mp4"
        log(f"  → render → {output.name}")
        used_encoder = render_short(
            str(video_path), crop_filter, start, end, str(ass_path), str(output), encoder, font_dir=font_dir
        )
        log(f"  ✓ {output}")
        results.append({
            "name": name,
            "start": seg["start"],
            "end": seg["end"],
            "layout": layout,
            "x_offset": x_offset,
            "crop_path": str(crop_path_file) if crop_path_file else None,
            "words": len(words),
            "words_source": words_source,
            "output": str(output),
            "encoder": used_encoder,
        })

    log(f"\nDone! {len(segments)} short(s) in {shorts_dir}")

    if args.json:
        print(json.dumps({
            "ok": True,
            "shorts": results,
            "output_dir": str(shorts_dir),
            **summary_fields(started),
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
