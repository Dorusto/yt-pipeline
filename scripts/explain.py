#!/usr/bin/env python3
"""Write ``explain.html`` in a workspace: why the edit looks the way it does.

Usage:
    python scripts/explain.py --workspace DIR
    python scripts/explain.py --workspace DIR --json
    python scripts/explain.py --workspace DIR --out /tmp/explain.html --force

Reads ``edit.json`` (required, validated against ``schemas/edit.schema.json``)
plus ``shot_plan.json`` and ``shotlog.json`` (optional) and writes one
self-contained ``explain.html``: the brief, the screenwriter plan in words, the
timeline with a base64 thumbnail per slot (the cut versus the candidate window
and the slot's reason) and every candidate used or rejected with the reason,
from ``decisions[]``. No model is called. Sources are read-only; the HTML is
written atomically and skipped when it is newer than its inputs, unless
``--force``.
"""

import argparse
import base64
import json
import os
import sys
import time
from html import escape
from pathlib import Path

import cv2

from common.schema import SchemaError, validate

HTML_NAME = "explain.html"
THUMBNAIL_WIDTH = 320
THUMBNAIL_QUALITY = 70


class ExplainError(RuntimeError):
    """Raised when a workspace cannot be explained."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="explain.py",
        description=(
            "Write explain.html in a workspace: the brief, the screenwriter plan "
            "in words, the edit timeline with a thumbnail per slot and every "
            "candidate used or rejected with the reason, from edit.json."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/explain.py --workspace ~/Videos/2026-04-30/workspace\n"
            "  python scripts/explain.py --workspace ~/Videos/2026-04-30/workspace --json\n"
            "  python scripts/explain.py --workspace ~/Videos/2026-04-30/workspace \\\n"
            "      --out /tmp/explain.html --force\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workspace",
        required=True,
        help="workspace holding edit.json; explain.html is written here by default",
    )
    parser.add_argument("--out", default=None, help="output HTML path (default WORKSPACE/explain.html)")
    parser.add_argument("--force", action="store_true", help="rewrite even when the output is up to date")
    parser.add_argument(
        "--json",
        action="store_true",
        help="print one JSON summary on stdout; human output goes to stderr",
    )
    return parser


def run(workspace, out=None, force=False):
    """Write the workspace ``explain.html``; return the summary and the exit code."""
    started = time.monotonic()
    workspace_path = Path(workspace).expanduser().resolve()
    if not workspace_path.is_dir():
        raise ExplainError(f"workspace is not a directory: {workspace_path}")
    edit_path = workspace_path / "edit.json"
    edit = _load_edit(edit_path)
    out_path = _out_path(workspace_path, out)
    inputs = _input_files(workspace_path, edit_path)
    if not force and _output_is_up_to_date(out_path, inputs):
        return _summary("skipped", edit, out_path, started), 0
    _check_out_location(out_path, workspace_path, edit.get("sources") or [])
    plan = _load_optional(workspace_path / "shot_plan.json", "shot_plan")
    shotlog = _load_optional(workspace_path / "shotlog.json", "shotlog")
    document = _html_document(workspace_path, edit, plan, shotlog)
    _write_html(out_path, document)
    return _summary("written", edit, out_path, started), 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary, exit_code = run(args.workspace, out=args.out, force=args.force)
    except (ExplainError, SchemaError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    line = (
        f"{summary['status']}: {summary['out']} "
        f"({summary['slots']} slots, {summary['candidates']} candidates)"
    )
    if args.json:
        print(line, file=sys.stderr)
        print(json.dumps(summary))
    elif summary["status"] == "skipped":
        print(f"up to date: {summary['out']}")
    else:
        print(
            f"wrote {summary['out']} "
            f"({summary['slots']} slots, {summary['candidates']} candidates, {summary['wall_time_s']}s)"
        )
    return exit_code


def _load_edit(path):
    if not path.is_file():
        raise ExplainError(f"no edit.json in workspace: {path}")
    data = _load_json(path)
    if data is None:
        raise ExplainError(f"unreadable edit.json: {path}")
    errors = validate(data, "edit")
    if errors:
        raise SchemaError("edit", errors)
    return data


def _load_optional(path, schema_name):
    if not path.exists():
        return None
    data = _load_json(path)
    if data is None:
        raise ExplainError(f"unreadable {path.name}: {path}")
    errors = validate(data, schema_name)
    if errors:
        raise SchemaError(schema_name, errors)
    return data


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _out_path(workspace_path, out):
    if out:
        return Path(out).expanduser().resolve()
    return workspace_path / HTML_NAME


def _input_files(workspace_path, edit_path):
    inputs = [edit_path]
    for name in ("shot_plan.json", "shotlog.json"):
        path = workspace_path / name
        if path.exists():
            inputs.append(path)
    return inputs


def _output_is_up_to_date(output_path, inputs):
    if not output_path.exists():
        return False
    try:
        output_mtime = output_path.stat().st_mtime
        newest_input = max(path.stat().st_mtime for path in inputs)
    except (OSError, ValueError):
        return False
    return output_mtime >= newest_input


def _check_out_location(out_path, workspace_path, sources):
    for source in sources:
        raw = str(source.get("path") or "")
        if not raw:
            continue
        source_path = Path(raw).expanduser().resolve()
        if source_path == out_path:
            raise ExplainError(f"--out must not overwrite a source file: {out_path}")
        folder = source_path.parent
        inside_sources_folder = out_path == folder or folder in out_path.parents
        folder_is_workspace_or_above = folder == workspace_path or folder in workspace_path.parents
        if inside_sources_folder and not folder_is_workspace_or_above:
            raise ExplainError(f"--out must not be inside the sources folder: {out_path}")


def _summary(status, edit, out_path, started):
    decisions = edit.get("decisions") or []
    used = sum(1 for row in decisions if row.get("status") == "used")
    return {
        "status": status,
        "out": str(out_path),
        "slots": len(edit.get("video") or []),
        "candidates": len(decisions),
        "used": used,
        "rejected": len(decisions) - used,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": 0.0,
    }


def _html_document(workspace_path, edit, plan, shotlog):
    slots = edit.get("video") or []
    decisions = _sorted_decisions(edit.get("decisions") or [])
    used_by_slot, by_id = _decision_maps(decisions)
    shotlog_by_key = _shotlog_windows(shotlog)
    sources = _source_paths(edit.get("sources") or [])
    used = sum(1 for row in decisions if row.get("status") == "used")
    sections = [
        _brief_section(edit.get("brief") or {}, edit.get("audio") or {}),
        _plan_section(plan),
        _timeline_section(slots, sources, used_by_slot, by_id, shotlog_by_key),
        _candidates_section(decisions),
    ]
    return _page(
        workspace_path.name,
        len(slots),
        len(decisions),
        used,
        len(decisions) - used,
        "\n".join(sections),
    )


def _page(workspace_name, slot_count, candidate_count, used_count, rejected_count, sections):
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>explain · {escape(workspace_name)}</title>\n"
        "<style>\n"
        ":root { color-scheme: dark; }\n"
        "* { box-sizing: border-box; }\n"
        "body { margin: 0; padding: 12px; font-family: system-ui, -apple-system, sans-serif; "
        "background: #111; color: #eee; }\n"
        "header { margin-bottom: 12px; }\n"
        "h1 { font-size: 1.2rem; margin: 0 0 4px; }\n"
        "header p { margin: 0; color: #aaa; font-size: 0.85rem; }\n"
        "main { display: flex; flex-direction: column; gap: 12px; max-width: 760px; }\n"
        "section { background: #1c1c1e; border-radius: 12px; padding: 10px 12px 14px; }\n"
        "h2 { font-size: 1rem; margin: 0 0 8px; }\n"
        ".brief { margin: 0 0 8px; padding: 8px 10px; background: #26262a; "
        "border-left: 3px solid #4a6fa5; border-radius: 4px; font-size: 0.95rem; }\n"
        ".meta-line { font-size: 0.8rem; color: #99a; margin: 2px 0; }\n"
        ".missing { color: #99a; font-style: italic; }\n"
        "ul { margin: 0 0 8px; padding-left: 20px; }\n"
        "li { margin: 2px 0; font-size: 0.9rem; }\n"
        ".slot { display: flex; gap: 10px; padding: 10px 0; border-top: 1px solid #2c2c2e; }\n"
        ".slot:first-of-type { border-top: none; padding-top: 4px; }\n"
        ".thumb { width: 320px; max-width: 45%; aspect-ratio: 16 / 9; object-fit: cover; "
        "border-radius: 6px; background: #333; flex-shrink: 0; }\n"
        ".thumb.placeholder { display: flex; align-items: center; justify-content: center; "
        "color: #888; font-size: 0.72rem; text-align: center; padding: 4px; overflow-wrap: anywhere; }\n"
        ".body { min-width: 0; }\n"
        ".slot-id { font-weight: 700; }\n"
        ".short-id { color: #f5c518; font-weight: 600; }\n"
        ".cut { font-size: 0.85rem; color: #bcc; margin: 4px 0; }\n"
        ".reason { font-size: 0.85rem; color: #ccc; margin: 4px 0 0; }\n"
        ".candidate { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; "
        "padding: 7px 0; border-top: 1px solid #2c2c2e; font-size: 0.85rem; }\n"
        ".candidate:first-of-type { border-top: none; }\n"
        ".candidate.rejected { opacity: 0.6; }\n"
        ".cand-id { font-weight: 700; }\n"
        ".cand-meta { color: #99a; font-size: 0.78rem; }\n"
        ".cand-reason { color: #ccc; flex-basis: 100%; }\n"
        ".badge { border-radius: 999px; padding: 1px 8px; font-size: 0.72rem; font-weight: 600; }\n"
        ".badge.used { background: #1f6f43; color: #d7ffe7; }\n"
        ".badge.rejected { background: #5a1f1f; color: #ffd7d7; }\n"
        "@media (max-width: 560px) { .slot { flex-direction: column; } "
        ".thumb { width: 100%; max-width: none; } }\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        "<header>\n"
        f"<h1>explain · {escape(workspace_name)}</h1>\n"
        f"<p>{slot_count} slots · {candidate_count} candidates "
        f"({used_count} used, {rejected_count} rejected)</p>\n"
        "</header>\n"
        "<main>\n"
        f"{sections}\n"
        "</main>\n"
        "</body>\n"
        "</html>\n"
    )


def _brief_section(brief, audio):
    lines = ['<section id="brief">', "<h2>Brief</h2>"]
    text = escape(str(brief.get("text") or ""))
    lines.append(f'<blockquote class="brief">{text}</blockquote>')
    meta_parts = []
    target = brief.get("target_duration_s")
    if _is_number(target):
        meta_parts.append(f"target {float(target):.2f} s")
    if brief.get("order"):
        meta_parts.append(f"order {brief['order']}")
    if brief.get("pacing"):
        meta_parts.append(f"pacing {brief['pacing']}")
    music = audio.get("music")
    if isinstance(music, dict) and music.get("path"):
        meta_parts.append(f"music {music['path']}")
    ambient = audio.get("ambient_under_music_db")
    if _is_number(ambient):
        meta_parts.append(f"ambient {float(ambient):.2f} dB under the music")
    if meta_parts:
        joined = escape(" · ".join(meta_parts))
        lines.append(f'<p class="meta-line">{joined}</p>')
    lines.append("</section>")
    return "\n".join(lines)


def _plan_section(plan):
    lines = ['<section id="plan">', "<h2>The plan in words</h2>"]
    if not isinstance(plan, dict):
        lines.append(
            '<p class="missing">No shot plan (shot_plan.json) in this workspace — '
            "the edit was not planned by the LLM director.</p>"
        )
        lines.append("</section>")
        return "\n".join(lines)
    logline = escape(str(plan.get("logline") or ""))
    lines.append(f'<blockquote class="brief">{logline}</blockquote>')
    beats = plan.get("structure") or []
    if beats:
        items = []
        for beat in beats:
            name = escape(str(beat.get("name") or ""))
            intent = escape(str(beat.get("intent") or ""))
            target = _seconds_text(beat.get("target_s"))
            items.append(f"<li><strong>{name}</strong> — {intent} <span class=\"cand-meta\">{target}</span></li>")
        lines.append("<ul>\n" + "\n".join(items) + "\n</ul>")
    rows = plan.get("slot_plan") or []
    if rows:
        items = []
        for row in rows:
            ident = escape(str(row.get("id") or ""))
            key = escape(str(row.get("shot_key") or ""))
            target = _seconds_text(row.get("target_s"))
            why = escape(str(row.get("why") or ""))
            items.append(
                f'<div class="candidate"><span class="cand-id">{ident}</span>'
                f'<span class="cand-meta">{key} · {target}</span>'
                f'<span class="cand-reason">{why}</span></div>'
            )
        lines.append("\n".join(items))
    lines.append("</section>")
    return "\n".join(lines)


def _timeline_section(slots, sources, used_by_slot, by_id, shotlog_by_key):
    rows = "\n".join(
        _timeline_row(slot, sources, used_by_slot, by_id, shotlog_by_key) for slot in slots
    )
    return f'<section id="timeline">\n<h2>Timeline</h2>\n{rows}\n</section>'


def _timeline_row(slot, sources, used_by_slot, by_id, shotlog_by_key):
    is_photo = str(slot.get("kind") or "") == "photo"
    source_name = str(slot.get("photo") or "") if is_photo else str(slot.get("clip") or "")
    path = sources.get(source_name)
    window = _window_for_slot(slot, used_by_slot, by_id, shotlog_by_key)
    if is_photo:
        duration = float(slot.get("duration_s") or 0.0)
        cut_text = f"photo {duration:.2f} s"
        move = (slot.get("ken_burns") or {}).get("move")
        move_html = f'<div class="meta-line">ken_burns {escape(str(move))}</div>' if move else ""
        data_uri = _thumbnail_data_uri(path, 0.0)
    else:
        in_s = float(slot.get("in_s") or 0.0)
        out_s = float(slot.get("out_s") or 0.0)
        cut_text = f"cut {in_s:.2f}–{out_s:.2f} s"
        move_html = ""
        data_uri = _thumbnail_data_uri(path, (in_s + out_s) / 2.0)
    if window is not None:
        cut_text += f" of {window[0]:.2f}–{window[1]:.2f} s"
    if data_uri:
        thumb = f'<img class="thumb" src="{data_uri}" alt="{escape(source_name)}">'
    else:
        thumb = f'<div class="thumb placeholder">{escape(source_name)}</div>'
    slot_id = escape(str(slot.get("id") or ""))
    shot_key = slot.get("shot_key")
    key_html = f' <span class="short-id">· {escape(str(shot_key))}</span>' if shot_key else ""
    reason = escape(str(slot.get("reason") or ""))
    return (
        f'<div class="slot">'
        f"{thumb}"
        f'<div class="body">'
        f'<div class="slot-id">{slot_id}{key_html}</div>'
        f'<div class="meta-line">{escape(source_name)}</div>'
        f'<div class="cut">{escape(cut_text)}</div>'
        f"{move_html}"
        f'<p class="reason">{reason}</p>'
        f"</div>"
        f"</div>"
    )


def _candidates_section(decisions):
    rows = "\n".join(_candidate_row(row) for row in decisions)
    if not rows:
        rows = '<p class="missing">No decisions recorded in edit.json.</p>'
    return f'<section id="candidates">\n<h2>Every candidate</h2>\n{rows}\n</section>'


def _candidate_row(row):
    status = str(row.get("status") or "")
    ident = escape(str(row.get("id") or ""))
    if status == "used":
        row_class = "used"
        badge = f'<span class="badge used">used → {escape(str(row.get("slot") or ""))}</span>'
    else:
        row_class = "rejected"
        badge = '<span class="badge rejected">rejected</span>'
    meta_parts = []
    if _is_number(row.get("score")):
        meta_parts.append(f"{float(row['score']):.2f}")
    if row.get("clip"):
        meta_parts.append(str(row["clip"]))
    meta_html = f'<span class="cand-meta">{escape(" · ".join(meta_parts))}</span>' if meta_parts else ""
    reason = escape(str(row.get("reason") or ""))
    return (
        f'<div class="candidate {row_class}">'
        f'<span class="cand-id">{ident}</span>'
        f"{badge}"
        f"{meta_html}"
        f'<span class="cand-reason">{reason}</span>'
        f"</div>"
    )


def _sorted_decisions(decisions):
    return sorted(decisions, key=lambda row: str(row.get("id") or ""))


def _decision_maps(decisions):
    used_by_slot = {}
    by_id = {}
    for row in decisions:
        by_id[str(row.get("id") or "")] = row
        if row.get("status") == "used" and row.get("slot"):
            used_by_slot[str(row["slot"])] = row
    return used_by_slot, by_id


def _shotlog_windows(shotlog):
    windows = {}
    for shot in (shotlog or {}).get("shots") or []:
        key = shot.get("key")
        start = shot.get("start_s")
        end = shot.get("end_s")
        if isinstance(key, str) and _is_number(start) and _is_number(end):
            windows[key] = (float(start), float(end))
    return windows


def _source_paths(sources):
    paths = {}
    for source in sources:
        name = source.get("file")
        path = source.get("path")
        if name and path:
            paths[str(name)] = str(path)
    return paths


def _window_for_slot(slot, used_by_slot, by_id, shotlog_by_key):
    row = used_by_slot.get(str(slot.get("id") or ""))
    shot_key = slot.get("shot_key")
    if row is None and shot_key:
        row = by_id.get(str(shot_key))
    if row is not None:
        window = _numeric_window(row.get("window_in_s"), row.get("window_out_s"))
        if window is not None:
            return window
    return shotlog_by_key.get(str(shot_key)) if shot_key else None


def _numeric_window(start, end):
    if _is_number(start) and _is_number(end):
        return (float(start), float(end))
    return None


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _seconds_text(value):
    if not _is_number(value):
        return ""
    return f"{float(value):.2f} s"


def _thumbnail_data_uri(path, t_s):
    if not path:
        return None
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        return None
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t_s) * 1000.0)
        ok, frame = capture.read()
        if not ok:
            return None
        return _encode_thumbnail(frame)
    finally:
        capture.release()


def _encode_thumbnail(frame):
    height, width = frame.shape[:2]
    if width > THUMBNAIL_WIDTH:
        scaled_height = max(1, int(round(height * THUMBNAIL_WIDTH / width)))
        frame = cv2.resize(frame, (THUMBNAIL_WIDTH, scaled_height), interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), THUMBNAIL_QUALITY])
    if not ok:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")


def _write_html(html_path, document):
    html_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = html_path.with_name(html_path.name + ".tmp")
    try:
        temporary.write_text(document, encoding="utf-8")
        os.replace(temporary, html_path)
    finally:
        if temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    sys.exit(main())
