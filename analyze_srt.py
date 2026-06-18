#!/usr/bin/env python3
"""
Analizează un fișier .srt și generează:
  - Capitole YouTube
  - video_metadata.txt — titlu, descriere, tags pentru uploadul principal
  - shorts/ — fișiere video tăiate 1:1 + metadata.txt gata de upload

Utilizare:
    python3 analyze_srt.py subtitles.srt video.mp4

Necesită: DEEPSEEK_API_KEY în environment
Necesită: pip install openai
"""

import json
import os
import re
import sys
import unicodedata

from openai import OpenAI

BATCH_CHARS = 80000

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)


def parse_srt(path):
    with open(path, encoding="utf-8") as f:
        content = f.read()
    blocks = re.split(r"\n\n+", content.strip())
    segments = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) >= 3:
            start = lines[1].split(" --> ")[0].split(",")[0]
            segments.append({
                "start": start,
                "text": " ".join(lines[2:]),
            })
    return segments


def format_transcript(segments):
    return "\n".join(f"[{s['start']}] {s['text']}" for s in segments)


def analyze_with_deepseek(transcript_text):
    prompt = f"""Ești un editor video expert YouTube specializat în Shorts virale și optimizare SEO.

Analizează transcriptul și returnează un JSON cu trei chei: "video", "chapters", "shorts".

REGULI VIDEO PRINCIPAL:
- title: titlu clickbait dar corect, max 70 caractere, cuvântul cheie la început
- description: 3 rânduri de hook (ce câștigă spectatorul), NU pune capitolele aici
- tags: 8-10 taguri relevante, primul = cuvântul cheie principal

REGULI CAPITOLE:
- Identifică schimbările reale de subiect, nu orice pauză
- Primul capitol e mereu "00:00:00 Intro"
- Titluri scurte, descriptive (max 40 caractere)

REGULI SHORTS — CRITERII STRICTE:
- Durata: 45–90 secunde (verifică diferența dintre start și end)
- START: la începutul unei propoziții complete, nu în mijlocul unui cuvânt
- END: după o concluzie clară sau pauză naturală
- Conținut: un singur subiect, auto-conținut, nu necesită context anterior
- Hook: prima propoziție — afirmație puternică sau întrebare, NU "deci", "și", "dar"
- Pentru fiecare short: titlu, descriere scurtă (2 rânduri), 5 taguri, 3 hashtag-uri
- score: număr de la 1 la 10 — potențial viral (hook + claritate + unicitate)
- reason: 1 propoziție scurtă de ce merită tăiat

Returnează TOATE segmentele care îndeplinesc criteriile, rankat descrescător după score.
Returnează EXCLUSIV JSON valid, fără text în afara JSON-ului.

TRANSCRIPT (format [HH:MM:SS] text):
{transcript_text}

FORMAT RĂSPUNS:
{{
  "video": {{
    "title": "Titlu video principal",
    "description": "Rând 1 hook\\nRând 2 hook\\nRând 3 hook",
    "tags": ["tag1", "tag2", "tag3"]
  }},
  "chapters": [
    {{"timestamp": "00:00:00", "title": "Titlu capitol"}}
  ],
  "shorts": [
    {{
      "start": "00:05:23",
      "end": "00:06:45",
      "score": 9,
      "reason": "Demo unic pe care ChatGPT nu îl poate face — moment de wow clar",
      "title": "Titlu Short captivant (max 60 chars)",
      "hook": "Prima propoziție exactă din segment",
      "description": "Rând 1 descriere Short\\nRând 2 descriere Short",
      "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
      "hashtags": "#Shorts #hashtag1 #hashtag2"
    }}
  ]
}}"""

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


