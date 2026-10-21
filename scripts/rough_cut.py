#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn talking-head + screen sessions into a Mode B rough-cut ``edit.json``.

Usage:
    python3 scripts/rough_cut.py SESSIONS.yaml --out edit.json
        [--config FILE] [--llm] [--json]

The tool reads one or more recording sessions (a camera file, an optional
screen file and the session's Whisper word JSON), cuts silence/pause gaps,
standalone Romanian filler words and restarted sentences (retakes), and writes
an ``edit.json`` edit list validated against ``schemas/edit.schema.json``. Every
cut carries a reason; the kept windows become video slots, paired screen and
camera slots sharing one id when a session has a screen file. The optional
``--llm`` pass proposes extra cuts for human approval only and never changes the
edit. No transcription happens here: word times come only from the given word
JSON files.
"""

import argparse
import difflib
import json
import os
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from common.config import ConfigError
from common.llm import LLMError, make_client
from common.media import parse_stamp
from common.schema import SchemaError, validate_or_raise

TOOL = "scripts/rough_cut.py"
SCHEMA_VERSION = "0.1"
SENTENCE_ENDINGS = (".", "!", "?", "…")
RUN_PRIORITY = {"retake": 0, "filler": 1, "pause": 2, "lead-in": 3, "tail": 4}
CUT_KINDS = ("pause", "filler", "retake")

DEFAULT_CONFIG = {
    "pause_gap_s": 0.6,
    "handle_s": 0.12,
    "retake_window_s": 60.0,
    "retake_similarity": 0.75,
    "retake_min_words": 3,
    "repeat_window_s": 3.0,
    "fillers": {
        "always": ["ă", "îî", "ăă", "ăăă", "ok"],
        "repeated": ["deci", "practic"],
    },
}


class RoughCutError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def load_yaml(path):
    try:
        return yaml.safe_load(Path(path).expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RoughCutError(f"cannot read '{path}': file not found")
    except yaml.YAMLError as exc:
        raise RoughCutError(f"cannot read '{path}': invalid YAML ({exc})")
    except OSError as exc:
        raise RoughCutError(f"cannot read '{path}': {exc}")


def load_sessions(path):
    document = load_yaml(path)
    try:
        validate_or_raise(document, "sessions")
    except SchemaError as exc:
        raise RoughCutError(f"invalid sessions '{path}': {exc}")
    return document["sessions"]


def load_cut_config(path=None):
    """Return the cut rules: the defaults, overridden by the optional config file."""
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path is None:
        return config
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise RoughCutError(f"config '{path}': root must be a mapping")
    for key in ("pause_gap_s", "handle_s", "retake_window_s", "retake_similarity",
                "retake_min_words", "repeat_window_s"):
        if key in data:
            config[key] = data[key]
    if "fillers" in data:
        fillers = data["fillers"]
        if not isinstance(fillers, dict):
            raise RoughCutError(f"config '{path}': fillers must be a mapping")
        for key in ("always", "repeated"):
            if key in fillers:
                config["fillers"][key] = list(fillers[key])
    return config


def load_review(path):
    """Load the phone cut review rows; a missing or unreadable file is a hard error."""
    document = load_yaml(path)
    if not isinstance(document, dict):
        raise RoughCutError(f"review '{path}': root must be a mapping")
    rows = document.get("review")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise RoughCutError(f"review '{path}': review must be a list")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cut_id = row.get("id")
        action = row.get("action")
        if not isinstance(cut_id, str) or action not in ("keep", "cut"):
            continue
        result.append({
            "id": cut_id,
            "action": action,
            "clip": row.get("clip"),
            "window_in_s": row.get("window_in_s"),
            "window_out_s": row.get("window_out_s"),
        })
    return result


def review_for_run(review_rows, run_id, run):
    """The review row that applies to a run, or None when the id is absent or the span is stale."""
    for row in review_rows:
        if row["id"] != run_id:
            continue
        start = row.get("window_in_s")
        end = row.get("window_out_s")
        if not is_number(start) or not is_number(end):
            return None
        if float(start) < run["end"] and float(end) > run["start"]:
            return row
        return None
    return None


def load_words(path):
    """Load ``segments[].words[]`` in time order, skipping empty word strings."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RoughCutError(f"cannot read words '{path}': file not found")
    except json.JSONDecodeError as exc:
        raise RoughCutError(f"cannot read words '{path}': not valid JSON ({exc})")
    except OSError as exc:
        raise RoughCutError(f"cannot read words '{path}': {exc}")
    words = []
    for segment in data.get("segments") or []:
        for word in segment.get("words") or []:
            text = str(word.get("word", "")).strip()
            if not text:
                continue
            start = word.get("start")
            end = word.get("end")
            if not is_number(start) or not is_number(end):
                raise RoughCutError(f"words '{path}': a word entry has no numeric start/end")
            words.append({"text": text, "start": float(start), "end": float(end)})
    words.sort(key=lambda word: (word["start"], word["end"]))
    return words


def normalize_token(text):
    """Lowercase a token and strip whitespace and punctuation from both ends."""
    stripped = text.strip()
    while stripped and unicodedata.category(stripped[0]).startswith("P"):
        stripped = stripped[1:]
    while stripped and unicodedata.category(stripped[-1]).startswith("P"):
        stripped = stripped[:-1]
    return stripped.lower()


def probe_media(path):
    """Return ``(duration_s, start_time_s)`` from ffprobe, or ``(None, 0.0)``."""
    media = Path(path).expanduser()
    if not media.is_file():
        return None, 0.0
    command = ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(media)]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError:
        return None, 0.0
    if result.returncode != 0:
        return None, 0.0
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, 0.0
    fmt = data.get("format") or {}
    duration = fmt.get("duration")
    start_time = fmt.get("start_time")
    duration_s = float(duration) if is_number(duration) else None
    start_time_s = float(start_time) if is_number(start_time) else 0.0
    return duration_s, start_time_s


