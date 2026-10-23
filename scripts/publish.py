#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the publish kit for a finished edit: subtitles, corrections, verify, review, translation, thumbnail, metadata and the manual-upload kit.

Usage:
    python3 scripts/publish.py --project DIR --context FILE \
        (--fcproj PROJ --sequence NAME [--track T] | --from-edit EDIT.json | --media FILE) \
        [--words-dir DIR] [--name NAME] [--resume] [--force] [--json] \
        [--chapters FILE] \
        [--kit [--final-video FILE] [--kit-loudness] [--kit-title TITLE]]

``publish.py`` is the single entry point for the publish chain: it chains the
existing station CLIs as subprocesses in order (subs_from_edit or transcribe,
correct_srt, a subtitle/video match check, a human-review stop, translate_srt,
thumbnail, metadata and the manual-upload kit), skips a step whose output already
exists, keeps a state file in the project folder and prints a ``--json`` summary
with the wall time and the total LLM cost. It imports no other tool and calls no
model itself. Originals are read-only; everything is written under the project
folder.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error
from common.ffmpeg_tools import probe_duration

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = "scripts/publish.py"
STATE_NAME = "publish_state.json"
COST_LINE = re.compile(r"LLM cost:\s*\$([0-9]+(?:\.[0-9]+)?)")
KIT_DIR_NAME = "publish-kit"
TITLE_MAX = 100
DESCRIPTION_MAX = 5000
TAGS_MAX = 500
THUMBNAIL_MAX_BYTES = 2_000_000
THUMBNAIL_SIZE = (1280, 720)
MATCH_TOLERANCE_S = 2.0
SRT_END = re.compile(r"--> (\d{2}):(\d{2}):(\d{2}),(\d{3})")


class PublishError(RuntimeError):
    """Raised when a publish run cannot be started."""


class Context:
    """Paths and flags shared by every step in one publish run."""

    def __init__(
        self,
        project,
        name,
        source_kind,
        context_path,
        python,
        repo_root,
        fcproj=None,
        sequence=None,
        track=None,
        from_edit=None,
        media=None,
        words_dir=None,
        photo=None,
        title=None,
        keyword=None,
        theme=None,
        kicker=None,
        previous_url=None,
        repo_url=None,
        chapters=None,
        transcribe_backend="whisper",
        kit=False,
        final_video=None,
        kit_loudness=False,
        kit_title=None,
        resume=False,
        force=False,
        json_mode=False,
    ):
        self.project = project
        self.name = name
        self.source_kind = source_kind
        self.context_path = context_path
        self.python = python
        self.repo_root = repo_root
        self.fcproj = fcproj
        self.sequence = sequence
        self.track = track
        self.from_edit = from_edit
        self.media = media
        self.words_dir = words_dir
        self.photo = photo
        self.title = title
        self.keyword = keyword
        self.theme = theme
        self.kicker = kicker
        self.previous_url = previous_url
        self.repo_url = repo_url
        self.chapters = chapters
        self.transcribe_backend = transcribe_backend
        self.kit = kit
        self.final_video = final_video
        self.kit_loudness = kit_loudness
        self.kit_title = kit_title
        self.resume = resume
        self.force = force
        self.json_mode = json_mode