def make_output_dir(srt_file):
    srt_dir = os.path.dirname(os.path.abspath(srt_file))
    name = os.path.splitext(os.path.basename(srt_file))[0]
    out_dir = os.path.join(srt_dir, name)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def save_video_metadata(result, chapters, out_dir, basename):
    video = result.get("video", {})
    chapters_text = "\n".join(f"{ch['timestamp']} {ch['title']}" for ch in chapters)
    tags_text = ", ".join(video.get("tags", []))

    content = f"""=== TITLU ===
{video.get('title', '')}

=== DESCRIERE ===
{video.get('description', '')}

{chapters_text}

=== TAGS ===
{tags_text}
"""
    out = os.path.join(out_dir, f"{basename}_video_metadata.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(content)
    return out


def save_short_metadata(short, index, output_path, youtube_url):
    tags_text = ", ".join(short.get("tags", []))
    content = f"""=== TITLU ===
{short.get('title', '')}

=== DESCRIERE ===
{short.get('description', '')}

Clip complet: {youtube_url}

{short.get('hashtags', '#Shorts')}

=== TAGS ===
{tags_text}

=== INTERVAL DIN VIDEO ORIGINAL ===
{short['start']} → {short['end']}
"""
    meta_path = output_path.replace(".mp4", "_metadata.txt")
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(content)


def cut_shorts(selected, video_file, youtube_url, out_dir):
    shorts_dir = os.path.join(out_dir, "shorts")
    os.makedirs(shorts_dir, exist_ok=True)

    for i, s in enumerate(selected, 1):
        title_ascii = ''.join(
            c for c in unicodedata.normalize('NFD', s["title"])
            if unicodedata.category(c) != 'Mn'
        )
        safe_title = re.sub(r"[^\w\-]", "_", title_ascii)[:40]
        output = os.path.join(shorts_dir, f"short_{i:02d}_{safe_title}.mp4")
        cmd = (
            f'ffmpeg -y -i "{video_file}" -ss {s["start"]} -to {s["end"]} '
            f'-vf "crop=min(iw\\,ih):min(iw\\,ih),scale=1080:1080" '
            f'-c:v libx264 -crf 23 -preset ultrafast -c:a aac '
            f'"{output}"'
        )
        print(f"\n  Tai short {i}/{len(selected)}: {s['start']} → {s['end']}")
        ret = os.system(cmd)
        if ret == 0:
            save_short_metadata(s, i, output, youtube_url)
            print(f"  ✓ Salvat: {output}")
        else:
            print(f"  ✗ Eroare la short {i}")

    return shorts_dir


def ask_selection(shorts):
    print("\nCare vrei să tai? (ex: 1,3,5 | all | none): ", end="")
    raw = input().strip().lower()
    if raw == "none" or raw == "":
        return []
    if raw == "all":
        return shorts
    indices = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(shorts):
                indices.append(idx)
    return [shorts[i] for i in indices]


def main():
    if len(sys.argv) < 3:
        print("Utilizare: python3 analyze_srt.py subtitles.srt video.mp4 [https://youtu.be/...]")
        sys.exit(1)

    srt_file = sys.argv[1]
    video_file = sys.argv[2]
    youtube_url = sys.argv[3] if len(sys.argv) > 3 else "[LINK VIDEO PRINCIPAL]"

    basename = os.path.splitext(os.path.basename(srt_file))[0]
    out_dir = make_output_dir(srt_file)

    print(f"Parsez {srt_file}...")
    segments = parse_srt(srt_file)
    print(f"  {len(segments)} segmente găsite")

    transcript = format_transcript(segments)
    if len(transcript) > BATCH_CHARS:
        print(f"  Transcript lung ({len(transcript)} chars) — trimit primele {BATCH_CHARS} chars")
        transcript = transcript[:BATCH_CHARS]

    print("Trimit la DeepSeek pentru analiză...")
    result = analyze_with_deepseek(transcript)

    # --- Metadata video principal ---
    chapters = result.get("chapters", [])
    meta_file = save_video_metadata(result, chapters, out_dir, basename)
    print("\n" + "=" * 50)
    print(f"VIDEO METADATA → {meta_file}")
    print("=" * 50)
    video = result.get("video", {})
    print(f"Titlu : {video.get('title', '—')}")
    chapters_text = "\n".join(f"{ch['timestamp']} {ch['title']}" for ch in chapters)
    print(f"\nCapitole:\n{chapters_text}")

    # --- Shorts: afișează rankat, cere confirmare ---
    shorts = result.get("shorts", [])
    shorts_sorted = sorted(shorts, key=lambda x: x.get("score", 0), reverse=True)

    print(f"\n{'=' * 50}")
    print(f"SEGMENTE CANDIDATE SHORTS ({len(shorts_sorted)} găsite, rankat după potențial)")
    print("=" * 50)
    for i, s in enumerate(shorts_sorted, 1):
        print(f"\n[{i}] ★{s.get('score', '?')}/10  {s['start']} → {s['end']}")
        print(f"    {s['title']}")
        print(f"    Hook   : {s.get('hook', '—')}")
        print(f"    Motiv  : {s.get('reason', '—')}")

    output = os.path.join(out_dir, f"{basename}_analysis.json")
    with open(output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nAnaliză salvată în: {out_dir}/")

    # --- Confirmare tăiere ---
    selected = ask_selection(shorts_sorted)
    if not selected:
        print("Nicio tăiere. Gata.")
        return

    print(f"\n{'=' * 50}")
    print(f"TĂIERE {len(selected)} SHORTS")
    print("=" * 50)
    cut_shorts(selected, video_file, youtube_url, out_dir)
    print(f"\nShorts salvate în: {out_dir}/shorts/")


if __name__ == "__main__":
    main()