def absolute_start(path, start_time_s):
    stamp = parse_stamp(path)
    if stamp is None:
        return None
    return stamp + timedelta(seconds=start_time_s)


def compute_sync_offset(camera, screen, warnings):
    """Compute ``abs_start(camera) - abs_start(screen)``; 0.0 with a warning when unknown."""
    _, camera_start = probe_media(camera)
    _, screen_start = probe_media(screen)
    camera_abs = absolute_start(camera, camera_start)
    screen_abs = absolute_start(screen, screen_start)
    if camera_abs is None or screen_abs is None:
        warnings.append(
            f"cannot compute sync offset for '{Path(screen).name}': no filename timestamp"
        )
        return 0.0
    return (camera_abs - screen_abs).total_seconds()


def split_sentences(words, pause_gap_s):
    """Split the word list into sentences at punctuation or a gap over ``pause_gap_s``."""
    sentences = []
    current = []
    for index, word in enumerate(words):
        if current:
            previous = words[current[-1]]
            if word["start"] - previous["end"] > pause_gap_s:
                sentences.append(current)
                current = []
        current.append(index)
        if word["text"].strip().endswith(SENTENCE_ENDINGS):
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    return sentences


def filler_tokens(config):
    always = {normalize_token(token) for token in config["fillers"]["always"]}
    repeated = {normalize_token(token) for token in config["fillers"]["repeated"]}
    return always, repeated


def pause_removals(words, config, duration, warnings, camera):
    """Return the pause, lead-in and tail spans to remove."""
    handle = config["handle_s"]
    gap = config["pause_gap_s"]
    removals = []
    for previous, following in zip(words, words[1:]):
        if following["start"] - previous["end"] > gap:
            start = previous["end"] + handle
            end = following["start"] - handle
            if end > start:
                removals.append({
                    "start": start,
                    "end": end,
                    "kind": "pause",
                    "detail": following["start"] - previous["end"],
                })
    if words:
        lead_end = words[0]["start"] - handle
        if lead_end > 0:
            removals.append({"start": 0.0, "end": lead_end, "kind": "lead-in", "detail": None})
        tail_start = words[-1]["end"] + handle
        if duration is not None and duration > tail_start:
            removals.append({"start": tail_start, "end": duration, "kind": "tail", "detail": None})
        elif duration is None or duration < tail_start:
            warnings.append(f"no usable duration for '{Path(camera).name}': tail silence not cut")
    return removals


