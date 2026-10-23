#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scan a folder of video clips and still photos and write ``ingest.json``.

Usage:
    python scripts/ingest.py SOURCE_DIR --workspace DIR
    python scripts/ingest.py SOURCE_DIR --workspace DIR --json

The scan is non-recursive and covers ``.mp4``, ``.mov`` and ``.m4v`` clips plus
``.jpg``, ``.jpeg`` and ``.heic`` stills. Every clip is probed once with
``ffprobe``; the result records duration, fps, resolution, all streams, creation
time and whether a DJI gyro/IMU stream is present. Every photo is read once with
Pillow; the result records size, orientation, format and the EXIF capture time.
The output is validated against ``schemas/ingest.schema.json`` and written only
after the whole scan finished. Source files are never touched.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from common.excluded import filter_excluded, load_excluded
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.errors import report_error
from common.photos import PHOTO_EXTENSIONS, PhotoError, is_photo, photo_metadata
from common.schema import SchemaError, validate_or_raise
from common.summary import summary_fields

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
GYRO_HANDLER_MARKERS = ("dji meta", "gyro", "gyroscope", "motion")
FILENAME_TIME_RE = re.compile(r"(\d{8})_?(\d{6})")


class IngestError(RuntimeError):
    """Raised when a scan cannot be completed."""


