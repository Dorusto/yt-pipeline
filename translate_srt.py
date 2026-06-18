#!/usr/bin/env python3
"""
Traduce un fisier .srt din romana in engleza via DeepSeek API.
Pastreaza exact structura .srt: numere, timestamps, linii goale.

Utilizare:
    python3 translate_srt.py input.srt
    python3 translate_srt.py input.srt output_EN.srt

Daca nu specifici output, il genereaza automat: input_EN.srt

Necesita: DEEPSEEK_API_KEY in environment
Necesita: pip install openai
"""

import os
import re
import sys

from openai import OpenAI

BATCH_SIZE = 20

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com"
)


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
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    result = response.choices[0].message.content.strip()
    translations = {}
    for line in result.splitlines():
        m = re.match(r"\[(\d+)\]\s*(.*)", line)
        if m:
            translations[int(m.group(1))] = m.group(2)
    return translations


def main():
    if len(sys.argv) < 2:
        print("Utilizare: python3 translate_srt.py input.srt [output.srt]")
        sys.exit(1)

    input_file = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]
    else:
        output_file = input_file.replace(".srt", "_EN.srt")

    segments = parse_srt(input_file)
    print(f"Segmente găsite: {len(segments)}")

    texts = [s["text"].replace("\n", " ") for s in segments]
    translated_texts = {}

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


if __name__ == "__main__":
    main()
