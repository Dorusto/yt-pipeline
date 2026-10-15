#!/usr/bin/env python3
"""Render an ``edit.json`` edit list to a preview or final MP4 with ffmpeg.

Usage:
    python scripts/render.py --edit edit.json --out recap_720p.mp4
    python scripts/render.py --edit edit.json --out final.mp4 --originals-dir ~/media/yt-test/raw

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
import math
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
from common.ffmpeg_tools import ffmpeg_path
from common.photos import ensure_jpeg, open_photo
from common.schema import SchemaError, validate_or_raise

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
            " --originals-dir ~/media/yt-test/raw\n"
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
        raise RenderError(f"could not run ffprobe: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        raise RenderError(f"ffprobe failed for {path}: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RenderError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc


def probe_output(path):
    """Return ``(duration_s, width, height)`` for a rendered file."""
    data = run_ffprobe(path)
    duration = float(data["format"]["duration"])
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise RenderError(f"no video stream in {path}")
    return duration, int(video["width"]), int(video["height"])


def probe_duration(path):
    try:
        return round(float(run_ffprobe(path)["format"]["duration"]), 3)
    except (RenderError, KeyError, TypeError, ValueError):
        return None


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
        raise RenderError(f"could not run ffprobe: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        raise RenderError(f"ffprobe failed for {path}: {detail}")
    try:
        return bool(json.loads(result.stdout).get("streams"))
    except json.JSONDecodeError as exc:
        raise RenderError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc


def resolve_clip(clip, sources, media_dir, originals_dir):
    """Resolve a slot's ``clip`` to a path and its origin.

    Order: ``--originals-dir``, then ``sources[]``, then ``--media-dir``.
    Origin is ``"originals"`` or ``"sources"``.
    """
    if originals_dir is not None:
        candidate = Path(originals_dir).expanduser() / clip
        if candidate.is_file():
            return candidate, "originals"
    for source in sources:
        if source.get("file") == clip:
            return Path(source["path"]), "sources"
    if media_dir is not None:
        candidate = Path(media_dir).expanduser() / clip
        if candidate.is_file():
            return candidate, "sources"
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
    for source in sources:
        if source.get("file") == photo and source.get("media_type") == "photo":
            return Path(source["path"]), "sources"
    for source in sources:
        if source.get("file") == photo:
            return Path(source["path"]), "sources"
    if media_dir is not None:
        candidate = Path(media_dir).expanduser() / photo
        if candidate.is_file():
            return candidate, "sources"
    return None, None


def slot_duration(slot):
    """The on-screen length of a slot: ``duration_s`` for photos, out-in for video."""
    if slot.get("kind") == "photo":
        return float(slot["duration_s"])
    return float(slot["out_s"]) - float(slot["in_s"])


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


def fade_for_join(slot, dur_prev, dur_next, xfade_s):
    """Return the crossfade length used at the join into ``slot``; 0 means cut."""
    transition = slot.get("transition") or {}
    if transition.get("type") != "crossfade":
        return 0.0
    requested = transition.get("duration_s") or 0.0
    if requested <= 0:
        requested = xfade_s
    if requested <= 0:
        return 0.0
    return min(requested, dur_prev / 2, dur_next / 2)


def total_duration(slots, durations, xfade_s):
    """The on-screen length of the whole edit, crossfades included."""
    total = durations[0]
    for join in range(len(slots) - 1):
        nxt = join + 1
        fade = fade_for_join(slots[nxt], durations[join], durations[nxt], xfade_s)
        total += durations[nxt] - fade
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
    frames = max(1, math.ceil(duration * FPS))
    span = max(1, frames - 1)
    progress = f"on/{span}"
    if move == "zoom_out":
        zoom = f"1+{fmt(amount)}*(1-{progress})"
        centre_x = fmt(focus_x)
        centre_y = fmt(focus_y)
    elif move == "pan_left":
        zoom = fmt(1 + amount)
        centre_x = f"{fmt(focus_x)}+{fmt(amount)}*(1-{progress})"
        centre_y = fmt(focus_y)
    elif move == "pan_right":
        zoom = fmt(1 + amount)
        centre_x = f"{fmt(focus_x)}-{fmt(amount)}*(1-{progress})"
        centre_y = fmt(focus_y)
    else:
        zoom = f"1+{fmt(amount)}*{progress}"
        centre_x = fmt(focus_x)
        centre_y = fmt(focus_y)
    x_expr = f"clip(({centre_x})*iw-iw/(2*zoom),0,iw-iw/zoom)"
    y_expr = f"clip(({centre_y})*ih-ih/(2*zoom),0,ih-ih/zoom)"
    zoompan = (
        f"zoompan=z='{zoom}':x='{x_expr}':y='{y_expr}':"
        f"d={frames}:s={width}x{height}:fps={FPS}"
    )
    photo_width, photo_height = photo_size
    frame_aspect = width / height
    photo_aspect = photo_width / photo_height
    if photo_height > photo_width or abs(photo_aspect - frame_aspect) > 0.01:
        return [
            f"[{index}:v]split=2[pbg{index}][pfg{index}]",
            f"[pbg{index}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=20:2[pbgb{index}]",
            f"[pfg{index}]scale={width}:{height}:force_original_aspect_ratio=decrease[pfgs{index}]",
            f"[pbgb{index}][pfgs{index}]overlay=(W-w)/2:(H-h)/2,setsar=1,{zoompan}[v{index}]",
        ]
    return [
        f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,{zoompan}[v{index}]"
    ]


def build_filter_graph(slots, durations, has_audio, ambience_enabled, width, height, xfade_s, warnings, clean_chain=None, photo_sizes=None):
    """Build the ``filter_complex`` graph and return it with the final labels.

    ``clean_chain`` is the ambient cleanup filter list (wind high-pass, RNNoise
    or afftdn, loudnorm); it runs on the mixed ambient bus before the output.
    ``photo_sizes`` holds the oriented ``(width, height)`` of each photo slot.
    """
    parts = []
    sizes = photo_sizes or [None] * len(slots)
    for index, slot in enumerate(slots):
        is_photo = slot.get("kind") == "photo"
        if is_photo:
            parts.extend(photo_filters(index, slot, durations[index], width, height, sizes[index]))
        else:
            start = fmt(slot["in_s"])
            end = fmt(slot["out_s"])
            parts.append(
                f"[{index}:v]trim=start={start}:end={end},setpts=PTS-STARTPTS,fps={FPS},"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{index}]"
            )
        if ambience_enabled and has_audio[index] and not is_photo:
            parts.append(
                f"[{index}:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
                f"aresample={SAMPLE_RATE}[a{index}]"
            )
        else:
            parts.append(
                f"anullsrc=r={SAMPLE_RATE}:cl=stereo,"
                f"atrim=start=0:end={fmt(durations[index])},"
                f"asetpts=PTS-STARTPTS[a{index}]"
            )

    current_v = "v0"
    current_a = "a0"
    accumulated = durations[0]
    for join in range(len(slots) - 1):
        nxt = join + 1
        fade = fade_for_join(slots[nxt], durations[join], durations[nxt], xfade_s)
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
            accumulated = accumulated + durations[nxt] - fade
        else:
            parts.append(
                f"[{current_v}][{current_a}][v{nxt}][a{nxt}]concat=n=2:v=1:a=1"
                f"[cv{join}][ca{join}]"
            )
            parts.append(f"[cv{join}]settb=1/{FPS}[cvt{join}]")
            current_v = f"cvt{join}"
            current_a = f"ca{join}"
            accumulated = accumulated + durations[nxt]
    if clean_chain:
        parts.append(f"[{current_a}]{','.join(clean_chain)}[aclean]")
        current_a = "aclean"
    return ";".join(parts), current_v, current_a


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
):
    """Render ``edit_path`` to ``out_path`` and return the run summary."""
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
            raise RenderError(f"could not read music duration: {music_config['path']}")
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
                "warnings": warnings,
                "originals_dir": originals_label,
                "source": source,
                "audio_clean": audio_clean_info,
                "music": music_info,
                "wall_time_s": round(time.monotonic() - started, 3),
                "llm_cost_usd": 0.0,
            }

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

    filter_complex, final_v, final_a = build_filter_graph(
        slots, durations, has_audio, ambience_enabled, width, height, xfade_s, warnings,
        clean_chain=clean_chain, photo_sizes=photo_sizes,
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
            raise RenderError(f"ffmpeg failed: {detail}")
        duration, out_width, out_height = probe_output(tmp_path)
        if (out_width, out_height) != (width, height):
            raise RenderError(f"unexpected output resolution: {out_width}x{out_height}")
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
        summary = render(
            args.edit,
            args.out,
            args.media_dir,
            args.originals_dir,
            args.width,
            args.height,
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
        )
    except (RenderError, SchemaError, AmbienceError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