def filler_removals(words, config):
    """Return the spans of standalone and repeated filler words to remove."""
    always, repeated = filler_tokens(config)
    removals = []
    for word in words:
        token = normalize_token(word["text"])
        if token in always:
            removals.append({"start": word["start"], "end": word["end"], "kind": "filler", "detail": token})
    occurrences = {}
    for index, word in enumerate(words):
        token = normalize_token(word["text"])
        if token in repeated:
            occurrences.setdefault(token, []).append(index)
    for token, indices in occurrences.items():
        cluster = [indices[0]]
        for index in indices[1:]:
            if words[index]["start"] - words[cluster[-1]]["start"] <= config["repeat_window_s"]:
                cluster.append(index)
            else:
                if len(cluster) >= 2:
                    removals.extend(
                        {"start": words[member]["start"], "end": words[member]["end"],
                         "kind": "filler", "detail": token}
                        for member in cluster
                    )
                cluster = [index]
        if len(cluster) >= 2:
            removals.extend(
                {"start": words[member]["start"], "end": words[member]["end"],
                 "kind": "filler", "detail": token}
                for member in cluster
            )
    return removals


def retake_removals(words, sentences, config):
    """Return the retake spans and a map from a cut sentence to the later sentence that restarts it."""
    always, repeated = filler_tokens(config)

    def content(sentence):
        tokens = []
        for index in sentence:
            token = normalize_token(words[index]["text"])
            if token in always or token in repeated:
                continue
            tokens.append(token)
        return tokens

    removals = []
    retake_map = {}
    for i, sentence_i in enumerate(sentences):
        wi = content(sentence_i)
        if not wi:
            continue
        i_start = words[sentence_i[0]]["start"]
        for j in range(i + 1, len(sentences)):
            sentence_j = sentences[j]
            j_start = words[sentence_j[0]]["start"]
            if j_start - i_start > config["retake_window_s"]:
                break
            wj = content(sentence_j)
            if len(wj) < config["retake_min_words"]:
                continue
            ratio = difflib.SequenceMatcher(None, wj, wi[:len(wj)]).ratio()
            if ratio >= config["retake_similarity"]:
                removals.append({
                    "start": i_start,
                    "end": words[sentence_i[-1]]["end"],
                    "kind": "retake",
                    "detail": i,
                })
                retake_map[i] = j
                break
    return removals, retake_map


def merge_spans(spans):
    """Merge removed spans that overlap or touch into runs."""
    ordered = sorted(spans, key=lambda span: (span["start"], span["end"]))
    merged = []
    for span in ordered:
        if merged and span["start"] <= merged[-1]["end"]:
            if span["end"] > merged[-1]["end"]:
                merged[-1]["end"] = span["end"]
            merged[-1]["spans"].append(span)
        else:
            merged.append({"start": span["start"], "end": span["end"], "spans": [span]})
    return merged


def run_kind(run):
    return min(run["spans"], key=lambda span: RUN_PRIORITY[span["kind"]])["kind"]


def run_reason(run, kept_take_ids):
    kind = run_kind(run)
    if kind == "retake":
        span = next(span for span in run["spans"] if span["kind"] == "retake")
        take_id = kept_take_ids.get(span["detail"])
        return f"retake of {take_id}" if take_id else "retake"
    if kind == "filler":
        span = next(span for span in run["spans"] if span["kind"] == "filler")
        return f"filler '{span['detail']}'"
    if kind == "pause":
        span = next(span for span in run["spans"] if span["kind"] == "pause")
        return f"pause {span['detail']:.1f} s"
    if kind == "lead-in":
        return "lead-in silence"
    return "tail silence"


def kept_windows(merged, session_end):
    """Return the complement of the merged runs over ``[0, session_end]``."""
    windows = []
    cursor = 0.0
    for run in merged:
        if run["start"] > cursor:
            windows.append({"start": cursor, "end": run["start"]})
        cursor = max(cursor, run["end"])
    if cursor < session_end:
        windows.append({"start": cursor, "end": session_end})
    return [window for window in windows if window["end"] - window["start"] > 1e-9]


