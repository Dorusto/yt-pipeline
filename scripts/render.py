#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render an ``edit.json`` edit list to a preview or final MP4 with ffmpeg.

Usage:
    python scripts/render.py --edit edit.json --out recap_720p.mp4
    python scripts/render.py --edit edit.json --out final.mp4 --originals-dir <source-folder>

The tool cuts each video slot from the clip named in ``sources[]`` (the proxy)
or, when ``--originals-dir`` is given, from the full-resolution original of the
same name, scales and pads it to the requested frame, joins the slots in
timeline order with crossfades or hard cuts and keeps each slot's own ambient
audio. A photo slot (``kind: "photo"``) is held for its ``duration_s`` with a
Ken Burns move; a portrait photo, or one whose aspect differs from the frame,
sits on a blurred cover-scaled copy of itself, never on black bars. The ambient
bus is cleaned like ``audio_clean.py`` cleans a clip — wind high-pass,
RNNoise/afftdn noise reduction and loudnorm — unless ``--no-audio-clean`` is
given. A music track (``--music`` or ``audio.music``) is looped to the edit
length, gained, ducked under the cleaned ambient bus with ``sidechaincompress``
and faded out at the end. Captions, graphics and SFX are warned about and
ignored. Sources are read-only; the MP4 is written atomically to ``--out``.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from common.ambience import (
    DEFAULT_MODEL,
    DEFAULT_TARGET_LUFS,
    DEFAULT_WIND_HZ,
    AmbienceError,
    build_filters,
    resolve_denoiser,
)
from common.ffmpeg_tools import ffmpeg_path, probe_duration
from common.moves import middle_speed_of, screen_factor, slot_duration
from common.timeline_words import fade_for_join
from common.photos import ensure_jpeg, open_photo
from common import vertical
from common.schema import SchemaError, validate_or_raise
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error

FPS = 25
SAMPLE_RATE = 48000
PREVIEW_FRAME = (1280, 720)
DEFAULT_MUSIC_GAIN_DB = 0.0
DEFAULT_AMBIENT_UNDER_MUSIC_DB = 12.0
DEFAULT_MUSIC_FADE_OUT_S = 2.0
MUSIC_DUCK_THRESHOLD = 0.1
MUSIC_DUCK_RATIO = 3.0
MUSIC_DUCK_ATTACK_MS = 20
MUSIC_DUCK_RELEASE_MS = 400


class RenderRuntimeError(RuntimeError):
    """Raised when a render fails after it started (ffprobe, ffmpeg)."""


