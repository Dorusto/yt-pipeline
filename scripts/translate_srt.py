#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Traduce un fisier .srt din romana in engleza via DeepSeek API.
Pastreaza exact structura .srt: numere, timestamps, linii goale.

Utilizare:
    python3 translate_srt.py input.srt
    python3 translate_srt.py input.srt output_EN.srt

Daca nu specifici output, il genereaza automat: input_EN.srt

Necesita: OPENROUTER_API_KEY in environment
Necesita: pip install openai
"""

import re
import sys
from pathlib import Path

from common.config import ConfigError
from common.llm import LLMError, make_client

BATCH_SIZE = 20

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


def translate_batch(texts):
    numbered = "\n".join(f"[{i+1}] {t}" for i, t in enumerate(texts))
    prompt = (
        "Translate the following Romanian subtitle lines to English.\n"
        "Rules:\n"
        "- Keep the [N] prefix on each line\n"
        "- Preserve the exact number of lines (one [N] per input line)\n"
        "- Natural, conversational English\n"
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


def main():
    if len(sys.argv) < 2:
        print("Utilizare: python3 translate_srt.py input.srt [video.mp4 | output_EN.srt]")
        sys.exit(1)

    input_file = Path(sys.argv[1])
    if len(sys.argv) > 2 and sys.argv[2].lower().endswith(".mp4"):
        video_dir = Path(sys.argv[2]).expanduser().resolve().parent
        basename_en = input_file.name.replace(".srt", "_EN.srt")
        output_file = video_dir / basename_en
    elif len(sys.argv) > 2:
        output_file = Path(sys.argv[2])
    else:
        output_file = input_file.with_name(input_file.name.replace(".srt", "_EN.srt"))

    segments = parse_srt(input_file)
    print(f"Segmente găsite: {len(segments)}")

    texts = [s["text"].replace("\n", " ") for s in segments]
    translated_texts = {}

    try:
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i:i + BATCH_SIZE]
            print(f"Traduc segmentele {i+1}–{i+len(batch)}...")
            result = translate_batch(batch)
            for j, translation in result.items():
                translated_texts[i + j] = translation

        with open(output_file, "w", encoding="utf-8") as f:
            for idx, seg in enumerate(segments):
                translation = translated_texts.get(idx + 1, seg["text"])
                f.write(f"{seg['num']}\n{seg['time']}\n{translation}\n\n")

        print(f"Salvat: {output_file}")
    except (ConfigError, LLMError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(2)

    print(f"LLM cost: ${get_client().spent_usd:.4f}")


if __name__ == "__main__":
    main()
