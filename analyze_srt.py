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

import yaml
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


def make_output_dir(video_file):
    return os.path.dirname(os.path.abspath(video_file))


def parse_time_secs(t):
    t = t.replace(",", ".")
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def extract_segment_transcript(srt_path, start_str, end_str):
    start = parse_time_secs(start_str)
    end = parse_time_secs(end_str)
    with open(srt_path, encoding="utf-8") as f:
        content = f.read()
    blocks = re.split(r"\n\n+", content.strip())
    lines_out = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        ts = lines[1].split(" --> ")
        ev_start = parse_time_secs(ts[0].strip())
        ev_end = parse_time_secs(ts[1].strip())
        if ev_end <= start or ev_start >= end:
            continue
        lines_out.append(" ".join(lines[2:]))
    return " ".join(lines_out)


def analyze_short_with_deepseek(transcript, segment_name, start, end):
    prompt = f"""Ești un editor YouTube expert în Shorts virale.

Generează metadata pentru un Short YouTube bazat pe transcriptul de mai jos.
Numele segmentului: {segment_name} ({start} → {end})

Returnează EXCLUSIV JSON valid:
{{
  "title": "titlu captivant max 60 caractere",
  "description": "rând 1 hook\\nrând 2 context sau call to action",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "hashtags": "#Shorts #hashtag1 #hashtag2"
}}

TRANSCRIPT:
{transcript}"""

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


def generate_shorts_metadata(srt_file, config_path, shorts_dir, youtube_url):
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    segments = config.get("segments", [])
    os.makedirs(shorts_dir, exist_ok=True)
    for i, seg in enumerate(segments, 1):
        name = seg["name"]
        start = seg["start"]
        end = seg["end"]
        print(f"  [{i}/{len(segments)}] {name}  ({start} → {end})")
        transcript = extract_segment_transcript(srt_file, start, end)
        meta = analyze_short_with_deepseek(transcript, name, start, end)
        tags_text = ", ".join(meta.get("tags", []))
        content = f"""=== TITLU ===
{meta.get('title', '')}

=== DESCRIERE ===
{meta.get('description', '')}

Clip complet: {youtube_url}

{meta.get('hashtags', '#Shorts')}

=== TAGS ===
{tags_text}

=== INTERVAL DIN VIDEO ORIGINAL ===
{start} → {end}
"""
        out = os.path.join(shorts_dir, f"{name}_metadata.txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"     ✓ {os.path.basename(out)}")
        print(f"     Titlu: {meta.get('title', '—')}")


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
        print("Utilizare: python3 analyze_srt.py subtitles.srt video.mp4 [youtube_url] [--shorts-config config.yaml]")
        sys.exit(1)

    srt_file = os.path.abspath(os.path.expanduser(sys.argv[1]))
    video_file = os.path.abspath(os.path.expanduser(sys.argv[2]))

    for label, path in [("SRT", srt_file), ("Video", video_file)]:
        if not os.path.exists(path):
            print(f"[eroare] {label} negăsit: {path}")
            print("Folosește căi absolute (ex: ~/Videos/Lenea/Export/subtitles/Lenea_RO.srt)")
            sys.exit(1)

    args = sys.argv[3:]
    youtube_url = "[LINK VIDEO PRINCIPAL]"
    shorts_config_path = None
    i = 0
    while i < len(args):
        if args[i] == "--shorts-config" and i + 1 < len(args):
            shorts_config_path = os.path.abspath(os.path.expanduser(args[i + 1]))
            i += 2
        elif not args[i].startswith("--"):
            youtube_url = args[i]
            i += 1
        else:
            i += 1

    out_dir = make_output_dir(video_file)
    shorts_dir = os.path.join(out_dir, "shorts")

    # --- Metadata per short din config ---
    if shorts_config_path:
        print(f"\nGenerez metadata per short din {shorts_config_path}...")
        generate_shorts_metadata(srt_file, shorts_config_path, shorts_dir, youtube_url)
        print(f"\nMetadata shorturi salvate în: {shorts_dir}/")
        return

    basename = os.path.splitext(os.path.basename(srt_file))[0]

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

    # --- Shorts candidate: afișează rankat + salvează ---
    shorts = result.get("shorts", [])
    shorts_sorted = sorted(shorts, key=lambda x: x.get("score", 0), reverse=True)

    print(f"\n{'=' * 50}")
    print(f"SEGMENTE CANDIDATE SHORTS ({len(shorts_sorted)} găsite, rankat după potențial)")
    print("=" * 50)
    lines = []
    for i, s in enumerate(shorts_sorted, 1):
        print(f"\n[{i}] ★{s.get('score', '?')}/10  {s['start']} → {s['end']}")
        print(f"    {s['title']}")
        print(f"    Hook   : {s.get('hook', '—')}")
        print(f"    Motiv  : {s.get('reason', '—')}")
        lines.append(f"[{i}] ★{s.get('score', '?')}/10  {s['start']} → {s['end']}")
        lines.append(f"    {s['title']}")
        lines.append(f"    Hook   : {s.get('hook', '—')}")
        lines.append(f"    Motiv  : {s.get('reason', '—')}")
        lines.append("")

    candidates_file = os.path.join(out_dir, f"{basename}_shorts_candidates.txt")
    with open(candidates_file, "w", encoding="utf-8") as f:
        f.write(f"SHORTURI CANDIDATE — {basename}\n")
        f.write("=" * 50 + "\n\n")
        f.write("\n".join(lines))
    print(f"\nCandidați salvați → {os.path.basename(candidates_file)}")

    # --- Confirmare tăiere ---
    try:
        selected = ask_selection(shorts_sorted)
    except EOFError:
        print("Mod non-interactiv — nicio tăiere. Gata.")
        return
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