class RenderError(RuntimeError):
    """Raised when an edit cannot be rendered."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="render.py",
        description="Render an edit.json edit list to a preview or final MP4 with ffmpeg.",
        epilog=(
            "examples:\n"
            "  python scripts/render.py --edit edit.json --out recap_720p.mp4\n"
            "  python scripts/render.py --edit edit.json --out final.mp4"
            " --originals-dir <source-folder>\n"
            "  python scripts/render.py --edit edit.json --out recap_720p_9x16.mp4 --format 9x16\n"
            "  python scripts/render.py --edit edit.json --out recap_720p.mp4 --format both\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--edit", required=True, help="edit.json to render")
    parser.add_argument("--out", required=True, help="MP4 file to write")
    parser.add_argument(
        "--originals-dir",
        help="directory of full-resolution originals, resolved before sources[]",
    )
    parser.add_argument(
        "--media-dir",
        help="fallback directory for clip names that are not in sources[]",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="output width (default: native of the first source, else 1280)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=None,
        help="output height (default: native of the first source, else 720)",
    )
    parser.add_argument(
        "--format",
        dest="frame_format",
        choices=list(vertical.FORMATS),
        default="16x9",
        help=(
            "16x9 (default), 9x16 or both. 9x16 follows each clip's reframe crop path "
            "(reframe.py, cached under <workspace>/reframe/) into 720x1280 (1080x1920 with"
            " --originals-dir); photos use their Ken Burns subject position. With both, --out"
            " is the 16:9 file and the vertical one is written next to it as <name>_9x16.mp4"
        ),
    )
    parser.add_argument(
        "--xfade-s",
        type=float,
        default=0.3,
        help="crossfade duration when the slot does not set one (default 0.3)",
    )
    parser.add_argument(
        "--no-audio-clean",
        action="store_true",
        help="keep the raw ambient mix (no wind high-pass, denoise or loudnorm)",
    )
    parser.add_argument(
        "--music",
        default=None,
        help="music track to mix under the cleaned ambient bus (overrides audio.music.path)",
    )
    parser.add_argument(
        "--music-gain-db",
        type=float,
        default=None,
        help=(
            "music gain in dB after the track is normalised to --target-lufs"
            f" (default: the edit's audio.music.gain_db, else {DEFAULT_MUSIC_GAIN_DB:g})"
        ),
    )
    parser.add_argument(
        "--ambient-under-music-db",
        type=float,
        default=None,
        help=(
            "how far the ambient sound sits under the music, in dB; it stays audible,"
            " never removed (default: the edit's audio.ambient_under_music_db,"
            f" else {DEFAULT_AMBIENT_UNDER_MUSIC_DB:g})"
        ),
    )
    parser.add_argument(
        "--denoiser",
        choices=["arnndn", "afftdn", "none"],
        default=None,
        help="noise reduction backend for the ambient bus (default: arnndn with a model, else afftdn)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"RNNoise model for arnndn (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--target-lufs",
        type=float,
        default=DEFAULT_TARGET_LUFS,
        help=f"loudnorm integrated loudness target (default: {DEFAULT_TARGET_LUFS:g})",
    )
    parser.add_argument(
        "--wind-hz",
        type=float,
        default=DEFAULT_WIND_HZ,
        help=f"high-pass corner for wind rumble, 0 disables it (default: {DEFAULT_WIND_HZ:g})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rewrite even when the output is up to date",
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def fmt(value):
    """Format a number for an ffmpeg filter argument without trailing zeros."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def run_ffprobe(path):
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise RenderRuntimeError(f"could not run ffprobe: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        raise RenderRuntimeError(f"ffprobe failed for {path}: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RenderRuntimeError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc


def probe_output(path):
    """Return ``(duration_s, width, height)`` for a rendered file."""
    data = run_ffprobe(path)
    duration = float(data["format"]["duration"])
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise RenderError(f"no video stream in {path}")
    return duration, int(video["width"]), int(video["height"])


def has_audio_stream(path):
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise RenderRuntimeError(f"could not run ffprobe: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        raise RenderRuntimeError(f"ffprobe failed for {path}: {detail}")
    try:
        return bool(json.loads(result.stdout).get("streams"))
    except json.JSONDecodeError as exc:
        raise RenderRuntimeError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc


def resolve_clip(clip, sources, media_dir, originals_dir):
    """Resolve a slot's ``clip`` to a path and its origin.

    Order: ``--originals-dir``, then ``sources[]``, then ``--media-dir``.
    Origin is ``"originals"`` or ``"sources"``.
    """
    if originals_dir is not None:
        candidate = Path(originals_dir).expanduser() / clip
        if candidate.is_file():
            return candidate, "originals"
    fallback = None
    for source in sources:
        if source.get("file") == clip:
            fallback = Path(source["path"])
            if fallback.is_file():
                return fallback, "sources"
    if media_dir is not None:
        candidate = Path(media_dir).expanduser() / clip
        if candidate.is_file():
            return candidate, "sources"
    if fallback is not None:
        return fallback, "sources"
    return None, None


def resolve_photo(photo, sources, media_dir, originals_dir):
    """Resolve a photo slot's ``photo`` to a path and its origin.

    Order: ``--originals-dir``, then a ``sources[]`` row with
    ``media_type == "photo"``, then any ``sources[]`` row with that file, then
    ``--media-dir``. Origin is ``"originals"`` or ``"sources"``.
    """
    if originals_dir is not None:
        candidate = Path(originals_dir).expanduser() / photo
        if candidate.is_file():
            return candidate, "originals"
    matches = [source for source in sources if source.get("file") == photo]
    matches.sort(key=lambda source: source.get("media_type") != "photo")
    fallback = None
    for source in matches:
        path = Path(source["path"])
        if path.is_file():
            return path, "sources"
        fallback = fallback or path
    if media_dir is not None:
        candidate = Path(media_dir).expanduser() / photo
        if candidate.is_file():
            return candidate, "sources"
    if fallback is not None:
        return fallback, "sources"
    return None, None


def oriented_photo_size(path):
    """Return ``(width, height)`` of a photo after EXIF orientation."""
    return open_photo(path).size


def is_within(path, directory):
    path = Path(path).expanduser().resolve()
    directory = Path(directory).expanduser().resolve()
    return path == directory or directory in path.parents


def probe_native_size(path):
    """Return ``(width, height)`` of the first video stream in ``path``."""
    data = run_ffprobe(path)
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise RenderError(f"no video stream in {path}")
    return int(video["width"]), int(video["height"])


def probe_video_size(path):
    try:
        _, width, height = probe_output(path)
        return width, height
    except RenderError:
        return None


def frame_aligned_durations(slots, durations, xfade_s):
    """On-screen durations and crossfade lengths rounded to whole frames.

    The cumulative on-screen position of every cut is rounded to the nearest
    frame and each clip's on-screen length is the difference of the rounded
    positions, so the per-clip rounding never accumulates into a drift across
    the edit. Returns ``(aligned_durations, aligned_fades)``.
    """
    aligned = []
    fades = []
    position = 0.0
    frames = 0
    for index, duration in enumerate(durations):
        position += duration
        target = round(position * FPS)
        aligned.append((target - frames) / FPS)
        frames = target
        if index < len(slots) - 1:
            fade = fade_for_join(slots[index + 1], durations[index], durations[index + 1], xfade_s)
            fade_frames = round(fade * FPS)
            fades.append(fade_frames / FPS)
            position -= fade_frames / FPS
            frames -= fade_frames
        else:
            fades.append(0.0)
    return aligned, fades


def total_duration(slots, durations, xfade_s):
    """The on-screen length of the whole edit, crossfades included, frame-aligned."""
    aligned, fades = frame_aligned_durations(slots, durations, xfade_s)
    total = aligned[0]
    for join in range(len(slots) - 1):
        total += aligned[join + 1] - fades[join]
    return total


def resolve_music(edit_music, music_flag, gain_flag, edit_dir,
                  target_lufs=DEFAULT_TARGET_LUFS,
                  ambient_under_db=DEFAULT_AMBIENT_UNDER_MUSIC_DB):
    """Resolve the music track from ``audio.music`` and the CLI flags.

    ``--music`` overrides ``audio.music.path``; ``--music-gain-db`` overrides
    ``audio.music.gain_db``. Returns ``None`` when no track is configured.
    """
    edit_music = edit_music or {}
    raw_path = music_flag if music_flag is not None else edit_music.get("path")
    if raw_path is None:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path(edit_dir) / path
    gain_db = (
        float(gain_flag)
        if gain_flag is not None
        else float(edit_music.get("gain_db", DEFAULT_MUSIC_GAIN_DB))
    )
    return {
        "path": path,
        "gain_db": gain_db,
        "fade_in_s": float(edit_music.get("fade_in_s", 0.0)),
        "fade_out_s": float(edit_music.get("fade_out_s", DEFAULT_MUSIC_FADE_OUT_S)),
        "start_s": float(edit_music.get("start_s", 0.0)),
        "target_lufs": float(target_lufs),
        "ambient_under_db": max(0.0, float(ambient_under_db)),
    }


def build_music_chain(music_index, ambient_label, edit_duration, music_duration, music, ducking):
    """Build the music mix chain and return ``(parts, final_label)``.

    The music is the main bed: the track is resampled, looped to the edit
    length, trimmed, normalised to ``target_lufs`` and offset by ``gain_db``.
    The ambient bus stays audible ``ambient_under_db`` below it (never
    removed); a loud ambient event (someone speaking close to the camera)
    dips the music gently through ``sidechaincompress``. The mix is faded out
    over the last ``fade_out_s`` seconds of the edit.
    """
    loop_size = max(1, int(round(music_duration * SAMPLE_RATE)))
    start = max(0.0, float(music["start_s"]))
    play = max(0.0, edit_duration - start)
    chain = (
        f"[{music_index}:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo,"
        f"aloop=loop=-1:size={loop_size},atrim=start=0:end={fmt(play)},"
        f"asetpts=PTS-STARTPTS,"
        f"loudnorm=I={fmt(music['target_lufs'])}:TP=-1.5:LRA=11,"
        f"aresample={SAMPLE_RATE},volume={fmt(music['gain_db'])}dB"
    )
    if start > 0:
        delay_ms = int(round(start * 1000))
        chain += (
            f",adelay={delay_ms}:all=1,apad,"
            f"atrim=start=0:end={fmt(edit_duration)},asetpts=PTS-STARTPTS"
        )
    chain += "[music]"
    parts = [chain]
    under = f"volume=-{fmt(music['ambient_under_db'])}dB"
    if ducking:
        parts.append(f"[{ambient_label}]asetpts=PTS-STARTPTS,{under},asplit=2[amb_sc][amb_mix]")
        parts.append(
            f"[music][amb_sc]sidechaincompress=threshold={fmt(MUSIC_DUCK_THRESHOLD)}:"
            f"ratio={fmt(MUSIC_DUCK_RATIO)}:attack={MUSIC_DUCK_ATTACK_MS}:"
            f"release={MUSIC_DUCK_RELEASE_MS}[ducked]"
        )
        parts.append("[ducked][amb_mix]amix=inputs=2:duration=first:normalize=0[amixed]")
    else:
        parts.append(f"[{ambient_label}]asetpts=PTS-STARTPTS,{under}[amb_norm]")
        parts.append("[music][amb_norm]amix=inputs=2:duration=first:normalize=0[amixed]")
    fades = []
    if music["fade_in_s"] > 0:
        fades.append(f"afade=t=in:st=0:d={fmt(music['fade_in_s'])}")
    if music["fade_out_s"] > 0:
        fade_start = max(0.0, edit_duration - music["fade_out_s"])
        fades.append(f"afade=t=out:st={fmt(fade_start)}:d={fmt(music['fade_out_s'])}")
    if fades:
        parts.append(f"[amixed]{','.join(fades)}[afinal]")
        return parts, "afinal"
    return parts, "amixed"


ZOOMPAN_MAX_WIDTH = 8000
ZOOMPAN_MAX_FACTOR = 4


def zoompan_upscale(width):
    """How much to enlarge the composed frame before ``zoompan`` so its whole-pixel crop steps vanish."""
    return max(1, min(ZOOMPAN_MAX_FACTOR, ZOOMPAN_MAX_WIDTH // max(1, width)))


def pan_span(move, amount, focus_x):
    """Start and end centre of a pan, kept inside the zoomed frame so it never stalls at an edge.

    The pan travels ``amount`` of the frame width, or the whole room the zoom
    leaves when that is less, and ends on ``focus_x`` when there is room.
    """
    zoom = 1 + amount
    low = 1 / (2 * zoom)
    high = 1 - low
    travel = min(amount, high - low)
    if move == "pan_right":
        end = min(max(focus_x, low + travel), high)
        return end - travel, end
    end = min(max(focus_x, low), high - travel)
    return end + travel, end


def zoompan_for(move, amount, focus_x, focus_y, frames, width, height):
    """The ``zoompan`` filter of one Ken Burns move over ``frames`` frames."""
    span = max(1, frames - 1)
    linear = f"(on/{span})"
    progress = f"({linear}*{linear}*(3-2*{linear}))"
    if move == "zoom_out":
        zoom = f"1+{fmt(amount)}*(1-{progress})"
        centre_x = fmt(focus_x)
        centre_y = fmt(focus_y)
    elif move in ("pan_left", "pan_right"):
        zoom = fmt(1 + amount)
        start_x, end_x = pan_span(move, amount, focus_x)
        centre_x = f"{fmt(start_x)}+{fmt(end_x - start_x)}*{progress}"
        centre_y = fmt(focus_y)
    else:
        zoom = f"1+{fmt(amount)}*{progress}"
        centre_x = fmt(focus_x)
        centre_y = fmt(focus_y)
    x_expr = f"clip(({centre_x})*iw-iw/(2*zoom),0,iw-iw/zoom)"
    y_expr = f"clip(({centre_y})*ih-ih/(2*zoom),0,ih-ih/zoom)"
    factor = zoompan_upscale(width)
    return (
        f"scale=iw*{factor}:ih*{factor}:flags=bicubic,format=yuv444p,"
        f"zoompan=z='{zoom}':x='{x_expr}':y='{y_expr}':"
        f"d={frames}:s={width}x{height}:fps={FPS}"
    )


def photo_filters(index, slot, duration, width, height, photo_size):
    """The filter chain for one photo slot: frame, Ken Burns move, output label.

    A photo whose aspect differs from the frame (or that is portrait) sits on a
    blurred cover-scaled copy of itself, never on black bars. The Ken Burns move
    runs with ``zoompan`` over the composed frame for exactly ``duration``.
    """
    burns = slot.get("ken_burns") or {}
    move = burns.get("move", "zoom_in")
    amount = float(burns.get("amount", 0.08))
    focus_x = float(burns.get("focus_x", 0.5))
    focus_y = float(burns.get("focus_y", 0.5))
    frames = max(1, round(duration * FPS))
    zoompan = zoompan_for(move, amount, focus_x, focus_y, frames, width, height)
    photo_width, photo_height = photo_size
    frame_aspect = width / height
    photo_aspect = photo_width / photo_height
    precrop = ""
    if height > width and photo_aspect > frame_aspect + 0.01:
        window_w = max(2, int(photo_height * frame_aspect) // 2 * 2)
        left = int(min(photo_width - window_w, max(0, focus_x * photo_width - window_w / 2)))
        precrop = f"crop={window_w}:{photo_height}:{left}:0,"
        focus_x = (focus_x * photo_width - left) / window_w
        photo_width, photo_aspect = window_w, frame_aspect
        zoompan = zoompan_for(move, amount, focus_x, focus_y, frames, width, height)
    if not precrop and (photo_height > photo_width or abs(photo_aspect - frame_aspect) > 0.01):
        return [
            f"[{index}:v]split=2[pbg{index}][pfg{index}]",
            f"[pbg{index}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=20:2[pbgb{index}]",
            f"[pfg{index}]scale={width}:{height}:force_original_aspect_ratio=decrease[pfgs{index}]",
            f"[pbgb{index}][pfgs{index}]overlay=(W-w)/2:(H-h)/2,setsar=1,{zoompan}[v{index}]",
        ]
    return [
        f"[{index}:v]{precrop}scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,{zoompan}[v{index}]"
    ]


def ramp_quarters(slot, end_s=None):
    """The source times that split a ramped slot into head quarter, middle half and tail quarter."""
    in_s = float(slot["in_s"])
    out_s = float(slot["out_s"]) if end_s is None else float(end_s)
    length = out_s - in_s
    return in_s, in_s + length / 4, in_s + 3 * length / 4, out_s


def ramp_video_filters(index, slot, speed, width, height, crop=None, end_s=None):
    """Three trims joined: head quarter and tail quarter in real time, middle half at ``speed``.

    ``crop`` is an optional ``crop`` filter applied to the source before it is split.
    """
    start, first, second, end = ramp_quarters(slot, end_s)
    head = [f"[{index}:v]{crop}[rc{index}]"] if crop else []
    source = f"rc{index}" if crop else f"{index}:v"
    return head + [
        f"[{source}]split=3[rh{index}][rm{index}][rt{index}]",
        f"[rh{index}]trim=start={fmt(start)}:end={fmt(first)},setpts=PTS-STARTPTS,fps={FPS}[rhs{index}]",
        f"[rm{index}]trim=start={fmt(first)}:end={fmt(second)},setpts=(PTS-STARTPTS)/{fmt(speed)},"
        f"fps={FPS}[rms{index}]",
        f"[rt{index}]trim=start={fmt(second)}:end={fmt(end)},setpts=PTS-STARTPTS,fps={FPS}[rts{index}]",
        f"[rhs{index}][rms{index}][rts{index}]concat=n=3:v=1:a=0,"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{index}]",
    ]


def ramp_audio_filters(index, slot, speed, end_s=None):
    """The ambient sound cut the same way: ``atempo`` on the middle half keeps it continuous."""
    start, first, second, end = ramp_quarters(slot, end_s)
    return [
        f"[{index}:a]asplit=3[ah{index}][am{index}][at{index}]",
        f"[ah{index}]atrim=start={fmt(start)}:end={fmt(first)},asetpts=PTS-STARTPTS,"
        f"aresample={SAMPLE_RATE}[ahs{index}]",
        f"[am{index}]atrim=start={fmt(first)}:end={fmt(second)},asetpts=PTS-STARTPTS,"
        f"atempo={fmt(speed)},aresample={SAMPLE_RATE}[ams{index}]",
        f"[at{index}]atrim=start={fmt(second)}:end={fmt(end)},asetpts=PTS-STARTPTS,"
        f"aresample={SAMPLE_RATE}[ats{index}]",
        f"[ahs{index}][ams{index}][ats{index}]concat=n=3:v=0:a=1,aresample={SAMPLE_RATE}[a{index}]",
    ]


def build_filter_graph(slots, durations, has_audio, ambience_enabled, width, height, xfade_s, warnings, clean_chain=None, photo_sizes=None, crops=None):
    """Build the ``filter_complex`` graph and return it with the final labels.

    ``clean_chain`` is the ambient cleanup filter list (wind high-pass, RNNoise
    or afftdn, loudnorm); it runs on the mixed ambient bus before the output.
    ``photo_sizes`` holds the oriented ``(width, height)`` of each photo slot.
    ``crops`` holds, per slot, the moving ``crop`` filter of the vertical render
    (None for a photo or a 16:9 render); it runs on the source before the trim.
    """
    parts = []
    sizes = photo_sizes or [None] * len(slots)
    crops = crops or [None] * len(slots)
    aligned, fades = frame_aligned_durations(slots, durations, xfade_s)
    for index, slot in enumerate(slots):
        is_photo = slot.get("kind") == "photo"
        if is_photo:
            parts.extend(photo_filters(index, slot, aligned[index], width, height, sizes[index]))
        else:
            start = fmt(slot["in_s"])
            ramp = middle_speed_of(slot)
            if ramp > 1.0:
                end_s = float(slot["in_s"]) + aligned[index] / screen_factor(ramp)
                parts.extend(ramp_video_filters(index, slot, ramp, width, height, crops[index], end_s))
            else:
                end = fmt(float(slot["in_s"]) + aligned[index])
                crop_prefix = f"{crops[index]}," if crops[index] else ""
                parts.append(
                    f"[{index}:v]{crop_prefix}trim=start={start}:end={end},setpts=PTS-STARTPTS,fps={FPS},"
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{index}]"
                )
        if ambience_enabled and has_audio[index] and not is_photo:
            if ramp > 1.0:
                end_s = float(slot["in_s"]) + aligned[index] / screen_factor(ramp)
                parts.extend(ramp_audio_filters(index, slot, ramp, end_s))
            else:
                end = fmt(float(slot["in_s"]) + aligned[index])
                parts.append(
                    f"[{index}:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
                    f"aresample={SAMPLE_RATE}[a{index}]"
                )
        else:
            parts.append(
                f"anullsrc=r={SAMPLE_RATE}:cl=stereo,"
                f"atrim=start=0:end={fmt(aligned[index])},"
                f"asetpts=PTS-STARTPTS[a{index}]"
            )

    current_v = "v0"
    current_a = "a0"
    accumulated = aligned[0]
    for join in range(len(slots) - 1):
        nxt = join + 1
        fade = fades[join]
        if fade > 0:
            offset = accumulated - fade
            if offset < 0:
                offset = 0.0
                warnings.append(f"join {join}: crossfade offset clamped to 0")
            parts.append(
                f"[{current_v}][v{nxt}]xfade=transition=fade:duration={fmt(fade)}:"
                f"offset={fmt(offset)}[xv{join}]"
            )
            parts.append(f"[{current_a}][a{nxt}]acrossfade=d={fmt(fade)}[xa{join}]")
            current_v = f"xv{join}"
            current_a = f"xa{join}"
            accumulated = accumulated + aligned[nxt] - fade
        else:
            parts.append(
                f"[{current_v}][{current_a}][v{nxt}][a{nxt}]concat=n=2:v=1:a=1"
                f"[cv{join}][ca{join}]"
            )
            parts.append(f"[cv{join}]settb=1/{FPS}[cvt{join}]")
            current_v = f"cvt{join}"
            current_a = f"ca{join}"
            accumulated = accumulated + aligned[nxt]
    if clean_chain:
        parts.append(f"[{current_a}]{','.join(clean_chain)}[aclean]")
        current_a = "aclean"
    return ";".join(parts), current_v, current_a


def run_reframe(command):
    """Run ``reframe.py``; return an error text, or None when it succeeded."""
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        return f"could not run reframe.py: {exc}"
    if result.returncode != 0:
        lines = (result.stderr or "").strip().splitlines()
        return lines[-1] if lines else "reframe.py failed"
    return None


def crop_cache_is_fresh(cache, video):
    return cache.is_file() and cache.stat().st_mtime >= Path(video).stat().st_mtime


def slot_crop_filters(slots, clip_paths, sources, media_dir, workspace, warnings, reframe_runner=None):
    """The moving ``crop`` filter of every video slot for the vertical render.

    Each source clip is analysed once with ``reframe.py`` (on its proxy when one
    exists) and the crop path is cached under ``<workspace>/reframe/``; the path
    is rescaled to the file that is rendered. A clip whose analysis fails gets a
    centred crop and a warning.
    """
    runner = reframe_runner or run_reframe
    cache_dir = Path(workspace) / "reframe"
    script = Path(__file__).resolve().parent / "reframe.py"
    paths_by_clip = {}
    sizes = {}
    filters = []
    for slot, clip_path in zip(slots, clip_paths):
        if slot.get("kind") == "photo":
            filters.append(None)
            continue
        key = (slot["clip"], str(clip_path))
        if key not in paths_by_clip:
            native = sizes.get(clip_path)
            if native is None:
                native = sizes[clip_path] = probe_native_size(clip_path)
            analysed, _ = resolve_clip(slot["clip"], sources, media_dir, None)
            analysed = analysed or clip_path
            cache = cache_dir / vertical.cached_path_name(analysed)
            if not crop_cache_is_fresh(cache, analysed):
                cache_dir.mkdir(parents=True, exist_ok=True)
                error = runner(vertical.reframe_command(sys.executable, script, analysed, cache))
                if error:
                    warnings.append(f"reframe failed for {slot['clip']}, centred crop used: {error}")
            path = vertical.load_crop_path(cache, native[0], native[1])
            if path is None:
                path = vertical.centred_path(native[0], native[1])
            paths_by_clip[key] = path
        filters.append(vertical.crop_filter(paths_by_clip[key], float(slot["in_s"]), float(slot["out_s"])))
    return filters


def collect_warnings(document, ambience):
    warnings = []
    audio = document.get("audio") or {}
    if ambience.get("enabled") and ambience.get("source") == "file":
        warnings.append("ambience file ignored in S1 render, using slot audio")
    if audio.get("sfx"):
        warnings.append("sfx ignored in S1 render")
    if document.get("captions"):
        warnings.append("captions ignored in S1 render")
    if document.get("graphics"):
        warnings.append("graphics ignored in S1 render")
    return warnings


def render(
    edit_path,
    out_path,
    media_dir,
    originals_dir,
    width,
    height,
    xfade_s,
    force,
    audio_clean=True,
    denoiser=None,
    model=None,
    target_lufs=DEFAULT_TARGET_LUFS,
    wind_hz=DEFAULT_WIND_HZ,
    music=None,
    music_gain_db=None,
    ambient_under_music_db=None,
    frame_format="16x9",
    reframe_runner=None,
):
    """Render ``edit_path`` to ``out_path`` and return the run summary.

    ``frame_format`` is ``16x9`` or ``9x16`` (``both`` is handled by :func:`main`).
    """
    started = time.monotonic()
    edit_path = Path(edit_path).expanduser()
    out_path = Path(out_path).expanduser()
    if not edit_path.is_file():
        raise RenderError(f"edit file not found: {edit_path}")
    if originals_dir is not None and is_within(out_path, originals_dir):
        raise RenderError(f"--out must not be inside --originals-dir: {out_path}")
    try:
        document = json.loads(edit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RenderError(f"could not read edit file: {exc}") from exc
    validate_or_raise(document, "edit")

    slots = document["video"]
    sources = document["sources"]
    audio = document.get("audio") or {}
    ambience = audio.get("ambience") or {}
    ambience_enabled = bool(ambience.get("enabled", False))
    warnings = collect_warnings(document, ambience)
    durations = [slot_duration(slot) for slot in slots]
    edit_duration = total_duration(slots, durations, xfade_s)

    ambient_under_db = ambient_under_music_db
    if ambient_under_db is None:
        ambient_under_db = audio.get("ambient_under_music_db")
    if ambient_under_db is None:
        ambient_under_db = DEFAULT_AMBIENT_UNDER_MUSIC_DB
    music_config = resolve_music(
        audio.get("music"), music, music_gain_db, edit_path.parent,
        target_lufs=target_lufs, ambient_under_db=ambient_under_db,
    )
    music_duration = None
    music_mtime = None
    if music_config is not None:
        if not music_config["path"].is_file():
            raise RenderError(f"music file not found: {music_config['path']}")
        if not has_audio_stream(music_config["path"]):
            raise RenderError(f"music file has no audio stream: {music_config['path']}")
        music_duration = probe_duration(music_config["path"])
        if music_duration is None:
            raise RenderRuntimeError(f"could not read music duration: {music_config['path']}")
        music_mtime = music_config["path"].stat().st_mtime
    ducking = bool(music_config is not None and ambience_enabled)
    looped = bool(music_config is not None and music_duration < edit_duration)
    music_info = None
    if music_config is not None:
        music_info = {
            "path": str(music_config["path"]),
            "gain_db": music_config["gain_db"],
            "ambient_under_db": music_config["ambient_under_db"],
            "ducking": ducking,
            "looped": looped,
        }
    clean_chain = []
    clean_applied = []
    clean_denoiser = None
    if audio_clean and ambience_enabled:
        model_path = Path(model).expanduser() if model is not None else DEFAULT_MODEL
        try:
            clean_denoiser, clean_warnings = resolve_denoiser(denoiser, model_path)
        except AmbienceError as exc:
            raise RenderError(str(exc)) from exc
        warnings.extend(clean_warnings)
        clean_chain, clean_applied = build_filters(
            clean_denoiser,
            model_path,
            wind_hz,
            apply_highpass=wind_hz > 0,
            apply_loudnorm=True,
            target_lufs=target_lufs,
        )
    audio_clean_info = {
        "enabled": bool(clean_chain),
        "denoiser": clean_denoiser,
        "filters": list(clean_applied),
    }
    is_vertical = frame_format == "9x16"
    if is_vertical:
        default_frame = (
            vertical.VERTICAL_FINAL_FRAME if originals_dir is not None else vertical.VERTICAL_PREVIEW_FRAME
        )
        if width is None:
            width = default_frame[0]
        if height is None:
            height = default_frame[1]
    source = "originals" if originals_dir is not None else "sources"
    originals_label = (
        str(Path(originals_dir).expanduser()) if originals_dir is not None else None
    )

    if out_path.exists() and not force:
        out_mtime = out_path.stat().st_mtime
        up_to_date = out_mtime > edit_path.stat().st_mtime
        if music_mtime is not None:
            up_to_date = up_to_date and out_mtime > music_mtime
        if up_to_date:
            size = probe_video_size(out_path)
            return {
                "status": "skipped",
                "out": str(out_path),
                "duration_s": probe_duration(out_path),
                "slots": len(slots),
                "width": size[0] if size else width,
                "height": size[1] if size else height,
                "format": frame_format,
                "warnings": warnings,
                "originals_dir": originals_label,
                "source": source,
                "audio_clean": audio_clean_info,
                "music": music_info,
                "wall_time_s": round(time.monotonic() - started, 3),
                "llm_cost_usd": 0.0,
            }

    if media_dir is None and (edit_path.parent / "proxy").is_dir():
        media_dir = edit_path.parent / "proxy"
    clip_paths = []
    photo_sizes = []
    missing = []
    photo_cache = edit_path.parent / "photos"
    for slot in slots:
        if slot.get("kind") == "photo":
            resolved, origin = resolve_photo(slot["photo"], sources, media_dir, originals_dir)
            if resolved is None:
                raise RenderError(f"could not resolve photo: {slot['photo']}")
            jpeg = ensure_jpeg(resolved, photo_cache)
            clip_paths.append(jpeg)
            photo_sizes.append(oriented_photo_size(jpeg))
            if origin == "sources" and originals_dir is not None:
                missing.append(slot["photo"])
        else:
            resolved, origin = resolve_clip(slot["clip"], sources, media_dir, originals_dir)
            if resolved is None:
                raise RenderError(f"could not resolve clip: {slot['clip']}")
            clip_paths.append(resolved)
            photo_sizes.append(None)
            if origin == "sources" and originals_dir is not None:
                missing.append(slot["clip"])
    if missing:
        warnings.append(
            "clips missing from originals dir, fell back to sources: " + ", ".join(missing)
        )

    if width is None or height is None:
        if originals_dir is not None:
            if slots[0].get("kind") == "photo":
                native = photo_sizes[0]
            else:
                native = probe_native_size(clip_paths[0])
        else:
            native = PREVIEW_FRAME
        if width is None:
            width = native[0]
        if height is None:
            height = native[1]

    durations = [slot_duration(slot) for slot in slots]
    if ambience_enabled:
        has_audio = [
            False if slot.get("kind") == "photo" else has_audio_stream(path)
            for slot, path in zip(slots, clip_paths)
        ]
    else:
        has_audio = [False] * len(slots)

    crops = None
    if is_vertical:
        crops = slot_crop_filters(
            slots, clip_paths, sources, media_dir, edit_path.parent, warnings, reframe_runner
        )
    filter_complex, final_v, final_a = build_filter_graph(
        slots, durations, has_audio, ambience_enabled, width, height, xfade_s, warnings,
        clean_chain=clean_chain, photo_sizes=photo_sizes, crops=crops,
    )
    if music_config is not None:
        music_parts, final_a = build_music_chain(
            len(slots),
            final_a,
            edit_duration,
            music_duration,
            music_config,
            ducking,
        )
        filter_complex = ";".join([filter_complex, *music_parts])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp.mp4")
    command = [ffmpeg_path(), "-y"]
    for path in clip_paths:
        command += ["-i", str(path)]
    if music_config is not None:
        command += ["-i", str(music_config["path"])]
    command += [
        "-filter_complex",
        filter_complex,
        "-map",
        f"[{final_v}]",
        "-map",
        f"[{final_a}]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(tmp_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            lines = (result.stderr or "").strip().splitlines()
            detail = lines[-1] if lines else "unknown error"
            raise RenderRuntimeError(f"ffmpeg failed: {detail}")
        duration, out_width, out_height = probe_output(tmp_path)
        if (out_width, out_height) != (width, height):
            raise RenderRuntimeError(f"unexpected output resolution: {out_width}x{out_height}")
        os.replace(tmp_path, out_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return {
        "status": "written",
        "out": str(out_path),
        "duration_s": round(duration, 3),
        "slots": len(slots),
        "width": width,
        "height": height,
        "format": frame_format,
        "warnings": warnings,
        "originals_dir": originals_label,
        "source": source,
        "audio_clean": audio_clean_info,
        "music": music_info,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summaries = []
        for frame_format in vertical.formats_to_render(args.frame_format):
            out = args.out
            width, height = args.width, args.height
            if frame_format == "9x16" and args.frame_format == "both":
                out = vertical.vertical_out_path(args.out)
                width, height = None, None
            summaries.append(
                render(
                    args.edit,
                    out,
                    args.media_dir,
                    args.originals_dir,
                    width,
                    height,
                    args.xfade_s,
                    args.force,
                    audio_clean=not args.no_audio_clean,
                    denoiser=args.denoiser,
                    model=args.model,
                    target_lufs=args.target_lufs,
                    wind_hz=args.wind_hz,
                    music=args.music,
                    music_gain_db=args.music_gain_db,
                    ambient_under_music_db=args.ambient_under_music_db,
                    frame_format=frame_format,
                )
            )
        summary = dict(summaries[0])
        if len(summaries) > 1:
            summary["vertical"] = summaries[1]
    except (RenderRuntimeError, AmbienceError) as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except (RenderError, SchemaError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    if args.json:
        print(json.dumps(summary))
    else:
        if summary["status"] == "skipped":
            print(f"skipped {summary['out']} (up to date)")
        else:
            print(f"wrote {summary['out']}")
            print(
                f"{summary['duration_s']}s, {summary['slots']} slots,"
                f" {summary['width']}x{summary['height']}"
            )
        clean = summary.get("audio_clean") or {}
        if clean.get("enabled"):
            print("ambient cleaned: " + ", ".join(clean.get("filters") or []))
        music = summary.get("music")
        if music:
            print(
                f"music: {music['path']} ({music['gain_db']:g} dB,"
                f" ducking={music['ducking']}, looped={music['looped']})"
            )
        for warning in summary["warnings"]:
            print(f"warning: {warning}")
        for extra in summaries[1:]:
            print(f"vertical: {extra['out']} {extra['width']}x{extra['height']} {extra['duration_s']}s")
            for warning in extra["warnings"]:
                print(f"warning: {warning}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
