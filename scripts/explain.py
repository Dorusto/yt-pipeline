#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
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
import json
import os
import sys
import time
from html import escape
from pathlib import Path

from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.errors import report_error
from common.frames import thumbnail_data_uri
from common.moves import middle_speed_of
from common.schema import SchemaError, validate

HTML_NAME = "explain.html"


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
            "  python scripts/explain.py --workspace <workspace>\n"
            "  python scripts/explain.py --workspace <workspace> --json\n"
            "  python scripts/explain.py --workspace <workspace> \\\n"
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
        return _summary("skipped", edit, out_path, started), EXIT_OK
    _check_out_location(out_path, workspace_path, edit.get("sources") or [])
    plan = _load_optional(workspace_path / "shot_plan.json", "shot_plan")
    shotlog = _load_optional(workspace_path / "shotlog.json", "shotlog")
    document = _html_document(workspace_path, edit, plan, shotlog)
    _write_html(out_path, document)
    return _summary("written", edit, out_path, started), EXIT_OK


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary, exit_code = run(args.workspace, out=args.out, force=args.force)
    except (ExplainError, SchemaError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    except OSError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
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
    sources = _source_paths(edit.get("sources") or [], workspace_path)
    director = (edit.get("meta") or {}).get("director")
    used_plan = (director or {}).get("by") != "rules"
    summary = " ".join(_summary_sentences(edit, plan, slots, decisions))
    sections = [
        _brief_section(edit.get("brief") or {}, edit.get("audio") or {}),
        _details_section("plan", "The director's plan", _plan_section(plan, used_plan=used_plan)),
        _details_section(
            "timeline",
            "Shots in the edit",
            _timeline_section(slots, sources, used_by_slot, by_id, shotlog_by_key),
        ),
        _details_section("left-out", "Left out and why", _left_out_section(decisions)),
        _details_section(
            "notes",
            "Technical notes",
            _director_section(director) or '<p class="missing">No repairs or notes.</p>',
        ),
    ]
    return _page(workspace_path.name, summary, "\n".join(sections))


def _summary_sentences(edit, plan, slots, decisions):
    """The 3-4 plain sentences at the top of the page, joined with a space."""
    used = sum(1 for row in decisions if row.get("status") == "used")
    sentences = [
        f"{len(slots)} shots in the edit, {used} of {len(decisions)} candidates used, "
        f"{len(decisions) - used} left out."
    ]
    names = [
        str(beat.get("name"))
        for beat in (plan or {}).get("structure") or []
        if isinstance(beat, dict) and beat.get("name")
    ]
    if names:
        sentences.append("The plan runs: " + " → ".join(names) + ".")
    else:
        sentences.append("No shot plan was used for this edit.")
    director = (edit.get("meta") or {}).get("director") or {}
    review = director.get("review")
    if isinstance(review, dict):
        if review.get("approved"):
            sentences.append("The reviewer approved the edit.")
        else:
            objections = review.get("objections") or []
            first = objections[0] if objections and isinstance(objections[0], dict) else None
            if first is None:
                sentences.append("The reviewer did not approve.")
            else:
                text = str(first.get("text") or "")
                shot = first.get("shot")
                if isinstance(shot, int) and not isinstance(shot, bool):
                    sentences.append(f"The reviewer did not approve: {text} (shot {shot}).")
                else:
                    sentences.append(f"The reviewer did not approve: {text}.")
    by = director.get("by")
    if by == "llm":
        sentences.append("Edited by the model.")
    elif by == "rules":
        sentences.append("Edited by the rule-based director.")
    return sentences


def _details_section(section_id, title, content):
    return (
        f'<details class="why-section" id="{section_id}" data-keep>'
        f"<summary>{escape(title)}</summary>"
        f"{content}"
        f"</details>"
    )


def _page(workspace_name, summary, sections):
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>explain · {escape(workspace_name)}</title>\n"
        "<style>\n"
        ":root { color-scheme: dark light; --water:#13202b; --deck:#1b2a37; --rope:#2b3d4d; "
        "--stone:#ece5d5; --haze:#9badbb; --sea:#57b8c4; --keep:#a3cf7f; --left:#e2806f; --busy:#e8b54d; "
        "--bg:var(--water); --surface:var(--deck); --line:var(--rope); --text:var(--stone); "
        "--muted:var(--haze); --accent:var(--sea); --ok:var(--keep); --warn:var(--busy); --danger:var(--left); "
        "--fs-xs:.78rem; --fs-s:.88rem; --fs-m:1rem; --fs-l:1.25rem; --fs-xl:1.6rem; --fs-2xl:2rem; "
        "--lh-tight:1.15; --lh-body:1.55; "
        "--font-display:ui-serif, Georgia, serif; --font-body:system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; "
        "--font-mono:ui-monospace, SFMono-Regular, Menlo, monospace; "
        "--sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px; --sp-5:24px; --sp-6:32px; "
        "--radius-s:6px; --radius-l:14px; --radius-pill:999px; --tap:44px; }\n"
        "@media (prefers-color-scheme: light) { :root { "
        "--water:#eef3f5; --deck:#ffffff; --rope:#d5dee3; --stone:#14232c; --haze:#5a6f7b; "
        "--sea:#17707c; --keep:#3d7a38; --left:#b0432e; --busy:#8a6410; } }\n"
        "* { box-sizing: border-box; }\n"
        "body { margin: 0; padding: 14px; font: 15px/1.5 system-ui, -apple-system, 'Segoe UI', sans-serif; "
        "background: var(--deck); color: var(--stone); }\n"
        "header { margin-bottom: 14px; }\n"
        "h1 { font-family: ui-serif, Georgia, serif; font-weight: 500; font-size: 1.35rem; margin: 0 0 2px; }\n"
        "header p { margin: 0; color: var(--haze); font-size: 0.9rem; font-variant-numeric: tabular-nums; }\n"
        "main { display: flex; flex-direction: column; gap: 22px; max-width: 760px; }\n"
        "section { border-top: 1px solid var(--rope); padding-top: 12px; }\n"
        "h2 { font-family: ui-serif, Georgia, serif; font-weight: 500; font-size: 1.15rem; margin: 0 0 10px; }\n"
        ".why-section { border-top: 1px solid var(--rope); padding-top: 12px; }\n"
        ".why-section summary { min-height: 44px; display: flex; align-items: center; cursor: pointer; "
        "font-family: ui-serif, Georgia, serif; font-weight: 500; font-size: 1.15rem; }\n"
        ".reason-group { margin: 6px 0 0 14px; }\n"
        ".reason-group summary { min-height: 36px; display: flex; align-items: center; cursor: pointer; "
        "font-size: 0.95rem; color: var(--haze); }\n"
        ".brief { margin: 0 0 8px; padding: 10px 14px; background: var(--water); "
        "border-left: 3px solid var(--sea); border-radius: 0 10px 10px 0; font-size: 1rem; }\n"
        ".meta-line { font-size: 0.82rem; color: var(--haze); margin: 2px 0; }\n"
        ".missing { color: var(--haze); font-style: italic; }\n"
        "ul { margin: 0 0 8px; padding-left: 20px; }\n"
        "li { margin: 3px 0; font-size: 0.92rem; }\n"
        ".slot { display: flex; gap: 12px; padding: 12px 0; border-top: 1px solid var(--rope); }\n"
        ".slot:first-of-type { border-top: none; padding-top: 2px; }\n"
        ".thumb { width: 300px; max-width: 45%; aspect-ratio: 16 / 9; object-fit: cover; "
        "border-radius: 8px; background: var(--water); flex-shrink: 0; align-self: flex-start; }\n"
        ".thumb.placeholder { display: flex; align-items: center; justify-content: center; "
        "color: var(--haze); font-size: 0.72rem; text-align: center; padding: 4px; overflow-wrap: anywhere; }\n"
        ".body { min-width: 0; }\n"
        ".slot-id { font-weight: 650; }\n"
        ".short-id { color: var(--sea); font-weight: 600; }\n"
        ".cut { font-size: 0.85rem; color: var(--stone); margin: 4px 0; font-variant-numeric: tabular-nums; }\n"
        ".reason { font-size: 0.88rem; color: var(--haze); margin: 4px 0 0; }\n"
        ".candidate { display: grid; grid-template-columns: auto auto 1fr; gap: 2px 10px; "
        "align-items: baseline; padding: 9px 0; border-top: 1px solid var(--rope); font-size: 0.88rem; }\n"
        ".candidate:first-of-type { border-top: none; }\n"
        ".cand-id { font-weight: 650; }\n"
        ".cand-meta { color: var(--haze); font-size: 0.8rem; font-variant-numeric: tabular-nums; }\n"
        ".cand-reason { grid-column: 1 / -1; color: var(--haze); }\n"
        ".candidate.used .cand-reason { color: var(--stone); }\n"
        ".badge { font-size: 0.8rem; font-weight: 600; display: inline-flex; align-items: center; gap: 5px; }\n"
        ".badge::before { content: ''; width: 8px; height: 8px; border-radius: 50%; background: currentColor; }\n"
        ".badge.used { color: var(--keep); }\n"
        ".badge.rejected { color: var(--left); }\n"
        "@media (max-width: 560px) { .thumb { width: 38%; } .slot { gap: 10px; } }\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        "<header>\n"
        "<h1>Why the edit looks like this</h1>\n"
        f'<p class="summary">{escape(summary)}</p>\n'
        "</header>\n"
        "<main>\n"
        f"{sections}\n"
        "</main>\n"
        "<script>\n"
        "(function(){var ds=document.querySelectorAll('details[data-keep]');\n"
        "for(var i=0;i<ds.length;i++){(function(d){var k='why:'+d.id;\n"
        "try{if(sessionStorage.getItem(k)==='open')d.open=true;}catch(e){}\n"
        "d.addEventListener('toggle',function(){try{sessionStorage.setItem(k,d.open?'open':'closed');}catch(e){}});})(ds[i]);}})();\n"
        "</script>\n"
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


DIRECTOR_TITLES = {
    "llm": "Edited by the model",
    "rules": "Edited by the rule-based director (the model's edit was not used)",
}


def _director_section(director):
    """Who made this edit, and the notes of the model's loop (repairs, fallback reason)."""
    if not isinstance(director, dict) or director.get("by") not in DIRECTOR_TITLES:
        return ""
    title = DIRECTOR_TITLES[director["by"]]
    if director["by"] == "llm" and director.get("approved") is False:
        title += " (reviewer did not approve; the last draft was kept and repaired)"
    lines = [f"<h2>{escape(title)}</h2>"]
    notes = [str(note) for note in director.get("notes") or []]
    if notes:
        lines.append("<ul>" + "".join(f"<li>{escape(note)}</li>" for note in notes) + "</ul>")
    return "\n".join(lines)


def _plan_section(plan, used_plan=True):
    heading = "The plan in words" if used_plan else "The model's plan (not used for this edit)"
    lines = [f"<h2>{heading}</h2>"]
    if not isinstance(plan, dict):
        lines.append(
            '<p class="missing">No shot plan (shot_plan.json) in this workspace — '
            "the edit was not planned by the LLM director.</p>"
        )
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
    return "\n".join(lines)


def _timeline_section(slots, sources, used_by_slot, by_id, shotlog_by_key):
    rows = "\n".join(
        _timeline_row(slot, sources, used_by_slot, by_id, shotlog_by_key) for slot in slots
    )
    return f"<h2>Timeline</h2>\n{rows}"


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
        data_uri = thumbnail_data_uri(path, 0.0)
    else:
        in_s = float(slot.get("in_s") or 0.0)
        out_s = float(slot.get("out_s") or 0.0)
        cut_text = f"cut {in_s:.2f}–{out_s:.2f} s"
        if middle_speed_of(slot) > 1.0:
            cut_text += f", middle at {middle_speed_of(slot):.2f}x"
        move_html = ""
        if slot.get("centred") and _is_number(slot.get("peak_s")):
            source = escape(str(slot.get("peak_source") or "middle"))
            move_html = (
                f'<div class="meta-line">centred on the best moment at '
                f'{float(slot["peak_s"]):.1f} s (from {source})</div>'
            )
        data_uri = thumbnail_data_uri(path, (in_s + out_s) / 2.0)
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
    meta_parts = [escape(source_name)]
    if _is_number(slot.get("score")):
        meta_parts.append(f"score {float(slot['score']):.2f}")
    meta_html = " · ".join(meta_parts)
    return (
        f'<div class="slot">'
        f"{thumb}"
        f'<div class="body">'
        f'<div class="slot-id">{slot_id}{key_html}</div>'
        f'<div class="meta-line">{meta_html}</div>'
        f'<div class="cut">{escape(cut_text)}</div>'
        f"{move_html}"
        f'<p class="reason">{reason}</p>'
        f"</div>"
        f"</div>"
    )


REASON_GROUPS = (
    ("near-duplicate", "near duplicate"),
    ("setup/teardown", "setup/teardown"),
    ("accidental", "accidental"),
    ("shake", "shaky"),
    ("blur", "blurry"),
    ("defect", "defect"),
    ("overlapping", "overlapping window"),
    ("low score", "low score"),
    ("did not fit", "did not fit the length"),
    ("not eligible", "not eligible"),
)


def _reason_group(reason):
    """The category a rejected candidate's reason falls into."""
    text = str(reason or "").lower()
    for needle, label in REASON_GROUPS:
        if needle in text:
            return label
    return str(reason or "").strip() or "other"


def _left_out_section(decisions):
    rejected = [row for row in decisions if row.get("status") != "used"]
    if not rejected:
        return '<p class="missing">Nothing was left out.</p>'
    groups = {}
    for row in rejected:
        groups.setdefault(_reason_group(row.get("reason")), []).append(row)
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    parts = []
    for category, rows in ordered:
        body = "\n".join(_candidate_row(row) for row in rows)
        parts.append(
            f'<details class="reason-group"><summary>{escape(category)} ({len(rows)})</summary>'
            f"{body}</details>"
        )
    return "\n".join(parts)


def _candidate_row(row):
    status = str(row.get("status") or "")
    ident = escape(str(row.get("id") or ""))
    if status == "used":
        row_class = "used"
        badge = f'<span class="badge used">in the edit as {escape(str(row.get("slot") or ""))}</span>'
    else:
        row_class = "rejected"
        badge = '<span class="badge rejected">left out</span>'
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


def _source_paths(sources, workspace_path=None):
    """Clip name -> readable path; a recorded path that is gone falls back to ``<workspace>/proxy``."""
    proxy_dir = Path(workspace_path) / "proxy" if workspace_path is not None else None
    paths = {}
    for source in sources:
        name = source.get("file")
        path = source.get("path")
        if not name:
            continue
        if (not path or not Path(path).exists()) and proxy_dir is not None and (proxy_dir / str(name)).is_file():
            path = str(proxy_dir / str(name))
        if path:
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
