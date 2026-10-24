#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the manual-upload publish kit from the publish chain's existing outputs.

Usage:
    python3 scripts/publish_kit.py --project DIR --final-video FILE \
        [--name NAME] [--kit-loudness] [--kit-title TITLE] [--json]

``publish_kit.py`` reads the outputs the publish chain already wrote
(``metadata.json``, ``<name>_RO.srt``, ``<name>_EN.srt``, ``thumbnail.jpg`` and
the final video) and writes ``PROJECT/publish-kit/`` for a manual YouTube Studio
upload. It never re-runs or overwrites the chain's outputs, and it links the
final video (hard link, else symlink, else only its path in ``studio.md``)
instead of copying it. No model is called; ``llm_cost_usd`` is always 0.0.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from common.constants import KIT_DIR_NAME
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.errors import report_error
from common.jsonio import parse_child_json
from common.sizes import format_size

REPO_ROOT = Path(__file__).resolve().parents[1]
TITLE_MAX = 100
DESCRIPTION_MAX = 5000
TAGS_MAX = 500
THUMBNAIL_MAX_BYTES = 2_000_000
THUMBNAIL_SIZE = (1280, 720)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="publish_kit.py",
        description=(
            "Build the manual-upload publish kit from the publish chain's existing "
            "outputs: copy the RO/EN subtitles and the thumbnail, link the final video "
            "and write studio.md."
        ),
        epilog=(
            "examples:\n"
            "  python3 scripts/publish_kit.py --project out/ --final-video final.mp4 --json\n"
            "  python3 scripts/publish_kit.py --project out/ --final-video final.mp4 \\\n"
            "    --kit-loudness --kit-title \"My title\"\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project",
        required=True,
        help="project folder holding the chain's outputs",
    )
    parser.add_argument(
        "--final-video",
        dest="final_video",
        required=True,
        help="finished edit's video file",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="artifact stem (default: the project folder's name)",
    )
    parser.add_argument(
        "--kit-loudness",
        dest="kit_loudness",
        action="store_true",
        help="run loudness.py on the final video first and link its _loud output",
    )
    parser.add_argument(
        "--kit-title",
        dest="kit_title",
        default=None,
        help="chosen Studio title (default: the first metadata.json title)",
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def say(json_mode, message):
    """Print a progress line to stdout, or to stderr when ``--json`` keeps stdout pure."""
    stream = sys.stderr if json_mode else sys.stdout
    print(message, file=stream, flush=True)


def load_metadata_json(project):
    """Return ``(data, path)`` for the metadata side-output; data is None when missing or invalid."""
    path = Path(project) / "metadata.json"
    if not path.is_file():
        return None, path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, path
    return (data if isinstance(data, dict) else None), path


def image_dimensions(path):
    """Return ``(width, height)`` of an image file, or None when it cannot be read."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            return image.size
    except (OSError, UnidentifiedImageError):
        return None


def chapter_mmss(timestamp):
    """Render a normalized HH:MM:SS chapter timestamp as MM:SS (or H:MM:SS past an hour)."""
    parts = timestamp.split(":")
    if len(parts) == 3 and parts[0] == "00":
        return f"{parts[1]}:{parts[2]}"
    return timestamp


def chapter_lines(chapters, lang="ro"):
    """Return the YouTube chapter lines (``MM:SS Title``) for the description blocks."""
    lines = []
    for chapter in chapters:
        title = chapter["title"]
        if lang == "en":
            title_en = chapter.get("title_en")
            if isinstance(title_en, str) and title_en:
                title = title_en
        lines.append(f"{chapter_mmss(chapter['timestamp'])} {title}")
    return "\n".join(lines)


def collect_kit_checks(project, name, final_video, kit_title):
    """Return ``(checks, errors)``: the measured check strings and the failing messages."""
    checks = []
    errors = []
    metadata, metadata_path = load_metadata_json(project)
    if metadata is None:
        errors.append(
            f"metadata.json not found: {metadata_path}; run the metadata step first (or --force it)"
        )

    if not Path(final_video).is_file():
        errors.append(f"final video not found: {final_video}")
    else:
        checks.append(f"final_video {final_video}")

    en_srt = Path(project) / f"{name}_EN.srt"
    if not en_srt.is_file():
        errors.append(f"EN subtitles not found: {en_srt}")
    else:
        checks.append(f"en_srt {en_srt}")

    thumbnail = Path(project) / "thumbnail.jpg"
    if not thumbnail.is_file():
        errors.append(f"thumbnail not found: {thumbnail}")
    else:
        size = thumbnail.stat().st_size
        dimensions = image_dimensions(thumbnail)
        if size > THUMBNAIL_MAX_BYTES:
            errors.append(f"thumbnail is {size} bytes, over the 2 MB limit: {thumbnail}")
        if dimensions != THUMBNAIL_SIZE:
            seen = f"{dimensions[0]}x{dimensions[1]}" if dimensions else "unreadable"
            errors.append(f"thumbnail is {seen}, expected 1280x720: {thumbnail}")
        if size <= THUMBNAIL_MAX_BYTES and dimensions == THUMBNAIL_SIZE:
            checks.append(f"thumbnail {dimensions[0]}x{dimensions[1]} {size}B")

    if metadata is not None:
        titles = metadata.get("titles") or []
        title = kit_title or (titles[0] if titles else None)
        if title is None:
            errors.append("no title available: metadata.json has no titles and --kit-title was not given")
        elif len(title) > TITLE_MAX:
            errors.append(f"title is {len(title)} characters, over the {TITLE_MAX} limit")
        else:
            checks.append(f"title {len(title)}/{TITLE_MAX}")

        chapters = metadata.get("chapters") or []
        ro_lines = chapter_lines(chapters)
        en_lines = chapter_lines(chapters, "en")
        ro_description = metadata.get("description_ro") or ""
        en_description = metadata.get("description_en") or ""
        ro_full = f"{ro_description}\n\n{ro_lines}" if ro_lines else ro_description
        en_full = f"{en_description}\n\n{en_lines}" if en_lines else en_description
        if len(ro_full) > DESCRIPTION_MAX:
            errors.append(f"RO description is {len(ro_full)} characters, over the {DESCRIPTION_MAX} limit")
        else:
            checks.append(f"description_ro {len(ro_full)}/{DESCRIPTION_MAX}")
        if len(en_full) > DESCRIPTION_MAX:
            errors.append(f"EN description is {len(en_full)} characters, over the {DESCRIPTION_MAX} limit")
        else:
            checks.append(f"description_en {len(en_full)}/{DESCRIPTION_MAX}")

        tags_line = ", ".join(metadata.get("tags") or [])
        if len(tags_line) > TAGS_MAX:
            errors.append(f"tags line is {len(tags_line)} characters, over the {TAGS_MAX} limit")
        else:
            checks.append(f"tags {len(tags_line)}/{TAGS_MAX}")

    return checks, errors


def build_studio_md(name, metadata, title, video_display, video_linked):
    """Render the Studio sheet with one copy-ready block per field and a Studio-order checklist."""
    titles = metadata.get("titles") or []
    others = [option for option in titles if option != title][:2]
    chapters = metadata.get("chapters") or []
    ro_lines = chapter_lines(chapters)
    en_lines = chapter_lines(chapters, "en")
    ro_description = metadata.get("description_ro") or ""
    en_description = metadata.get("description_en") or ""
    ro_full = f"{ro_description}\n\n{ro_lines}" if ro_lines else ro_description
    en_full = f"{en_description}\n\n{en_lines}" if en_lines else en_description
    tags_line = ", ".join(metadata.get("tags") or [])
    pinned = metadata.get("pinned_comment") or ""
    if video_linked:
        upload_line = f"Upload: `{video_display}`"
    else:
        upload_line = f"Upload: `{video_display}` (not copied into the kit)"
    parts = [
        f"# Studio kit — {name}",
        "",
        upload_line,
        "",
        "## Checklist (Studio order)",
        "",
        "- [ ] Upload the video",
        "- [ ] Paste the title",
        "- [ ] Paste the description (RO)",
        "- [ ] Upload the thumbnail",
        "- [ ] Upload the subtitles RO + EN (as subtitles, not closed captions)",
        "- [ ] Set visibility to **private** first",
        "- [ ] Pick the playlist, category and language",
        "- [ ] Paste the pinned comment",
        "- [ ] Add end screen / cards if any",
        "- [ ] Publish when ready",
        "",
        "## Title",
        "",
        "```",
        title,
        "```",
        "",
    ]
    if others:
        parts.append("Other options:")
        parts.extend(f"- {option}" for option in others)
        parts.append("")
    parts.extend([
        "## Description (RO)",
        "",
        "```",
        ro_full,
        "```",
        "",
        "## Description (EN)",
        "",
        "```",
        en_full,
        "```",
        "",
        f"## Tags ({len(tags_line)}/{TAGS_MAX} characters)",
        "",
        "```",
        tags_line,
        "```",
        "",
        "## Pinned comment",
        "",
        "```",
        pinned,
        "```",
        "",
        "## Reminders",
        "",
        "- Pick the playlist.",
        "- Pick the closest category in Studio.",
        "- Video language: Romanian.",
        "- Default caption language: RO.",
        "- Made for kids: no.",
        "- Visibility: private until you flip it.",
        "",
    ])
    return "\n".join(parts)


def link_video(source, dest):
    """Link ``source`` at ``dest``: hard link, else symlink, else None (path-only)."""
    try:
        os.link(source, dest)
        return "hardlink"
    except OSError:
        pass
    try:
        os.symlink(source, dest)
        return "symlink"
    except OSError:
        return None


def run_loudness(final_video):
    """Run loudness.py on the final video and return ``(output_path, error)``."""
    argv = [sys.executable, "scripts/loudness.py", "--video", str(final_video), "--json"]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, cwd=str(REPO_ROOT))
    except OSError as exc:
        return None, str(exc)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        return None, "\n".join(tail[-5:]) or f"exit {result.returncode}"
    data = parse_child_json(result.stdout)
    output = data.get("output") if isinstance(data, dict) else None
    if not output:
        return None, "loudness.py did not report an output file"
    return Path(output), None


def write_kit(project, name, video_source, metadata, title):
    """Write the kit folder from the checked inputs and return ``(kit_dir, video_link)``."""
    kit_dir = Path(project) / KIT_DIR_NAME
    if kit_dir.exists():
        shutil.rmtree(kit_dir)
    kit_dir.mkdir(parents=True)
    shutil.copy2(Path(project) / f"{name}_RO.srt", kit_dir / f"{name}_RO.srt")
    shutil.copy2(Path(project) / f"{name}_EN.srt", kit_dir / f"{name}_EN.srt")
    shutil.copy2(Path(project) / "thumbnail.jpg", kit_dir / "thumbnail.jpg")
    video_link = link_video(video_source, kit_dir / video_source.name)
    if video_link is None:
        video_display = str(video_source)
        video_linked = False
    else:
        video_display = video_source.name
        video_linked = True
    (kit_dir / "studio.md").write_text(
        build_studio_md(name, metadata, title, video_display, video_linked), encoding="utf-8"
    )
    return kit_dir, video_link


def main(argv=None):
    args = build_parser().parse_args(argv)
    started = time.monotonic()
    project = Path(args.project).expanduser().resolve()
    name = args.name or project.name
    final_video = Path(args.final_video).expanduser().resolve()

    checks, errors = collect_kit_checks(project, name, final_video, args.kit_title)
    if errors:
        return report_error("; ".join(errors), EXIT_USAGE, args.json)

    metadata, _ = load_metadata_json(project)
    titles = (metadata or {}).get("titles") or []
    title = args.kit_title or (titles[0] if titles else None)

    video_source = final_video
    if args.kit_loudness:
        say(args.json, f"[kit] --kit-loudness writes a large file: {final_video}")
        loud, error = run_loudness(final_video)
        if error is not None:
            return report_error(f"loudness failed: {error}", EXIT_RUNTIME, args.json)
        video_source = loud

    try:
        size = video_source.stat().st_size
    except OSError as exc:
        return report_error(f"cannot read the final video: {exc}", EXIT_RUNTIME, args.json)
    say(args.json, f"[kit] linking the final video ({format_size(size)}) into the kit: {video_source}")

    try:
        kit_dir, video_link = write_kit(project, name, video_source, metadata, title)
    except OSError as exc:
        return report_error(f"could not write the kit: {exc}", EXIT_RUNTIME, args.json)

    summary = {
        "output": f"{KIT_DIR_NAME}/studio.md",
        "video": video_source.name,
        "video_link": video_link or "path",
        "title": title,
        "checks": checks,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }
    if args.json:
        print(json.dumps(summary))
    else:
        print(f"kit: {kit_dir}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