def final_sentence(index, retake_map):
    seen = set()
    while index in retake_map and index not in seen:
        seen.add(index)
        index = retake_map[index]
    return index


def process_session(session, config, warnings, state, camera_duration, review_rows=None, review_state=None):
    """Cut one session and return its slots, decisions and counts."""
    camera = session["camera"]
    screen = session.get("screen")
    words = load_words(session["words"])
    if not Path(camera).expanduser().is_file():
        warnings.append(f"missing media: {camera}")
    if screen and not Path(screen).expanduser().is_file():
        warnings.append(f"missing media: {screen}")

    sentences = split_sentences(words, config["pause_gap_s"])
    spans = []
    spans.extend(pause_removals(words, config, camera_duration, warnings, camera))
    spans.extend(filler_removals(words, config))
    retake_spans, retake_map = retake_removals(words, sentences, config)
    spans.extend(retake_spans)
    merged = merge_spans(spans)

    review_rows = review_rows or []
    review_state = review_state if review_state is not None else {"applied": set(), "keep": 0, "cut": 0}
    run_ids = []
    review_kept = []
    removal_runs = []
    for run in merged:
        run_id = f"c{state['run']:03d}"
        state["run"] += 1
        run_ids.append((run_id, run))
        row = review_for_run(review_rows, run_id, run)
        if row is not None:
            review_state["applied"].add(run_id)
            if row["action"] == "keep":
                review_state["keep"] += 1
                review_kept.append({"id": run_id, "run": run})
                continue
            review_state["cut"] += 1
        removal_runs.append(run)

    if words:
        tail_start = words[-1]["end"] + config["handle_s"]
        session_end = camera_duration if (camera_duration is not None and camera_duration > tail_start) else tail_start
    else:
        session_end = camera_duration or 0.0

    window_records = []
    for window in kept_windows(removal_runs, session_end):
        window_records.append({
            "id": f"w{state['window']:03d}",
            "start": window["start"],
            "end": window["end"],
        })
        state["window"] += 1

    kept_take_ids = {}
    for span in retake_spans:
        final = final_sentence(span["detail"], retake_map)
        start = words[sentences[final][0]]["start"]
        for record in window_records:
            if record["start"] <= start < record["end"]:
                kept_take_ids[span["detail"]] = record["id"]
                break

    kept_ids = {item["id"] for item in review_kept}
    run_records = []
    for run_id, run in run_ids:
        if run_id in kept_ids:
            continue
        run_records.append({
            "id": run_id,
            "start": run["start"],
            "end": run["end"],
            "kind": run_kind(run),
            "reason": run_reason(run, kept_take_ids),
        })

    for index, record in enumerate(window_records):
        if index == 0:
            record["reason"] = "start: session"
            continue
        preceding = None
        for run in run_records:
            if run["end"] <= record["start"] + 1e-9:
                preceding = run
            else:
                break
        record["reason"] = f"cut: {preceding['reason']}" if preceding else "start: session"

    review_kept_decisions = []
    for item in review_kept:
        run = item["run"]
        midpoint = (run["start"] + run["end"]) / 2.0
        absorbing = None
        for record in window_records:
            if record["start"] <= midpoint < record["end"]:
                absorbing = record
                break
        if absorbing is None:
            for record in window_records:
                if record["start"] < run["end"] and record["end"] > run["start"]:
                    absorbing = record
                    break
        if absorbing is not None:
            absorbing["reason"] = "kept by review"
        review_kept_decisions.append({
            "id": item["id"],
            "status": "used",
            "reason": "kept by review",
            "slot": absorbing["id"] if absorbing else None,
            "score": 1.0,
            "clip": Path(camera).name,
            "window_in_s": run["start"],
            "window_out_s": run["end"],
        })

    if screen:
        if session.get("sync_offset_s") is not None:
            sync_offset = float(session["sync_offset_s"])
        else:
            sync_offset = compute_sync_offset(camera, screen, warnings)
    else:
        sync_offset = None

    slots = []
    for record in window_records:
        if screen:
            screen_in = record["start"] + sync_offset
            screen_out = record["end"] + sync_offset
            if screen_out <= 0:
                warnings.append(f"screen window for {record['id']} ends before 0: no screen slot")
            else:
                if screen_in < 0:
                    screen_in = 0.0
                    warnings.append(f"screen window for {record['id']} starts before 0: clamped to 0")
                slots.append({
                    "id": record["id"],
                    "clip": Path(screen).name,
                    "in_s": screen_in,
                    "out_s": screen_out,
                    "score": 1.0,
                    "reason": record["reason"],
                })
        slots.append({
            "id": record["id"],
            "clip": Path(camera).name,
            "in_s": record["start"],
            "out_s": record["end"],
            "score": 1.0,
            "reason": record["reason"],
        })

    decisions = []
    for record in window_records:
        decisions.append({
            "id": record["id"],
            "status": "used",
            "reason": "kept speech",
            "slot": record["id"],
            "score": 1.0,
            "clip": Path(camera).name,
            "window_in_s": record["start"],
            "window_out_s": record["end"],
        })
    for run in run_records:
        decisions.append({
            "id": run["id"],
            "status": "rejected",
            "reason": run["reason"],
            "slot": None,
            "score": None,
            "clip": Path(camera).name,
            "window_in_s": run["start"],
            "window_out_s": run["end"],
        })
    decisions.extend(review_kept_decisions)
    decisions.sort(key=lambda decision: decision["window_in_s"])

    words_kept = 0
    kept_words = []
    for word in words:
        midpoint = (word["start"] + word["end"]) / 2.0
        if any(record["start"] <= midpoint < record["end"] for record in window_records):
            words_kept += 1
            kept_words.append(word)

    cuts = {kind: 0 for kind in CUT_KINDS}
    for run in run_records:
        if run["kind"] in cuts:
            cuts[run["kind"]] += 1

    return {
        "slots": slots,
        "decisions": decisions,
        "windows": len(window_records),
        "words_read": len(words),
        "words_kept": words_kept,
        "kept_words": kept_words,
        "cuts": cuts,
    }


