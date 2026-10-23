#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read a brief and a workspace and write ``route.json``: which stations run.

The router is the front door of the AI Director pipeline. It parses the plain
language brief into hints, detects the content type from an explicit flag, the
brief keywords or the analysed footage in the workspace, and writes every
station of ``schemas/route.schema.json`` in run order with an enabled flag,
settings and a reason. It is deterministic and calls no model.

Usage:
    python scripts/router.py --brief "about 90 seconds, chronological"
    python scripts/router.py --brief-file brief.txt --workspace DIR --json
    python scripts/router.py --workspace DIR --content-type tutorial --out route.json
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.schema import SchemaError, validate, validate_or_raise

TOOL = "scripts/router.py"
SCHEMA_VERSION = "0.1"
CONTENT_TYPES = ("vacation", "tutorial", "presentation", "talking-head")

STATION_ORDER = (
    "ingest",
    "motion",
    "quality",
    "describe",
    "similarity",
    "audio_analysis",
    "index",
    "director",
    "audio_clean",
    "beats",
    "render",
    "export_kdenlive",
    "graphics",
    "chapters",
    "shorts",
)

MOTION_WEIGHTS = {
    "tutorial": 0.2,
    "presentation": 0.2,
    "talking-head": 0.3,
}
AUDIO_LUFS = {
    "vacation": -16.0,
    "tutorial": -16.0,
    "presentation": -16.0,
    "talking-head": -14.0,
}
GRAPHICS_KITS = {
    "vacation": [],
    "tutorial": ["titles", "steps"],
    "presentation": ["slides", "charts"],
    "talking-head": ["lower_thirds"],
}

TRAVEL_LABELS = {"travel_forward", "travel_backward", "orbit", "orbit_candidate"}
MUSIC_EXTENSIONS = ("mp3", "wav", "m4a", "flac", "ogg")

DURATION_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(seconds|second|secs|sec|minutes|minute|mins|min|s)\b",
    re.IGNORECASE,
)
MINUTE_UNITS = {"minutes", "minute", "mins", "min"}
ORDER_CHRONOLOGICAL = ("chronological", "in order", "as it happened")
ORDER_SCORE = ("by score", "best first", "ranked", "top shots")
UNIFORM_PHRASES = ("uniform", "same length", "even")
AMBIENT_PHRASES = ("ambient sound only", "ambient only", "ambient audio only", "no music")
NO_LLM_PHRASES = ("no llm", "rules only", "without ai")
MUSIC_LABEL_RE = re.compile(r"\b(music|song|soundtrack)\s*:\s*([^,\n]+)", re.IGNORECASE)
MUSIC_TOKEN_RE = re.compile(
    r"[\"']?([^\s\"']+\.(?:" + "|".join(MUSIC_EXTENSIONS) + r"))[\"']?",
    re.IGNORECASE,
)

BRIEF_KEYWORDS = (
    (
        "tutorial",
        ("tutorial", "how to", "how-to", "lesson", "guide", "walkthrough", "explain", "step by step", "steps"),
    ),
    (
        "presentation",
        ("presentation", "slides", "keynote", "conference", "lecture", "talk", "demo", "pitch"),
    ),
    (
        "talking-head",
        (
            "talking head",
            "talking-head",
            "vlog",
            "podcast",
            "interview",
            "monologue",
            "face to camera",
            "face-to-camera",
            "sit down",
        ),
    ),
    (
        "vacation",
        ("trip", "travel", "vacation", "holiday", "recap", "adventure", "hike", "boat", "weekend", "day out", "journey"),
    ),
)


