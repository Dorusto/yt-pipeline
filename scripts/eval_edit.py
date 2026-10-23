#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check a workspace edit.json against a YAML of human edit expectations.

Usage:
    python scripts/eval_edit.py --workspace WS --expect eval/kotor-edit.yaml
    python scripts/eval_edit.py --workspace WS --expect eval/kotor-edit.yaml --json
    python scripts/eval_edit.py --workspace WS --expect eval/kotor-edit.yaml --out report.json

The expectations YAML holds what a human requires of an edit: ``must_include``
items (each an ``any_of`` list of shot keys, at least one of which must appear
on a slot) and a ``length_s`` block (``target`` and ``tolerance`` for the
total on-screen length). Every expectation is checked against
``WS/edit.json`` and reported PASS/FAIL in YAML order; the exit code is 0 only
when every expectation passes. The on-screen length of a video slot is
``0.25*L + 0.5*L/speed + 0.25*L`` for a source window of ``L`` seconds whose
middle half plays at ``speed`` (the same math the director and the renderer
use, recomputed here so this tool stays standalone). The tool reads the
workspace and the YAML and writes nothing except ``--out``; it calls no model.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from common.exit_codes import EXIT_ATTENTION, EXIT_OK, EXIT_RUNTIME, EXIT_USAGE


class EvalError(RuntimeError):
    """Raised when the inputs cannot be read or parsed."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="eval_edit.py",
        description=(
            "Check a workspace edit.json against a YAML of human expectations: "
            "required shot keys (must_include) and the total on-screen length "
            "(length_s). Prints PASS/FAIL per expectation and exits 1 on any fail."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/eval_edit.py --workspace WS --expect eval/kotor-edit.yaml\n"
            "  python scripts/eval_edit.py --workspace WS --expect eval/kotor-edit.yaml --json\n"
            "  python scripts/eval_edit.py --workspace WS --expect eval/kotor-edit.yaml --out report.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--workspace", required=True, help="workspace holding edit.json")
    parser.add_argument("--expect", required=True, help="expectations YAML to check against")
    parser.add_argument("--json", action="store_true", help="print the machine-readable report object instead of the table")
    parser.add_argument("--out", default=None, help="also write the report object to FILE, atomically")
    return parser


def is_photo_slot(slot):
    return slot.get("kind") == "photo" or slot.get("media_type") == "photo"


def screen_length(source_length_s, middle_speed=1.0):
    """On-screen length of a source window whose middle half plays at ``middle_speed``."""
    length = max(0.0, float(source_length_s))
    return 0.25 * length + 0.5 * length / max(1.0, float(middle_speed)) + 0.25 * length


def slot_screen_length(slot):
    """The on-screen length of one edit slot: ``duration_s`` for photos, the ramp-aware length for video."""
    if is_photo_slot(slot):
        return float(slot.get("duration_s") or 0.0)
    return screen_length(float(slot.get("out_s", 0.0)) - float(slot.get("in_s", 0.0)), slot.get("middle_speed") or 1.0)


def slot_key(slot):
    """The shot key a slot is known by in the shotlog, or None when it has none."""
    if slot.get("shot_key"):
        return str(slot["shot_key"])
    if is_photo_slot(slot):
        if slot.get("id"):
            return str(slot["id"])
        photo = slot.get("photo")
        if photo:
            return f"{Path(str(photo)).stem}/p1"
    return None


def edit_keys(slots):
    """The set of shot keys actually present in the edit."""
    return {key for key in (slot_key(slot) for slot in slots) if key}


def load_edit(path):
    if not path.is_file():
        raise EvalError(f"edit.json not found: {path}")
    try:
        edit = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalError(f"cannot read edit {path}: {exc}") from exc
    if not isinstance(edit, dict) or not isinstance(edit.get("video"), list):
        raise EvalError(f"edit has no video list: {path}")
    return edit


def as_number(value):
    """The value as int or float, keeping the YAML's own numeric type."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(value)
    return value