def build_parser():
    parser = argparse.ArgumentParser(
        prog="publish.py",
        usage=(
            "publish.py [-h] --project PROJECT --context CONTEXT"
            " (--fcproj PROJ --sequence NAME [--track T] | --from-edit EDIT.json | --media FILE)"
            " [--words-dir DIR] [--name NAME]"
            " [--photo FILE --title T --keyword K --theme {tech,creator} [--kicker K]]"
            " [--previous-url URL] [--repo-url URL] [--chapters FILE]"
            " [--transcribe-backend {whisper,mock}] [--resume] [--force] [--json]"
            " [--kit [--final-video FILE] [--kit-loudness] [--kit-title TITLE]]"
        ),
        description=(
            "Run the publish kit for a finished edit: subtitles, corrections, a subtitle/video "
            "match check, a human-review stop, translation, thumbnail, metadata and the "
            "manual-upload kit."
        ),
        epilog=(
            "steps: subtitles -> corrections -> verify -> review -> translation -> thumbnail -> "
            "metadata -> kit (only with --kit)\n"
            "example:\n"
            "  python3 scripts/publish.py --project out/ --context ctx.md \\\n"
            "    --fcproj project.fcproj --sequence Main --words-dir words/ --json\n"
            "  python3 scripts/publish.py --project out/ --context ctx.md \\\n"
            "    --fcproj project.fcproj --sequence Main --words-dir words/ --resume --json\n"
            "  python3 scripts/publish.py --project out/ --context ctx.md \\\n"
            "    --fcproj project.fcproj --sequence Main --words-dir words/ --resume \\\n"
            "    --kit --final-video final.mp4 --kit-loudness --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project",
        required=True,
        help="project folder (created if missing); all outputs and the state file live here",
    )
    parser.add_argument("--context", required=True, help="context note for metadata.py")
    parser.add_argument("--fcproj", help="FilmCraft project file (JSON)")
    parser.add_argument("--sequence", help="sequence name inside the FilmCraft project")
    parser.add_argument(
        "--track",
        default=None,
        help="audio track name inside the sequence (default: the child's 'Audio 1')",
    )
    parser.add_argument(
        "--from-edit",
        dest="from_edit",
        help="edit.json edit list to map instead of a FilmCraft project",
    )
    parser.add_argument("--media", help="media file to transcribe when no edit project exists")
    parser.add_argument(
        "--words-dir",
        dest="words_dir",
        help="folder with one Whisper word JSON per source clip (required with --fcproj/--from-edit)",
    )
    parser.add_argument("--name", help="artifact stem (default: the project folder's name)")
    parser.add_argument("--photo", help="portrait photo for the thumbnail step")
    parser.add_argument("--title", help="thumbnail title line")
    parser.add_argument("--keyword", help="thumbnail keyword line")
    parser.add_argument("--theme", choices=("tech", "creator"), help="thumbnail brand theme")
    parser.add_argument("--kicker", default=None, help="thumbnail kicker line")
    parser.add_argument(
        "--previous-url",
        dest="previous_url",
        default=None,
        help="earlier clip to link in the metadata draft",
    )
    parser.add_argument(
        "--repo-url",
        dest="repo_url",
        default=None,
        help="the clip's own code repo for the metadata draft",
    )
    parser.add_argument(
        "--chapters",
        default=None,
        help="chapters.json for the metadata step instead of the model's chapters",
    )
    parser.add_argument(
        "--transcribe-backend",
        dest="transcribe_backend",
        choices=("whisper", "mock"),
        default="whisper",
        help="backend for transcribe.py (default: whisper)",
    )
    parser.add_argument(
        "--kit",
        action="store_true",
        help="build the manual-upload publish kit as the last step of the chain",
    )
    parser.add_argument(
        "--final-video",
        dest="final_video",
        default=None,
        help="finished edit video for the kit (default: the --media file)",
    )
    parser.add_argument(
        "--kit-loudness",
        dest="kit_loudness",
        action="store_true",
        help="run loudness.py on the final video before copying it into the kit",
    )
    parser.add_argument(
        "--kit-title",
        dest="kit_title",
        default=None,
        help="chosen Studio title (default: the first metadata.json title)",
    )
    parser.add_argument(
        "--resume", action="store_true", help="continue past the human-review stop"
    )
    parser.add_argument(
        "--force", action="store_true", help="re-run every step even when its output exists"
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def resolve_source(args):
    """Return the single source kind, or raise when the source flags conflict."""
    kinds = []
    if args.fcproj:
        kinds.append("fcproj")
    if args.from_edit:
        kinds.append("from_edit")
    if args.media:
        kinds.append("media")
    if len(kinds) != 1:
        raise PublishError("give exactly one source: --fcproj/--sequence, --from-edit or --media")
    kind = kinds[0]
    if kind == "fcproj" and not args.sequence:
        raise PublishError("--fcproj requires --sequence")
    if kind in ("fcproj", "from_edit") and not args.words_dir:
        raise PublishError("--words-dir is required with --fcproj or --from-edit")
    if kind == "media" and args.words_dir:
        raise PublishError("--words-dir is not allowed with --media")
    return kind


def validate_thumbnail(args):
    """Check that the thumbnail flags are either all present with --photo or all absent."""
    required = {"--title": args.title, "--keyword": args.keyword, "--theme": args.theme}
    if args.photo:
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise PublishError(f"--photo requires {' and '.join(missing)}")
        return
    given = [name for name, value in required.items() if value]
    if given:
        raise PublishError(f"{' and '.join(given)} given without --photo")


def validate_kit(args):
    """Check that the kit flags are only given together with --kit."""
    if args.kit:
        return
    given = [
        name
        for name, value in (("--final-video", args.final_video), ("--kit-title", args.kit_title))
        if value
    ]
    if args.kit_loudness:
        given.append("--kit-loudness")
    if given:
        raise PublishError(f"{' and '.join(given)} given without --kit")


def build_context(args):
    """Validate the arguments and return the shared run context."""
    project = Path(args.project).expanduser().resolve()
    project.mkdir(parents=True, exist_ok=True)
    if not os.access(project, os.W_OK):
        raise PublishError(f"project folder is not writable: {project}")
    source_kind = resolve_source(args)
    validate_thumbnail(args)
    validate_kit(args)
    return Context(
        project=project,
        name=args.name or project.name,
        source_kind=source_kind,
        context_path=Path(args.context).expanduser().resolve(),
        python=sys.executable,
        repo_root=REPO_ROOT,
        fcproj=Path(args.fcproj).expanduser().resolve() if args.fcproj else None,
        sequence=args.sequence,
        track=args.track,
        from_edit=Path(args.from_edit).expanduser().resolve() if args.from_edit else None,
        media=Path(args.media).expanduser().resolve() if args.media else None,
        words_dir=Path(args.words_dir).expanduser().resolve() if args.words_dir else None,
        photo=Path(args.photo).expanduser().resolve() if args.photo else None,
        title=args.title,
        keyword=args.keyword,
        theme=args.theme,
        kicker=args.kicker,
        previous_url=args.previous_url,
        repo_url=args.repo_url,
        chapters=Path(args.chapters).expanduser().resolve() if args.chapters else None,
        transcribe_backend=args.transcribe_backend,
        kit=args.kit,
        final_video=Path(args.final_video).expanduser().resolve() if args.final_video else None,
        kit_loudness=args.kit_loudness,
        kit_title=args.kit_title,
        resume=args.resume,
        force=args.force,
        json_mode=args.json,
    )


def build_subtitles_step(ctx):
    """Return the subtitles step spec for the chosen source."""
    if ctx.source_kind == "media":
        argv = [
            ctx.python,
            "scripts/transcribe.py",
            str(ctx.media),
            "--out-dir",
            str(ctx.project),
            "--name",
            ctx.name,
            "--backend",
            ctx.transcribe_backend,
            "--json",
        ]
    elif ctx.source_kind == "fcproj":
        argv = [
            ctx.python,
            "scripts/subs_from_edit.py",
            "--fcproj",
            str(ctx.fcproj),
            "--sequence",
            ctx.sequence,
        ]
        if ctx.track:
            argv += ["--track", ctx.track]
        argv += [
            "--words-dir",
            str(ctx.words_dir),
            "--out-dir",
            str(ctx.project),
            "--name",
            ctx.name,
            "--json",
        ]
    else:
        argv = [
            ctx.python,
            "scripts/subs_from_edit.py",
            "--from-edit",
            str(ctx.from_edit),
            "--words-dir",
            str(ctx.words_dir),
            "--out-dir",
            str(ctx.project),
            "--name",
            ctx.name,
            "--json",
        ]
    return {"name": "subtitles", "argv": argv}


def build_steps(ctx):
    """Return the ordered step specs for this run, each with its command list."""
    steps = [build_subtitles_step(ctx)]
    steps.append(
        {
            "name": "correct",
            "argv": [
                ctx.python,
                "scripts/correct_srt.py",
                str(ctx.project / f"{ctx.name}.srt"),
                str(ctx.project / f"{ctx.name}_RO.srt"),
            ],
        }
    )
    if resolve_final_video(ctx) is None:
        steps.append({"name": "verify", "skip_reason": "no final video"})
    else:
        steps.append({"name": "verify"})
    steps.append({"name": "review"})
    steps.append(
        {
            "name": "translate",
            "argv": [
                ctx.python,
                "scripts/translate_srt.py",
                str(ctx.project / f"{ctx.name}_RO.srt"),
                str(ctx.project / f"{ctx.name}_EN.srt"),
            ],
            "parse_cost_line": True,
        }
    )
    if ctx.photo:
        argv = [
            ctx.python,
            "scripts/thumbnail.py",
            "--photo",
            str(ctx.photo),
            "--title",
            ctx.title,
            "--keyword",
            ctx.keyword,
            "--theme",
            ctx.theme,
        ]
        if ctx.kicker:
            argv += ["--kicker", ctx.kicker]
        argv += ["--out", str(ctx.project), "--json"]
        steps.append({"name": "thumbnail", "argv": argv})
    else:
        steps.append({"name": "thumbnail", "skip_reason": "no photo"})
    metadata_argv = [
        ctx.python,
        "scripts/metadata.py",
        str(ctx.project / f"{ctx.name}_RO.srt"),
        "--context",
        str(ctx.context_path),
        "--out-dir",
        str(ctx.project),
        "--name",
        ctx.name,
    ]
    if ctx.previous_url:
        metadata_argv += ["--previous-url", ctx.previous_url]
    if ctx.repo_url:
        metadata_argv += ["--repo-url", ctx.repo_url]
    if ctx.chapters:
        metadata_argv += ["--chapters", str(ctx.chapters)]
    metadata_argv.append("--json")
    steps.append({"name": "metadata", "argv": metadata_argv})
    if ctx.kit:
        steps.append({"name": "kit"})
    return steps


def step_outputs(name, ctx):
    """Return the expected output paths of a step, whether or not they exist yet."""
    project = ctx.project
    if name == "subtitles":
        return [str(project / f"{ctx.name}.srt"), str(project / f"{ctx.name}_words.json")]
    if name == "correct":
        return [str(project / f"{ctx.name}_RO.srt")]
    if name == "translate":
        return [str(project / f"{ctx.name}_EN.srt")]
    if name == "thumbnail":
        return [str(project / "thumbnail.jpg")]
    if name == "metadata":
        return [str(project / "metadata.md")]
    if name == "kit":
        return [str(project / KIT_DIR_NAME / "studio.md")]
    return []


def step_outputs_exist(name, ctx):
    """True when every output of a step already exists (the skip rule)."""
    outputs = step_outputs(name, ctx)
    return bool(outputs) and all(Path(path).is_file() for path in outputs)


def normalize_subs_output(ctx):
    """Rename the subs_from_edit ``<name>_RO.srt`` to the working ``<name>.srt``."""
    raw = ctx.project / f"{ctx.name}.srt"
    ro = ctx.project / f"{ctx.name}_RO.srt"
    if ro.is_file():
        os.replace(ro, raw)


def parse_child_json(stdout):
    """Return the child's JSON summary object, or None when there is none."""
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def child_outputs(data):
    """Collect the output paths a child reported in its JSON summary."""
    if not isinstance(data, dict):
        return []
    found = []
    raw = data.get("outputs")
    if isinstance(raw, dict):
        found.extend(str(value) for value in raw.values() if value)
    elif isinstance(raw, list):
        found.extend(str(value) for value in raw if value)
    for key in ("output", "thumbnail"):
        value = data.get(key)
        if value:
            found.append(str(value))
    return found


def child_cost(data):
    """Return the ``llm_cost_usd`` a child reported, or 0.0."""
    if not isinstance(data, dict):
        return 0.0
    value = data.get("llm_cost_usd")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def parse_cost_line(stdout):
    """Parse the ``LLM cost: $X.XXXX`` line a child printed, or 0.0."""
    match = COST_LINE.search(stdout or "")
    return float(match.group(1)) if match else 0.0


def execute(step, argv, ctx):
    """Run one step as a subprocess and return its status, outputs and LLM cost."""
    started = time.monotonic()
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, cwd=str(ctx.repo_root)
        )
    except OSError as exc:
        return {
            "status": "FAIL",
            "returncode": 1,
            "outputs": [],
            "llm_cost_usd": 0.0,
            "stderr": str(exc),
            "stdout": "",
            "wall_time_s": round(time.monotonic() - started, 3),
        }
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    wall_time_s = round(time.monotonic() - started, 3)
    if result.returncode != 0:
        return {
            "status": "FAIL",
            "returncode": result.returncode,
            "outputs": [],
            "llm_cost_usd": 0.0,
            "stderr": stderr,
            "stdout": stdout,
            "wall_time_s": wall_time_s,
        }
    data = parse_child_json(stdout)
    llm_cost_usd = child_cost(data)
    if step.get("parse_cost_line"):
        llm_cost_usd = parse_cost_line(stdout)
    return {
        "status": "done",
        "returncode": 0,
        "outputs": child_outputs(data),
        "llm_cost_usd": llm_cost_usd,
        "stderr": stderr,
        "stdout": stdout,
        "wall_time_s": wall_time_s,
    }


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_options(ctx):
    """Return the recorded run options for the state file."""
    return {
        "source": ctx.source_kind,
        "fcproj": str(ctx.fcproj) if ctx.fcproj else None,
        "sequence": ctx.sequence,
        "track": ctx.track,
        "from_edit": str(ctx.from_edit) if ctx.from_edit else None,
        "media": str(ctx.media) if ctx.media else None,
        "words_dir": str(ctx.words_dir) if ctx.words_dir else None,
        "photo": str(ctx.photo) if ctx.photo else None,
        "previous_url": ctx.previous_url,
        "repo_url": ctx.repo_url,
        "chapters": str(ctx.chapters) if ctx.chapters else None,
        "kit": ctx.kit,
        "final_video": str(ctx.final_video) if ctx.final_video else None,
        "kit_loudness": ctx.kit_loudness,
        "kit_title": ctx.kit_title,
    }


def new_state(ctx):
    return {
        "tool": TOOL,
        "name": ctx.name,
        "options": build_options(ctx),
        "steps": {},
    }


def load_state(project):
    path = Path(project) / STATE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_state(project, state):
    """Write the state file atomically."""
    path = Path(project) / STATE_NAME
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def record_step(state, name, status, command=None, outputs=None, reason=None):
    """Replace one step's record in the state file."""
    entry = {"status": status}
    if command is not None:
        entry["command"] = list(command)
    if outputs is not None:
        entry["outputs"] = list(outputs)
    if reason is not None:
        entry["reason"] = reason
    entry["finished_at"] = now_iso()
    state.setdefault("steps", {})[name] = entry


def print_progress(ctx, index, total, name, status, wall_time_s, detail):
    line = f"[{index:2d}/{total:2d}] {name:<10} {status:<7} {wall_time_s:7.1f}s  {detail}"
    stream = sys.stderr if ctx.json_mode else sys.stdout
    print(line, file=stream)


def print_start(ctx, index, total, name):
    stream = sys.stderr if ctx.json_mode else sys.stdout
    print(f"[{index:2d}/{total:2d}] {name:<10} start", file=stream, flush=True)


def print_failure(name, result):
    code = result.get("returncode")
    print(f"[error] {name} failed (exit {code})", file=sys.stderr)
    lines = (result.get("stderr") or "").strip().splitlines()
    for line in lines[-20:]:
        print(line, file=sys.stderr)


def resolve_final_video(ctx):
    """Return the final video path for the kit, or None when it cannot be resolved."""
    if ctx.final_video:
        return ctx.final_video
    if ctx.source_kind == "media":
        return ctx.media
    return None


def srt_end_seconds(path):
    """Return the last cue's end time in seconds, or None when there is none."""
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    matches = SRT_END.findall(content)
    if not matches:
        return None
    hours, minutes, seconds, millis = matches[-1]
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def format_seconds(seconds):
    """Render a duration as MM:SS, or H:MM:SS past an hour."""
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def load_metadata_json(ctx):
    """Return ``(data, path)`` for the metadata side-output; data is None when missing or invalid."""
    path = ctx.project / "metadata.json"
    if not path.is_file():
        return None, path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, path
    return (data if isinstance(data, dict) else None), path


def chapter_mmss(timestamp):
    """Render a normalized HH:MM:SS chapter timestamp as MM:SS (or H:MM:SS past an hour)."""
    parts = timestamp.split(":")
    if len(parts) == 3 and parts[0] == "00":
        return f"{parts[1]}:{parts[2]}"
    return timestamp


def chapter_lines(chapters):
    """Return the YouTube chapter lines (``MM:SS Title``) for the description blocks."""
    return "\n".join(
        f"{chapter_mmss(chapter['timestamp'])} {chapter['title']}" for chapter in chapters
    )


def image_dimensions(path):
    """Return ``(width, height)`` of an image file, or None when it cannot be read."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            return image.size
    except (OSError, UnidentifiedImageError):
        return None


def collect_kit_checks(ctx):
    """Return ``(checks, errors)``: the measured check strings and the failing messages."""
    checks = []
    errors = []
    metadata, metadata_path = load_metadata_json(ctx)
    if metadata is None:
        errors.append(
            f"metadata.json not found: {metadata_path}; run the metadata step first (or --force it)"
        )

    final_video = resolve_final_video(ctx)
    if final_video is None:
        errors.append("--final-video is required with --kit when the source is --fcproj or --from-edit")
    elif not Path(final_video).is_file():
        errors.append(f"final video not found: {final_video}")
    else:
        checks.append(f"final_video {final_video}")

    en_srt = ctx.project / f"{ctx.name}_EN.srt"
    if not en_srt.is_file():
        errors.append(f"EN subtitles not found: {en_srt}")
    else:
        checks.append(f"en_srt {en_srt}")

    thumbnail = ctx.project / "thumbnail.jpg"
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
        title = ctx.kit_title or (titles[0] if titles else None)
        if title is None:
            errors.append("no title available: metadata.json has no titles and --kit-title was not given")
        elif len(title) > TITLE_MAX:
            errors.append(f"title is {len(title)} characters, over the {TITLE_MAX} limit")
        else:
            checks.append(f"title {len(title)}/{TITLE_MAX}")

        chapters = metadata.get("chapters") or []
        lines = chapter_lines(chapters)
        ro_description = metadata.get("description_ro") or ""
        en_description = metadata.get("description_en") or ""
        ro_full = f"{ro_description}\n\n{lines}" if lines else ro_description
        en_full = f"{en_description}\n\n{lines}" if lines else en_description
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


def kit_info(ctx, checks, video_name=None):
    """Return the ``kit`` summary object for the run."""
    metadata, _ = load_metadata_json(ctx)
    titles = (metadata or {}).get("titles") or []
    title = ctx.kit_title or (titles[0] if titles else None)
    if video_name is None:
        final_video = resolve_final_video(ctx)
        video_name = Path(final_video).name if final_video else None
    return {
        "output": KIT_DIR_NAME,
        "video": video_name,
        "title": title,
        "checks": checks,
    }


def run_loudness(ctx, final_video):
    """Run loudness.py on the final video and return ``(output_path, error)``."""
    argv = [ctx.python, "scripts/loudness.py", "--video", str(final_video), "--json"]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, cwd=str(ctx.repo_root))
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


def build_studio_md(ctx, metadata, video_name):
    """Render the Studio sheet with one copy-ready block per field and a Studio-order checklist."""
    titles = metadata.get("titles") or []
    title = ctx.kit_title or (titles[0] if titles else "")
    others = [option for option in titles if option != title][:2]
    chapters = metadata.get("chapters") or []
    lines = chapter_lines(chapters)
    ro_description = metadata.get("description_ro") or ""
    en_description = metadata.get("description_en") or ""
    ro_full = f"{ro_description}\n\n{lines}" if lines else ro_description
    en_full = f"{en_description}\n\n{lines}" if lines else en_description
    tags_line = ", ".join(metadata.get("tags") or [])
    pinned = metadata.get("pinned_comment") or ""
    parts = [
        f"# Studio kit — {ctx.name}",
        "",
        f"Upload: `{video_name}`",
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


def write_kit(ctx, video_source):
    """Write the kit folder from the checked inputs and return its path."""
    kit_dir = ctx.project / KIT_DIR_NAME
    if kit_dir.exists():
        shutil.rmtree(kit_dir)
    kit_dir.mkdir(parents=True)
    video_name = Path(video_source).name
    shutil.copy2(video_source, kit_dir / video_name)
    shutil.copy2(ctx.project / f"{ctx.name}_RO.srt", kit_dir / f"{ctx.name}_RO.srt")
    shutil.copy2(ctx.project / f"{ctx.name}_EN.srt", kit_dir / f"{ctx.name}_EN.srt")
    shutil.copy2(ctx.project / "thumbnail.jpg", kit_dir / "thumbnail.jpg")
    metadata, _ = load_metadata_json(ctx)
    (kit_dir / "studio.md").write_text(
        build_studio_md(ctx, metadata, video_name), encoding="utf-8"
    )
    return kit_dir


def execute_kit(ctx):
    """Run the kit step: check every Studio limit, optionally loudness, then write the kit folder."""
    started = time.monotonic()
    checks, errors = collect_kit_checks(ctx)
    if errors:
        return {
            "status": "FAIL",
            "returncode": 2,
            "outputs": [],
            "llm_cost_usd": 0.0,
            "stderr": "\n".join(f"[error] {error}" for error in errors),
            "stdout": "",
            "wall_time_s": round(time.monotonic() - started, 3),
            "kit": kit_info(ctx, checks),
        }
    final_video = resolve_final_video(ctx)
    video_source = final_video
    if ctx.kit_loudness:
        loud, error = run_loudness(ctx, final_video)
        if error is not None:
            return {
                "status": "FAIL",
                "returncode": EXIT_RUNTIME,
                "outputs": [],
                "llm_cost_usd": 0.0,
                "stderr": f"[error] loudness failed: {error}",
                "stdout": "",
                "wall_time_s": round(time.monotonic() - started, 3),
                "kit": kit_info(ctx, checks),
            }
        video_source = loud
    kit_dir = write_kit(ctx, video_source)
    return {
        "status": "done",
        "returncode": 0,
        "outputs": [str(kit_dir / "studio.md")],
        "llm_cost_usd": 0.0,
        "stderr": "",
        "stdout": "",
        "wall_time_s": round(time.monotonic() - started, 3),
        "kit": kit_info(ctx, checks, video_name=Path(video_source).name),
    }


def execute_verify(ctx):
    """Check that the corrected SRT matches the final video before the rest of the chain."""
    started = time.monotonic()
    errors = []
    final_video = resolve_final_video(ctx)
    srt = ctx.project / f"{ctx.name}_RO.srt"
    if not srt.is_file():
        errors.append(f"RO subtitles not found: {srt}")
    if final_video is None:
        errors.append("no final video to compare the subtitles against")
    elif not Path(final_video).is_file():
        errors.append(f"final video not found: {final_video}")
    if not errors:
        duration = probe_duration(final_video)
        end = srt_end_seconds(srt)
        if duration is None:
            errors.append(f"cannot read the final video duration: {final_video}")
        if end is None:
            errors.append(f"cannot read the last subtitle time: {srt}")
        if duration is not None and end is not None:
            if end > duration + MATCH_TOLERANCE_S or end < duration - MATCH_TOLERANCE_S:
                errors.append(
                    f"subtitles end at {format_seconds(end)} but the final video is "
                    f"{format_seconds(duration)}; the subtitles no longer match the final video "
                    "(a cut was removed or added) — re-export them from the final edit"
                )
            if srt.stat().st_mtime < Path(final_video).stat().st_mtime - MATCH_TOLERANCE_S:
                errors.append(
                    f"subtitles are older than the final video: {srt} was written before "
                    f"{final_video}; re-export them from the final edit"
                )
    if errors:
        return {
            "status": "FAIL",
            "returncode": EXIT_USAGE,
            "outputs": [],
            "llm_cost_usd": 0.0,
            "stderr": "\n".join(f"[error] {error}" for error in errors),
            "stdout": "",
            "wall_time_s": round(time.monotonic() - started, 3),
        }
    return {
        "status": "done",
        "returncode": 0,
        "outputs": [],
        "llm_cost_usd": 0.0,
        "stderr": "",
        "stdout": "",
        "wall_time_s": round(time.monotonic() - started, 3),
    }


def run(ctx):
    """Run the step chain and return the flat summary object."""
    started = time.monotonic()
    state = load_state(ctx.project) or new_state(ctx)
    state["name"] = ctx.name
    state["options"] = build_options(ctx)
    state.setdefault("steps", {})
    steps = build_steps(ctx)
    total = len(steps)
    entries = []
    llm_cost_usd = 0.0
    stopped_at = None
    kit_result = None
    for index, step in enumerate(steps, start=1):
        name = step["name"]
        if name == "review":
            if ctx.resume:
                record_step(state, "review", "passed")
                write_state(ctx.project, state)
                entries.append({"name": "review", "status": "passed", "outputs": []})
                print_progress(ctx, index, total, name, "passed", 0.0, "resumed")
                continue
            record_step(state, "review", "stopped")
            write_state(ctx.project, state)
            entries.append({"name": "review", "status": "stopped", "outputs": []})
            stopped_at = "review"
            print_progress(ctx, index, total, name, "stopped", 0.0, "waiting for review")
            break
        if step.get("skip_reason"):
            record_step(state, name, "skipped", reason=step["skip_reason"])
            write_state(ctx.project, state)
            entries.append({"name": name, "status": "skipped", "outputs": []})
            print_progress(ctx, index, total, name, "skipped", 0.0, step["skip_reason"])
            continue
        if not ctx.force and step_outputs_exist(name, ctx):
            outputs = step_outputs(name, ctx)
            record_step(state, name, "skipped", command=step.get("argv"), outputs=outputs)
            write_state(ctx.project, state)
            entries.append({"name": name, "status": "skipped", "outputs": outputs})
            if name == "kit":
                kit_result = kit_info(ctx, [])
            print_progress(ctx, index, total, name, "skipped", 0.0, "output exists")
            continue
        print_start(ctx, index, total, name)
        if name == "kit":
            result = execute_kit(ctx)
        elif name == "verify":
            result = execute_verify(ctx)
        else:
            result = execute(step, step["argv"], ctx)
        if result["status"] == "FAIL":
            record_step(state, name, "failed", command=step.get("argv"), outputs=[])
            write_state(ctx.project, state)
            entries.append({"name": name, "status": "failed", "outputs": [], "returncode": result["returncode"]})
            if name == "kit":
                kit_result = result.get("kit")
                for line in (result.get("stderr") or "").splitlines():
                    print(line, file=sys.stderr)
            else:
                print_failure(name, result)
            break
        if name == "subtitles":
            normalize_subs_output(ctx)
        llm_cost_usd += result["llm_cost_usd"]
        record_step(state, name, "done", command=step.get("argv"), outputs=result["outputs"])
        write_state(ctx.project, state)
        entries.append({"name": name, "status": "done", "outputs": result["outputs"]})
        if name == "kit":
            kit_result = result.get("kit")
        print_progress(ctx, index, total, name, "done", result["wall_time_s"], "written")
    wall_time_s = round(time.monotonic() - started, 3)
    outputs = {
        "srt_raw": str(ctx.project / f"{ctx.name}.srt"),
        "words_json": str(ctx.project / f"{ctx.name}_words.json"),
        "srt_ro": str(ctx.project / f"{ctx.name}_RO.srt"),
        "srt_en": str(ctx.project / f"{ctx.name}_EN.srt"),
        "thumbnail": str(ctx.project / "thumbnail.jpg") if ctx.photo else None,
        "metadata": (
            str(ctx.project / "metadata.md")
            if (ctx.project / "metadata.md").is_file()
            else None
        ),
    }
    summary = {
        "project": str(ctx.project),
        "name": ctx.name,
        "steps": entries,
        "stopped_at": stopped_at,
        "outputs": outputs,
        "wall_time_s": wall_time_s,
        "llm_cost_usd": round(llm_cost_usd, 8),
    }
    if ctx.kit:
        summary["kit"] = kit_result
    return summary


def print_human(summary):
    if summary["stopped_at"] == "review":
        print(
            f"publish: stopped for review after {summary['wall_time_s']:.1f}s,"
            f" llm cost ${summary['llm_cost_usd']:.4f}"
        )
        print(f"review {summary['outputs']['srt_ro']}, then re-run with --resume")
        return
    failed = next(
        (entry for entry in summary["steps"] if entry["status"] == "failed"), None
    )
    if failed is not None:
        print(
            f"publish: failed at {failed['name']} after {summary['wall_time_s']:.1f}s,"
            f" llm cost ${summary['llm_cost_usd']:.4f}"
        )
        return
    print(
        f"publish: done in {summary['wall_time_s']:.1f}s,"
        f" llm cost ${summary['llm_cost_usd']:.4f}"
    )
    for key in ("srt_ro", "srt_en", "thumbnail", "metadata"):
        if summary["outputs"].get(key):
            print(f"{key}: {summary['outputs'][key]}")
    if summary.get("kit"):
        print(f"kit: {Path(summary['project']) / summary['kit']['output']}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        ctx = build_context(args)
    except PublishError as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    summary = run(ctx)
    if args.json:
        print(json.dumps(summary))
    else:
        print_human(summary)
    failed = [entry for entry in summary["steps"] if entry["status"] == "failed"]
    if not failed:
        return EXIT_OK
    return EXIT_USAGE if failed[0].get("returncode") == EXIT_USAGE else EXIT_RUNTIME


if __name__ == "__main__":
    sys.exit(main())