class RouterError(RuntimeError):
    """Raised when the router cannot produce a routing decision."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="router.py",
        description=(
            "Read a brief and a workspace and write route.json: the content type "
            "and which pipeline stations run with which settings. Deterministic, no model."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/router.py --brief \"about 90 seconds, chronological\"\n"
            "  python scripts/router.py --brief-file brief.txt --workspace DIR --json\n"
            "  python scripts/router.py --workspace DIR --content-type tutorial --out route.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--brief", help="the brief in plain language")
    parser.add_argument("--brief-file", help="path to a file holding the brief")
    parser.add_argument("--workspace", help="workspace used for content-type detection and default output")
    parser.add_argument("--content-type", choices=CONTENT_TYPES, help="force the content type")
    parser.add_argument("--out", help="output path (default WORKSPACE/route.json or ./route.json)")
    parser.add_argument("--force", action="store_true", help="rewrite even when the output is up to date")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def _keyword_re(keyword):
    return re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)


def _first_phrase(text, phrases):
    for phrase in phrases:
        if _keyword_re(phrase).search(text):
            return phrase
    return None


def parse_duration(text):
    match = DURATION_RE.search(text)
    if not match:
        return None, None
    value = float(match.group(1))
    seconds = value * 60 if match.group(2).lower() in MINUTE_UNITS else value
    return seconds, match.group(0)


def parse_order(text):
    phrase = _first_phrase(text, ORDER_CHRONOLOGICAL)
    if phrase:
        return "chronological", phrase
    phrase = _first_phrase(text, ORDER_SCORE)
    if phrase:
        return "score", phrase
    return None, None


def parse_pacing(text):
    has_calm = bool(_keyword_re("calm").search(text))
    has_energy = bool(_keyword_re("energy").search(text))
    if has_calm and has_energy:
        return "calm-to-energy", ["calm", "energy"]
    if _keyword_re("slow start").search(text):
        return "calm-to-energy", ["slow start"]
    if _keyword_re("build").search(text):
        return "calm-to-energy", ["build"]
    phrase = _first_phrase(text, UNIFORM_PHRASES)
    if phrase:
        return "uniform", [phrase]
    return None, []


def parse_music(text):
    match = MUSIC_LABEL_RE.search(text)
    if match:
        value = match.group(2).strip().strip("\"'").strip()
        if value:
            return value, match.group(0).strip()
    match = MUSIC_TOKEN_RE.search(text)
    if match:
        return match.group(1), match.group(1)
    return None, None


def parse_ambient_only(text):
    phrase = _first_phrase(text, AMBIENT_PHRASES)
    return (phrase is not None), phrase


def parse_rules_only(text):
    phrase = _first_phrase(text, NO_LLM_PHRASES)
    return (phrase is not None), phrase


def parse_brief(text):
    """Parse the raw brief into the ``brief`` object and whether rules-only was asked."""
    signals = []
    duration, phrase = parse_duration(text)
    if phrase:
        signals.append(phrase)
    order, phrase = parse_order(text)
    if phrase:
        signals.append(phrase)
    pacing, phrases = parse_pacing(text)
    signals.extend(phrases)
    music_file, phrase = parse_music(text)
    if phrase:
        signals.append(phrase)
    ambient_only, phrase = parse_ambient_only(text)
    if phrase:
        signals.append(phrase)
    rules_only, phrase = parse_rules_only(text)
    if phrase:
        signals.append(phrase)
    brief = {
        "text": text,
        "target_duration_s": duration,
        "order": order,
        "pacing": pacing,
        "music_file": music_file,
        "ambient_only": ambient_only,
        "signals": signals,
    }
    return brief, rules_only


def match_brief_keyword(text):
    for content_type, keywords in BRIEF_KEYWORDS:
        phrase = _first_phrase(text, keywords)
        if phrase:
            return content_type, phrase
    return None, None


def has_transcript(workspace):
    if list(workspace.glob("*.srt")):
        return True
    return any(path.is_file() for path in workspace.glob("*transcript*"))


def load_json(path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def detect_from_workspace(workspace):
    """Return a content type from the analysed footage, or ``None``."""
    data = load_json(workspace / "shotlog.json")
    if not isinstance(data, dict):
        return None
    shots = data.get("shots") or []
    if not shots:
        return None
    travel = sum(
        1
        for shot in shots
        if shot.get("motion_label") in TRAVEL_LABELS or shot.get("semantic_motion") == "travel"
    )
    if travel * 2 >= len(shots):
        return "vacation"
    holds = sum(1 for shot in shots if shot.get("role") == "hold")
    if has_transcript(workspace) and holds * 2 > len(shots):
        return "talking-head"
    return None


def detect_content_type(text, flag, workspace):
    if flag:
        return flag, "flag", None
    content_type, phrase = match_brief_keyword(text)
    if content_type:
        return content_type, "brief", phrase
    if workspace is not None and workspace.is_dir():
        detected = detect_from_workspace(workspace)
        if detected:
            return detected, "workspace", None
    return "vacation", "default", None


def director_settings(rules_only):
    if rules_only:
        return {"mode": "rules", "fallback": None, "max_rounds": 0}
    return {"mode": "llm", "fallback": "rules", "max_rounds": 3}


def motion_settings(content_type):
    settings = {"sample_fps": 10}
    if content_type in MOTION_WEIGHTS:
        settings["weight"] = MOTION_WEIGHTS[content_type]
    return settings


def build_stations(content_type, brief, rules_only):
    music_file = brief["music_file"]
    ambient_only = brief["ambient_only"]
    beats_enabled = content_type in ("vacation", "talking-head") and music_file is not None and not ambient_only
    beats_required_when = (
        "a music file in the brief" if content_type == "vacation" and music_file is None else None
    )

    reasons = {
        "ingest": "every project starts with a folder scan",
        "quality": "defects and roles decide what is usable",
        "describe": "content score drives the selection",
        "similarity": "the director refuses near duplicates",
        "index": "one shotlog per project",
        "director": "the brief asks for a story, not a clip list",
        "render": "a playable recap is the deliverable",
        "export_kdenlive": "optional export stays one command away",
    }
    motion_reasons = {
        "vacation": "action footage needs camera moves",
        "tutorial": "a light motion signal for a how-to",
        "presentation": "a light motion signal for a talk",
        "talking-head": "head and camera movement still matter",
    }
    audio_analysis_reasons = {
        "vacation": "ambient sound is part of the shot on action footage",
        "tutorial": "the voice carries the how-to",
        "presentation": "speech carries the talk",
        "talking-head": "speech and room tone are the whole story",
    }
    audio_clean_reasons = {
        "vacation": "wind and noise on the water",
        "tutorial": "clean up room tone in the voice",
        "presentation": "level the room for clear speech",
        "talking-head": "a steady voice level for the whole talk",
    }
    graphics_reasons = {
        "vacation": "no titles or maps asked for",
        "tutorial": "titles and step callouts carry the how-to",
        "presentation": "slides and charts carry the talk",
        "talking-head": "lower thirds name the speaker",
    }
    chapters_reasons = {
        "vacation": "chapters belong to tutorials and talks",
        "tutorial": "chapters help navigate a long how-to",
        "presentation": "chapters help navigate a long talk",
        "talking-head": "chapters help navigate a long talk",
    }
    shorts_reasons = {
        "vacation": "shorts are their own deliverable",
        "tutorial": "the best steps become vertical shorts",
        "presentation": "the best moments become vertical shorts",
        "talking-head": "the best moments become vertical shorts",
    }
    beats_reasons = {
        "vacation": "beat sync and music for a trip recap",
        "talking-head": "music is allowed under a talking-head video",
    }

    if beats_enabled:
        beats_reason = beats_reasons[content_type]
    elif ambient_only:
        beats_reason = "the brief asks for ambient sound only, no music"
    elif content_type == "vacation":
        beats_reason = "no music named, beats wait for a track"
    elif content_type == "talking-head":
        beats_reason = "no music named, beats stay off"
    else:
        beats_reason = f"beats do not fit a {content_type}"

    settings = {
        "ingest": {},
        "motion": motion_settings(content_type),
        "quality": {},
        "describe": {},
        "similarity": {"threshold": 0.9},
        "audio_analysis": {},
        "index": {},
        "director": director_settings(rules_only),
        "audio_clean": {"target_lufs": AUDIO_LUFS[content_type]},
        "beats": {"music_file": music_file, "align": True} if beats_enabled else {},
        "render": {"preview_height": 720, "final_from_originals": True},
        "export_kdenlive": {},
        "graphics": {"kits": list(GRAPHICS_KITS[content_type])},
        "chapters": {},
        "shorts": {},
    }

    enabled = {
        "ingest": True,
        "motion": True,
        "quality": True,
        "describe": True,
        "similarity": True,
        "audio_analysis": True,
        "index": True,
        "director": True,
        "audio_clean": True,
        "beats": beats_enabled,
        "render": True,
        "export_kdenlive": True,
        "graphics": content_type != "vacation",
        "chapters": content_type != "vacation",
        "shorts": content_type != "vacation",
    }

    reasons["motion"] = motion_reasons[content_type]
    reasons["audio_analysis"] = audio_analysis_reasons[content_type]
    reasons["audio_clean"] = audio_clean_reasons[content_type]
    reasons["beats"] = beats_reason
    reasons["graphics"] = graphics_reasons[content_type]
    reasons["chapters"] = chapters_reasons[content_type]
    reasons["shorts"] = shorts_reasons[content_type]

    rows = []
    for name in STATION_ORDER:
        row = {
            "name": name,
            "enabled": bool(enabled[name]),
            "settings": settings[name],
            "reason": reasons[name],
        }
        if name == "beats" and beats_required_when is not None:
            row["required_when"] = beats_required_when
        rows.append(row)
    return rows


def atomic_write_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def input_paths(brief_path, workspace):
    paths = []
    if brief_path is not None:
        paths.append(brief_path)
    if workspace is not None:
        shotlog = workspace / "shotlog.json"
        if shotlog.is_file():
            paths.append(shotlog)
    return paths


def is_up_to_date(out_path, brief_path, workspace, brief_text, content_type_flag):
    if not out_path.is_file():
        return False
    data = load_json(out_path)
    if not isinstance(data, dict) or validate(data, "route"):
        return False
    if (data.get("brief") or {}).get("text") != brief_text:
        return False
    if content_type_flag is not None and (
        data.get("content_type") != content_type_flag or data.get("content_type_source") != "flag"
    ):
        return False
    out_mtime = out_path.stat().st_mtime
    for path in input_paths(brief_path, workspace):
        if path.is_file() and path.stat().st_mtime > out_mtime:
            return False
    return True


def existing_route(out_path):
    data = load_json(out_path)
    return data if isinstance(data, dict) else {}


def build_record(content_type, source, brief, stations, warnings, wall_time_s):
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": TOOL,
        "brief": brief,
        "content_type": content_type,
        "content_type_source": source,
        "stations": stations,
        "meta": {"tool": TOOL, "wall_time_s": round(wall_time_s, 3), "llm_cost_usd": 0.0},
        "warnings": warnings,
    }


def summary_from_record(record, out_path, status, wall_time_s):
    return {
        "status": status,
        "out": str(out_path),
        "content_type": record.get("content_type"),
        "content_type_source": record.get("content_type_source"),
        "stations_enabled": [row["name"] for row in record.get("stations", []) if row.get("enabled")],
        "target_duration_s": (record.get("brief") or {}).get("target_duration_s"),
        "music_file": (record.get("brief") or {}).get("music_file"),
        "warnings": list(record.get("warnings") or []),
        "wall_time_s": round(wall_time_s, 3),
        "llm_cost_usd": 0.0,
    }


def run(args):
    started = time.monotonic()
    if args.brief is None and args.brief_file is None:
        raise RouterError("provide --brief or --brief-file")
    if args.brief is not None and args.brief_file is not None:
        raise RouterError("give either --brief or --brief-file, not both")

    if args.brief_file is not None:
        brief_path = Path(args.brief_file).expanduser().resolve()
        if not brief_path.is_file():
            raise RouterError(f"brief file not found: {brief_path}")
        text = brief_path.read_text(encoding="utf-8")
    else:
        brief_path = None
        text = args.brief

    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else None
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
    elif workspace is not None:
        out_path = workspace / "route.json"
    else:
        out_path = Path.cwd() / "route.json"

    if not args.force and is_up_to_date(out_path, brief_path, workspace, text, args.content_type):
        return summary_from_record(existing_route(out_path), out_path, "skipped", time.monotonic() - started)

    brief, rules_only = parse_brief(text)
    content_type, source, keyword = detect_content_type(text, args.content_type, workspace)
    if keyword:
        brief["signals"].append(keyword)

    warnings = []
    if workspace is not None and not workspace.is_dir():
        warnings.append(f"workspace folder does not exist: {workspace}")
    if brief["music_file"] and not Path(brief["music_file"]).expanduser().exists():
        warnings.append(f"music file not found: {brief['music_file']}")
    if not brief["signals"]:
        warnings.append("brief has no parseable hints")

    stations = build_stations(content_type, brief, rules_only)
    wall_time_s = time.monotonic() - started
    record = build_record(content_type, source, brief, stations, warnings, wall_time_s)
    validate_or_raise(record, "route")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out_path, record)
    return summary_from_record(record, out_path, "written", wall_time_s)


def print_human(summary):
    enabled = ", ".join(summary["stations_enabled"])
    print(f"{summary['content_type']} ({summary['content_type_source']}): {enabled}")
    print(f"wrote {summary['out']}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = run(args)
    except (RouterError, SchemaError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_USAGE
    except OSError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    if args.json:
        print(json.dumps(summary))
    else:
        print_human(summary)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
