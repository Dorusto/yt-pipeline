# SPDX-License-Identifier: AGPL-3.0-or-later
"""SRT parsing and transcript helpers shared by the publish tools."""

import re
from pathlib import Path

SRT_END = re.compile(r"--> (\d{2}):(\d{2}):(\d{2}),(\d{3})")


def parse_srt_time(value):
    value = value.replace(",", ".")
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_srt(text):
    """Return ``[{start, end, text}]`` in seconds for every cue of an SRT body."""
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        match = re.match(
            r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})",
            lines[1],
        )
        if not match:
            continue
        cues.append({
            "start": parse_srt_time(match.group(1)),
            "end": parse_srt_time(match.group(2)),
            "text": " ".join(line.strip() for line in lines[2:]).strip(),
        })
    return cues


def format_clock(seconds):
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_transcript(cues):
    return "\n".join(f"[{format_clock(cue['start'])}] {cue['text']}" for cue in cues)


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