def normalize_expectations(data, path):
    must_include_raw = data.get("must_include") or []
    if not isinstance(must_include_raw, list):
        raise EvalError(f"must_include must be a list: {path}")
    must_include = []
    for item in must_include_raw:
        if isinstance(item, str):
            must_include.append({"any_of": [item], "note": ""})
        elif isinstance(item, dict) and isinstance(item.get("any_of"), list):
            must_include.append(
                {
                    "any_of": [str(key) for key in item["any_of"]],
                    "note": str(item.get("note") or ""),
                }
            )
        else:
            raise EvalError(f"must_include item must be a key string or a mapping with any_of: {path}")
    expectations = {"must_include": must_include}
    length_spec = data.get("length_s")
    if length_spec is not None:
        if not isinstance(length_spec, dict) or "target" not in length_spec:
            raise EvalError(f"length_s needs a target: {path}")
        try:
            expectations["length_s"] = {
                "target": as_number(length_spec["target"]),
                "tolerance": as_number(length_spec.get("tolerance") or 0.0),
                "note": str(length_spec.get("note") or ""),
            }
        except (TypeError, ValueError) as exc:
            raise EvalError(f"length_s target and tolerance must be numbers: {path}") from exc
    return expectations


def load_expectations(path):
    if not path.is_file():
        raise EvalError(f"expectations not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvalError(f"cannot read expectations {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise EvalError(f"cannot parse expectations {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise EvalError(f"expectations must be a mapping: {path}")
    return normalize_expectations(data, path)


def evaluate(edit, expectations, workspace, expect_path, edit_path, generated_at=None):
    """Check every expectation against the edit and build the report object."""
    slots = edit.get("video") or []
    keys = edit_keys(slots)
    checks = []
    for item in expectations["must_include"]:
        found = [key for key in item["any_of"] if key in keys]
        checks.append(
            {
                "type": "must_include",
                "any_of": item["any_of"],
                "found": found,
                "passed": bool(found),
                "note": item["note"],
            }
        )
    length_spec = expectations.get("length_s")
    if length_spec is not None:
        total = round(sum(slot_screen_length(slot) for slot in slots), 2)
        lower = length_spec["target"] - length_spec["tolerance"]
        upper = length_spec["target"] + length_spec["tolerance"]
        checks.append(
            {
                "type": "length_s",
                "target": length_spec["target"],
                "tolerance": length_spec["tolerance"],
                "got": total,
                "passed": lower <= total <= upper,
                "note": length_spec["note"],
            }
        )
    return {
        "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tool": "scripts/eval_edit.py",
        "workspace": str(workspace),
        "expect": str(expect_path),
        "edit": str(edit_path),
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
        "wall_time_s": 0.0,
        "llm_cost_usd": 0.0,
    }


def check_label(check):
    if check["type"] == "must_include":
        return "must_include " + "|".join(check["any_of"])
    return f"length_s {check['target']:g}+-{check['tolerance']:g} (got {check['got']:.2f})"


def format_table(report):
    """Render the report as a fixed-width table, one row per expectation, plus a summary line."""
    lines = [f"{'expectation':<36}{'result':<8}note"]
    for check in report["checks"]:
        result = "PASS" if check["passed"] else "FAIL"
        lines.append(f"{check_label(check):<36}{result:<8}{check['note']}".rstrip())
    passed = sum(1 for check in report["checks"] if check["passed"])
    failed = len(report["checks"]) - passed
    lines.append(f"{passed} passed, {failed} failed")
    return "\n".join(lines)


def atomic_write_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv=None):
    started = time.monotonic()
    args = build_parser().parse_args(argv)
    try:
        workspace = Path(args.workspace).expanduser()
        if not workspace.is_dir():
            raise EvalError(f"workspace not found: {workspace}")
        edit_path = workspace / "edit.json"
        edit = load_edit(edit_path)
        expect_path = Path(args.expect).expanduser()
        expectations = load_expectations(expect_path)
        report = evaluate(edit, expectations, workspace, expect_path, edit_path)
        report["wall_time_s"] = round(time.monotonic() - started, 2)
    except EvalError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.out:
        try:
            atomic_write_json(Path(args.out).expanduser(), report)
        except OSError as exc:
            print(f"[error] cannot write {args.out}: {exc}", file=sys.stderr)
            return EXIT_RUNTIME
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_table(report))
    return EXIT_OK if report["passed"] else EXIT_ATTENTION


if __name__ == "__main__":
    sys.exit(main())