def build_llm_prompt(kept_words):
    lines = [f"{word['start']:.2f}-{word['end']:.2f} {word['text']}" for word in kept_words]
    transcript = "\n".join(lines)
    return (
        "You are the director of a talking-head + screen recording rough cut. "
        "The transcript below is the kept speech after silence, filler and retake cuts. "
        "Propose extra cuts for off-topic asides or dead ends, for human approval. "
        "Answer with JSON only: "
        '{"proposals": [{"start_s": <number>, "end_s": <number>, "reason": "<short reason>"}]}.'
        f"\n\nTranscript:\n{transcript}"
    )


def parse_proposals(text):
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        items = data.get("proposals") or []
    elif isinstance(data, list):
        items = data
    else:
        return []
    proposals = []
    for item in items:
        if not isinstance(item, dict):
            continue
        start = item.get("start_s")
        end = item.get("end_s")
        reason = item.get("reason")
        if not is_number(start) or not is_number(end) or not isinstance(reason, str):
            continue
        proposals.append({"start_s": float(start), "end_s": float(end), "reason": reason})
    return proposals


def write_json(path, document):
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(out.parent), prefix=out.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(tmp_name, out)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def run(sessions_path, out_path, config_path=None, use_llm=False, review_path=None):
    """Cut the sessions and write ``edit.json``; return the ``--json`` summary."""
    started = time.monotonic()
    config = load_cut_config(config_path)
    sessions = load_sessions(sessions_path)
    review_rows = load_review(review_path) if review_path else []
    review_state = {"applied": set(), "keep": 0, "cut": 0}
    warnings = []
    sources = []
    video = []
    decisions = []
    state = {"window": 1, "run": 1}
    words_read = 0
    words_kept = 0
    windows_total = 0
    cuts = {kind: 0 for kind in CUT_KINDS}
    kept_words = []

    for session in sessions:
        camera = session["camera"]
        screen = session.get("screen")
        camera_duration, _ = probe_media(camera)
        sources.append({
            "file": Path(camera).name,
            "path": camera,
            "duration_s": camera_duration,
            "media_type": "video",
        })
        if screen:
            screen_duration, _ = probe_media(screen)
            sources.append({
                "file": Path(screen).name,
                "path": screen,
                "duration_s": screen_duration,
                "media_type": "video",
            })
        result = process_session(session, config, warnings, state, camera_duration, review_rows, review_state)
        video.extend(result["slots"])
        decisions.extend(result["decisions"])
        windows_total += result["windows"]
        words_read += result["words_read"]
        words_kept += result["words_kept"]
        kept_words.extend(result["kept_words"])
        for kind in cuts:
            cuts[kind] += result["cuts"][kind]

    for row in review_rows:
        if row["id"] not in review_state["applied"]:
            warnings.append(f"review row '{row['id']}' does not overlap any cut: ignored")

    proposals = []
    proposals_path = None
    llm_cost = 0.0
    if use_llm:
        proposals_path = Path(out_path).expanduser().parent / "rough_cut_proposals.json"
        client = make_client()
        response = client.complete("director", [{"role": "user", "content": build_llm_prompt(kept_words)}])
        llm_cost = response.cost_usd
        proposals = parse_proposals(response.text)
        write_json(proposals_path, {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now_iso(),
            "tool": TOOL,
            "proposals": proposals,
        })

    for slot in video:
        assert slot["in_s"] < slot["out_s"], f"slot {slot['id']} has in_s >= out_s"

    wall_time_s = round(time.monotonic() - started, 3)
    document = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "tool": TOOL,
        "brief": {
            "text": f"Mode B rough cut: silence, fillers and retakes removed from {len(sessions)} session(s)",
            "target_duration_s": None,
            "order": "chronological",
            "pacing": "uniform",
        },
        "sources": sources,
        "video": video,
        "decisions": decisions,
        "meta": {
            "tool": TOOL,
            "wall_time_s": wall_time_s,
            "llm_cost_usd": round(llm_cost, 8),
            "director": {"by": "rules", "approved": None, "notes": []},
        },
    }
    validate_or_raise(document, "edit")
    write_json(out_path, document)

    summary = {
        "outputs": {
            "edit": str(Path(out_path).expanduser()),
            "proposals": str(proposals_path) if proposals_path else None,
        },
        "sessions": len(sessions),
        "windows": windows_total,
        "slots": len(video),
        "words_read": words_read,
        "words_kept": words_kept,
        "cuts": cuts,
        "review": {
            "keep": review_state["keep"],
            "cut": review_state["cut"],
            "ignored": len(review_rows) - len(review_state["applied"]),
        },
        "warnings": warnings,
        "wall_time_s": wall_time_s,
        "llm_cost_usd": round(llm_cost, 8),
    }
    if use_llm:
        summary["proposals"] = proposals
    return summary


