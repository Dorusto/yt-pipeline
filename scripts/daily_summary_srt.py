#!/usr/bin/env python3
"""
Generează un rezumat narativ pe zile dintr-un folder de transcripturi .srt brute
(footage acțiune/dronă, gen DJI_YYYYMMDDHHMMSS_NNN_D.srt).

Grupează fișierele după data din numele fișierului, concatenează segmentele
în ordine cronologică per zi, și cere unui model (DeepSeek) un rezumat complet
al zilei — nu filtrat pe unghi de montaj, doar tot ce s-a povestit, ca ajutor
de memorie pentru autor, separat de orice structură de video deja aleasă.

Utilizare:
    python3 daily_summary_srt.py transcripts_raw/ [output.md]

Necesită: OPENROUTER_API_KEY în environment
Necesită: pip install openai
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

from common.config import ConfigError
from common.llm import LLMError, make_client

BATCH_CHARS = 80000
DATE_PATTERN = re.compile(r"(\d{4})(\d{2})(\d{2})(\d{6})")

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
    lines_out = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) >= 3:
            lines_out.append(" ".join(lines[2:]))
    return " ".join(lines_out)


def group_by_day(srt_dir):
    srt_dir = Path(srt_dir)
    files = [p for p in srt_dir.iterdir() if p.suffix.lower() == ".srt"]
    days = defaultdict(list)
    skipped = []
    for path in sorted(files):
        m = DATE_PATTERN.search(path.name)
        if not m:
            skipped.append(path.name)
            continue
        date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        timestamp = m.group(1) + m.group(2) + m.group(3) + m.group(4)
        days[date].append((timestamp, path))
    for date in days:
        days[date].sort(key=lambda x: x[0])
    return dict(sorted(days.items())), skipped


def summarize_day(date, transcript_text):
    prompt = f"""Ești un editor care ajută un motociclist să-și reconstituie amintirile dintr-o călătorie solo, din transcriptul brut al vlogurilor lui vorbite (dictate în cort/pe motor, cu ezitări și bâlbâieli).

Scrie un rezumat narativ, în română, al zilei de {date}, pe baza transcriptului de mai jos.

REGULI:
- Scop: reconstituirea memoriei — include TOATE detaliile concrete demne de reținut (locuri vizitate, distanțe, oameni întâlniți și ce au vorbit, decizii luate, incidente, gânduri/emoții exprimate), nu doar momentele "shareable" pentru montaj video.
- NU filtra pe potențial de conținut YouTube — asta e un rezumat personal, nu un plan de montaj.
- Curăță bâlbâielile și ezitările din transcript, dar NU parafraza replicile importante — citează-le exact între ghilimele acolo unde sunt formulări puternice sau semnificative.
- Scrie la persoana I, cronologic, ca o pagină de jurnal, în proză (nu bullet points).
- 150-400 cuvinte, în funcție de câtă substanță are ziua respectivă în transcript.

TRANSCRIPT BRUT ({date}):
{transcript_text}"""

    response = get_client().complete(
        "summary",
        [{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return response.text.strip()


def main():
    if len(sys.argv) < 2:
        print("Utilizare: python3 daily_summary_srt.py transcripts_raw/ [output.md]")
        sys.exit(1)

    srt_dir = Path(sys.argv[1]).expanduser().resolve()
    if not srt_dir.is_dir():
        print(f"[eroare] Folder negăsit: {srt_dir}")
        sys.exit(1)

    output_path = Path(sys.argv[2]).expanduser().resolve() if len(sys.argv) > 2 \
        else srt_dir.resolve().parent / "rezumat_pe_zile.md"

    days, skipped = group_by_day(srt_dir)
    if skipped:
        print(f"[atenție] {len(skipped)} fișiere fără dată recognoscibilă în nume, ignorate:")
        for f in skipped:
            print(f"  - {f}")

    if not days:
        print("[eroare] Niciun fișier .srt cu dată recognoscibilă în nume.")
        sys.exit(1)

    print(f"{len(days)} zile găsite: {', '.join(days.keys())}")

    try:
        sections = []
        for date, files in days.items():
            print(f"\nProcesez {date} ({len(files)} fișier{'e' if len(files) > 1 else ''})...")
            transcript_text = "\n".join(parse_srt(path) for _, path in files)
            if len(transcript_text) > BATCH_CHARS:
                print(f"  Transcript lung ({len(transcript_text)} chars) — trimit primele {BATCH_CHARS} chars")
                transcript_text = transcript_text[:BATCH_CHARS]
            summary = summarize_day(date, transcript_text)
            sections.append(f"## {date}\n\n{summary}\n")
            print("  ✓ rezumat generat")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("# Rezumat pe zile\n\n")
            f.write("\n".join(sections))

        print(f"\nSalvat: {output_path}")
    except (ConfigError, LLMError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(2)

    print(f"LLM cost: ${get_client().spent_usd:.4f}")


if __name__ == "__main__":
    main()
