#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generate a retimed .srt for a rough-cut assembled by kdenlive_from_fragments.py.

Takes the same fragments YAML (file, in, out, in beat/timeline order) plus the
directory of raw per-clip SRT transcripts, and produces a single .srt where
every raw subtitle line that falls inside a used fragment is remapped to its
new position on the edited timeline. Lines outside any fragment are dropped.

Usage:
    python3 srt_from_fragments.py fragments.yaml transcripts_raw/ output.srt
"""
import argparse
import json
import re
import sys
import time

import yaml

from common.overlap import overlap_bounds
from common.schema import SchemaError, validate
from common.summary import summary_fields
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME

SRT_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")
TC = re.compile(r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})")


def load_config(path):
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    errors = validate(cfg, "fragments")
    if errors:
        raise SchemaError("fragments", errors)
    return cfg


def srt_to_sec(m):
    h, mn, s, ms = (int(x) for x in m.groups())
    return h * 3600 + mn * 60 + s + ms / 1000


def tc_to_sec(tc):
    m = TC.match(tc)
    h, mn, s, ms = (int(x) for x in m.groups())
    return h * 3600 + mn * 60 + s + ms / 1000


def sec_to_srt(s):
    if s < 0:
        s = 0
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    ms = int(round((s - int(s)) * 1000))
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def parse_srt(path):
    text = open(path, encoding="utf-8").read()
    blocks = re.split(r"\n\s*\n", text.strip())
    entries = []
    for b in blocks:
        lines = b.strip().splitlines()
        if len(lines) < 2:
            continue
        time_line = None
        for line in lines:
            if "-->" in line:
                time_line = line
                break
        if not time_line:
            continue
        start_m, end_m = SRT_TIME.search(time_line.split("-->")[0]), SRT_TIME.search(time_line.split("-->")[1])
        start = srt_to_sec(start_m)
        end = srt_to_sec(end_m)
        text_lines = lines[lines.index(time_line) + 1:]
        entries.append((start, end, " ".join(text_lines)))
    return entries


def build(fragments_path, transcripts_dir, output_path, quiet=False):
    cfg = load_config(fragments_path)

    out_entries = []
    timeline_pos = 0.0
    srt_cache = {}

    for frag in cfg["fragments"]:
        fname = frag["file"]
        srt_name = fname.rsplit(".", 1)[0] + ".srt"
        if srt_name not in srt_cache:
            srt_cache[srt_name] = parse_srt(f"{transcripts_dir.rstrip('/')}/{srt_name}")
        entries = srt_cache[srt_name]

        frag_in = tc_to_sec(frag["in"])
        frag_out = tc_to_sec(frag["out"])
        frag_dur = frag_out - frag_in

        for start, end, text in entries:
            clipped_start, clipped_end = overlap_bounds(start, end, frag_in, frag_out)
            if clipped_end <= clipped_start:
                continue
            new_start = timeline_pos + (clipped_start - frag_in)
            new_end = timeline_pos + (clipped_end - frag_in)
            out_entries.append((new_start, new_end, text, frag.get("label", "")))

        timeline_pos += frag_dur

    with open(output_path, "w", encoding="utf-8") as f:
        for i, (start, end, text, label) in enumerate(out_entries, 1):
            f.write(f"{i}\n{sec_to_srt(start)} --> {sec_to_srt(end)}\n{text}\n\n")

    if not quiet:
        print(f"written: {output_path} ({len(out_entries)} lines, timeline length {timeline_pos:.1f}s)")
    return {"output": output_path, "lines": len(out_entries), "duration": round(timeline_pos, 3)}


def main(argv=None):
    started = time.monotonic()
    parser = argparse.ArgumentParser(
        description="Retime raw per-clip subtitles onto a rough-cut timeline."
    )
    parser.add_argument("fragments", help="path to fragments.yaml")
    parser.add_argument("transcripts_dir", help="folder with the raw per-clip .srt files")
    parser.add_argument("output", help="path of the .srt to write")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    args = parser.parse_args(argv)

    try:
        summary = build(args.fragments, args.transcripts_dir, args.output, quiet=args.json)
    except (SchemaError, FileNotFoundError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc), **summary_fields(started)}))
        else:
            print(f"[error] {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.json:
        print(json.dumps({"ok": True, **summary, **summary_fields(started)}))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