def build_parser():
    parser = argparse.ArgumentParser(
        prog="rough_cut.py",
        description="Cut silence, fillers and retakes from talking-head + screen sessions into an edit.json rough cut.",
        epilog=(
            "examples:\n"
            "  python3 scripts/rough_cut.py sessions.yaml --out edit.json --json\n"
            "  python3 scripts/rough_cut.py sessions.yaml --out edit.json --config rough_cut_config.yaml\n"
            "  python3 scripts/rough_cut.py sessions.yaml --out edit.json --llm --json\n"
            "  python3 scripts/rough_cut.py sessions.yaml --out edit.json --review cut_review.yaml --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("sessions", help="session manifest YAML (validated against schemas/sessions.schema.json)")
    parser.add_argument("--out", required=True, help="the edit.json to write (atomic replace)")
    parser.add_argument("--config", help="cut-rules YAML; without it the built-in defaults apply")
    parser.add_argument("--review", help="cut_review.yaml from the phone review; keep rows stay in the edit")
    parser.add_argument("--llm", action="store_true", help="propose extra cuts for human approval (never applied)")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = run(args.sessions, args.out, args.config, args.llm, args.review)
    except (RoughCutError, SchemaError, ConfigError, LLMError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    if args.json:
        print(json.dumps({"ok": True, **summary}))
    else:
        print(
            f"written: {summary['outputs']['edit']} ({summary['windows']} windows, "
            f"{summary['slots']} slots, {summary['words_kept']}/{summary['words_read']} words kept)"
        )
        for warning in summary["warnings"]:
            print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