class IngestRuntimeError(RuntimeError):
    """Raised when the scan fails after it started (ffprobe, disk)."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ingest.py",
        description=(
            "Scan a folder of video clips and still photos and write ingest.json "
            "with duration, fps, resolution, streams, creation time, gyro presence "
            "and EXIF capture time."
        ),
        epilog=(
            "video extensions: .mp4, .mov, .m4v\n"
            "photo extensions: .jpg, .jpeg, .heic\n"
            "examples:\n"
            "  python scripts/ingest.py <trip-folder> --workspace <workspace>\n"
            "  python scripts/ingest.py <trip-folder> --workspace <workspace> --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "source_dir",
        help="folder of video clips (.mp4, .mov, .m4v) and photos (.jpg, .jpeg, .heic) (non-recursive)",
    )
    parser.add_argument("--workspace", required=True, help="output root; ingest.json is written here")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value):
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_fps(rate):
    """Turn an ffprobe frame-rate string such as ``25/1`` into ``25.0``."""
    if not rate or rate in ("0/0", "N/A"):
        return None
    if "/" not in rate:
        return to_float(rate)
    numerator, denominator = rate.split("/", 1)
    numerator = to_float(numerator)
    denominator = to_float(denominator)
    if not numerator or not denominator:
        return None
    return numerator / denominator


def creation_time_from_name(name):
    """Return ``YYYY-MM-DDTHH:MM:SS`` for the first timestamp in a file name."""
    match = FILENAME_TIME_RE.search(name)
    if not match:
        return None
    date, time = match.groups()
    return f"{date[0:4]}-{date[4:6]}-{date[6:8]}T{time[0:2]}:{time[2:4]}:{time[4:6]}"


def find_creation_time(data, name):
    """Return ``(iso_time, source)`` using format tags, stream tags then the file name."""
    format_tags = (data.get("format") or {}).get("tags") or {}
    if format_tags.get("creation_time"):
        return format_tags["creation_time"], "metadata"
    for stream in data.get("streams") or []:
        stream_tags = stream.get("tags") or {}
        if stream_tags.get("creation_time"):
            return stream_tags["creation_time"], "metadata"
    parsed = creation_time_from_name(name)
    if parsed:
        return parsed, "filename"
    return None, None


def gyro_evidence(stream):
    """Describe a stream recognised as gyro data for the report."""
    parts = [f"stream {stream.get('index')}"]
    if stream.get("codec_tag"):
        parts.append(f"codec_tag={stream['codec_tag']}")
    if stream.get("handler_name"):
        parts.append(f"handler={stream['handler_name']!r}")
    if stream.get("nb_frames") is not None:
        parts.append(f"samples={stream['nb_frames']}")
    return parts[0] + ": " + " ".join(parts[1:]) if len(parts) > 1 else parts[0]


def gyro_from_streams(streams):
    """Return ``(present, evidence)`` for the first stream that carries gyro data.

    A stream qualifies when its codec tag is ``djmd`` or its handler name
    mentions DJI metadata, gyro or motion, and it has more than one sample.
    """
    for stream in streams:
        codec_tag = (stream.get("codec_tag") or "").lower()
        handler_name = (stream.get("handler_name") or "").lower()
        matches = codec_tag == "djmd" or any(
            marker in handler_name for marker in GYRO_HANDLER_MARKERS
        )
        if not matches:
            continue
        nb_frames = stream.get("nb_frames")
        if nb_frames is not None and nb_frames <= 1:
            continue
        return True, gyro_evidence(stream)
    return False, None


def is_attached_picture(stream):
    return (stream.get("disposition") or {}).get("attached_pic") == 1


def primary_video_stream(streams):
    """Return the first real video stream, skipping thumbnails and attached pictures."""
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        if is_attached_picture(stream):
            continue
        if stream.get("width") is None or stream.get("height") is None:
            continue
        if (
            stream.get("codec_name") == "mjpeg"
            and stream.get("duration") is None
            and stream.get("nb_frames") is None
        ):
            continue
        return stream
    return None


def normalize_stream(raw):
    """Turn one ffprobe stream into the flat record written to ``streams[]``."""
    stream = {
        "index": raw.get("index"),
        "codec_type": raw.get("codec_type"),
        "codec_name": raw.get("codec_name"),
        "codec_tag": raw.get("codec_tag_string"),
        "handler_name": (raw.get("tags") or {}).get("handler_name"),
    }
    for key, value in (
        ("width", to_int(raw.get("width"))),
        ("height", to_int(raw.get("height"))),
        ("fps", parse_fps(raw.get("r_frame_rate"))),
        ("sample_rate", to_int(raw.get("sample_rate"))),
        ("channels", to_int(raw.get("channels"))),
        ("nb_frames", to_int(raw.get("nb_frames"))),
        ("duration_s", to_float(raw.get("duration"))),
    ):
        if value is not None:
            stream[key] = value
    return stream


def clip_duration(format_data, primary):
    """Resolve a clip duration from the container, the video stream or its frame count."""
    duration = to_float(format_data.get("duration"))
    if duration is not None:
        return duration
    if primary is None:
        return None
    duration = to_float(primary.get("duration"))
    if duration is not None:
        return duration
    nb_frames = to_int(primary.get("nb_frames"))
    fps = parse_fps(primary.get("r_frame_rate"))
    if nb_frames and fps:
        return nb_frames / fps
    return None


def run_ffprobe(path):
    command = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise IngestRuntimeError(f"could not run ffprobe: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        raise IngestRuntimeError(f"ffprobe failed for {path.name}: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise IngestRuntimeError(f"ffprobe returned invalid JSON for {path.name}: {exc}") from exc


def probe_clip(path):
    """Probe one clip and build its ingest record."""
    data = run_ffprobe(path)
    raw_streams = data.get("streams") or []
    if not raw_streams:
        raise IngestError(f"ffprobe reported no streams for {path.name}")
    streams = [normalize_stream(stream) for stream in raw_streams]
    primary = primary_video_stream(raw_streams)
    warnings = []
    duration = clip_duration(data.get("format") or {}, primary)
    if duration is None:
        duration = 0.0
        warnings.append("no duration reported by ffprobe")
    if primary is None:
        warnings.append("no primary video stream found")
    present, evidence = gyro_from_streams(streams)
    creation_time, creation_time_source = find_creation_time(data, path.name)
    return {
        "file": path.name,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "duration_s": duration,
        "creation_time": creation_time,
        "creation_time_source": creation_time_source,
        "width": to_int(primary.get("width")) if primary else None,
        "height": to_int(primary.get("height")) if primary else None,
        "fps": parse_fps(primary.get("r_frame_rate")) if primary else None,
        "video_codec": primary.get("codec_name") if primary else None,
        "nb_frames": to_int(primary.get("nb_frames")) if primary else None,
        "streams": streams,
        "gyro": {"present": present, "evidence": evidence},
        "warnings": warnings,
    }


def find_clips(source):
    return sorted(
        (
            path
            for path in source.iterdir()
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        ),
        key=lambda path: path.name,
    )


def find_photos(source):
    return sorted(
        (path for path in source.iterdir() if path.is_file() and is_photo(path)),
        key=lambda path: path.name,
    )


def probe_photo(path):
    """Probe one still photo and build its ingest record."""
    try:
        metadata = photo_metadata(path)
    except PhotoError as exc:
        raise IngestError(str(exc)) from exc
    metadata["warnings"] = []
    return metadata


def atomic_write_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def scan(source_dir, workspace):
    """Scan ``source_dir`` and write ``workspace/ingest.json``; return ``(data, output_path)``."""
    source = Path(source_dir).expanduser().resolve()
    if not source.exists():
        raise IngestError(f"source directory not found: {source}")
    if not source.is_dir():
        raise IngestError(f"not a directory: {source}")
    clips = find_clips(source)
    photos = find_photos(source)
    if not clips and not photos:
        raise IngestError(
            f"no video files ({', '.join(sorted(VIDEO_EXTENSIONS))}) or photos "
            f"({', '.join(sorted(PHOTO_EXTENSIONS))}) in {source}"
        )
    excluded = load_excluded(workspace)
    clips = filter_excluded(clips, excluded)
    photos = filter_excluded(photos, excluded)
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source),
        "clip_count": len(clips),
        "clips": [probe_clip(path) for path in clips],
        "photo_count": len(photos),
        "photos": [probe_photo(path) for path in photos],
        "warnings": [],
    }
    validate_or_raise(data, "ingest")
    output_dir = Path(workspace).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "ingest.json"
    atomic_write_json(output_path, data)
    return data, output_path


def main(argv=None):
    started = time.monotonic()
    args = build_parser().parse_args(argv)
    try:
        data, output_path = scan(args.source_dir, args.workspace)
    except IngestRuntimeError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except (IngestError, SchemaError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    except OSError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    clips = data["clips"]
    summary = {
        "clips": len(clips),
        "photos": data["photo_count"],
        "output": str(output_path),
        "gyro_clips": sum(1 for clip in clips if clip["gyro"]["present"]),
        "total_duration_s": round(sum(clip["duration_s"] for clip in clips), 3),
        **summary_fields(started),
    }
    if args.json:
        print(json.dumps(summary))
    else:
        print(
            f"scanned {summary['clips']} clips and {summary['photos']} photos "
            f"({summary['total_duration_s']} s total, {summary['gyro_clips']} with gyro)"
        )
        print(f"wrote {summary['output']}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
