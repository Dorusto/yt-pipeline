#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Traduce un fisier .srt din romana in engleza via DeepSeek API.
Pastreaza exact structura .srt: numere, timestamps, linii goale.

Utilizare:
    python3 translate_srt.py input.srt
    python3 translate_srt.py input.srt output_EN.srt
    python3 translate_srt.py input.srt video.mp4 --json

Daca nu specifici output, il genereaza automat: input_EN.srt

Necesita: OPENROUTER_API_KEY in environment
Necesita: pip install openai
"""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common.config import ConfigError
from common.llm import LLMError, make_client
from common.summary import summary_fields
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME

BATCH_SIZE = 20
WORKERS = 4
MAX_LINE_CHARS = 42

_client = None


def get_client():
    global _client
    if _client is None:
        _client = make_client()
    return _client


def parse_srt(path):
    with open(path, encoding="utf-8") as f:
        content = f.read()
    blocks = re.split(r"\n\n+", content.strip())
    segments = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) >= 3:
            segments.append({
                "num": lines[0],
                "time": lines[1],
                "text": "\n".join(lines[2:])
            })
    return segments


def wrap_subtitle(text, width=MAX_LINE_CHARS):
    """Lay a translated cue out as at most 2 lines, each at most ``width`` characters when it fits."""
    words = text.split()
    if len(" ".join(words)) <= width:
        return " ".join(words)
    # Pick the split that keeps the longer line shortest, so the two lines stay balanced.
    best = min(range(1, len(words)),
               key=lambda k: max(len(" ".join(words[:k])), len(" ".join(words[k:]))),
               default=len(words))
    return " ".join(words[:best]) + "\n" + " ".join(words[best:])


def translate_batch(texts):
    numbered = "\n".join(f"[{i+1}] {t}" for i, t in enumerate(texts))
    prompt = (
        "Translate the following Romanian subtitle lines to English.\n"
        "Rules:\n"
        "- Keep the [N] prefix on each line\n"
        "- Preserve the exact number of lines (one [N] per input line)\n"
        "- Natural, conversational English\n"
        "- Keep each line about as long as the Romanian one; it is a subtitle read in a few seconds\n"
        "- Do not merge or split items\n\n"
        f"{numbered}"
    )
    response = get_client().complete(
        "translate",
        [{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    result = response.text.strip()
    translations = {}
    for line in result.splitlines():
        m = re.match(r"\[(\d+)\]\s*(.*)", line)
        if m:
            translations[int(m.group(1))] = m.group(2)
    return translations


def spent_usd():
    return _client.spent_usd if _client is not None else 0.0


def main():
    started = time.monotonic()
    json_out = "--json" in sys.argv[1:]
    stream = sys.stderr if json_out else sys.stdout
    positionals = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not positionals:
        print("Utilizare: python3 translate_srt.py input.srt [video.mp4 | output_EN.srt] [--json]", file=stream)
        sys.exit(EXIT_USAGE)

    input_file = Path(positionals[0])
    if len(positionals) > 1 and positionals[1].lower().endswith(".mp4"):
        video_dir = Path(positionals[1]).expanduser().resolve().parent
        basename_en = input_file.name.replace(".srt", "_EN.srt")
        output_file = video_dir / basename_en
    elif len(positionals) > 1:
        output_file = Path(positionals[1])
    else:
        output_file = input_file.with_name(input_file.name.replace(".srt", "_EN.srt"))

    segments = parse_srt(input_file)
    print(f"Segmente găsite: {len(segments)}", file=stream)

    texts = [s["text"].replace("\n", " ") for s in segments]
    translated_texts = {}

    try:
        starts = list(range(0, len(texts), BATCH_SIZE))
        print(f"Traduc {len(texts)} segmente în {len(starts)} loturi, câte {WORKERS} odată...", file=stream)
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = pool.map(lambda i: (i, translate_batch(texts[i:i + BATCH_SIZE])), starts)
            for i, result in results:
                for j, translation in result.items():
                    translated_texts[i + j] = translation

        with open(output_file, "w", encoding="utf-8") as f:
            for idx, seg in enumerate(segments):
                translation = wrap_subtitle(translated_texts.get(idx + 1, seg["text"]))
                f.write(f"{seg['num']}\n{seg['time']}\n{translation}\n\n")

        print(f"Salvat: {output_file}", file=stream)
    except ConfigError as exc:
        if json_out:
            print(json.dumps({"ok": False, "error": str(exc), **summary_fields(started, spent_usd())}))
        else:
            print(f"[error] {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    except LLMError as exc:
        if json_out:
            print(json.dumps({"ok": False, "error": str(exc), **summary_fields(started, spent_usd())}))
        else:
            print(f"[error] {exc}", file=sys.stderr)
        sys.exit(EXIT_RUNTIME)

    if json_out:
        print(json.dumps({
            "ok": True,
            "output": str(output_file),
            "segments": len(segments),
            **summary_fields(started, spent_usd()),
        }))
    else:
        print(f"LLM cost: ${get_client().spent_usd:.4f}")


if __name__ == "__main__":
    main()
