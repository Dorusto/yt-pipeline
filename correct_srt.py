#!/usr/bin/env python3
"""
Corectează termenii greșiți dintr-un fișier .srt folosind o listă de corecturi.

Utilizare:
    python3 correct_srt.py input.srt
    python3 correct_srt.py input.srt output_corectat.srt

Dacă nu specifici output, generează automat: input_corectat.srt

Fișierul de corecturi: corrections.txt (în același folder cu scriptul)
Format corrections.txt: gresit|corect  (câte o corecție per linie)
Liniile care încep cu # sunt ignorate.
"""

import sys
import re
from pathlib import Path

CORRECTIONS_FILE = Path(__file__).parent / "corrections.txt"


def load_corrections(path: Path) -> list[tuple[str, str]]:
    corrections = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|", 1)
            if len(parts) == 2:
                corrections.append((parts[0], parts[1]))
    return corrections


def apply_corrections(text: str, corrections: list[tuple[str, str]]) -> str:
    for wrong, correct in corrections:
        text = text.replace(wrong, correct)
    return text


def main():
    if len(sys.argv) < 2:
        print("Utilizare: python3 correct_srt.py input.srt [output.srt]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else input_file.replace(".srt", "_corectat.srt")

    corrections = load_corrections(CORRECTIONS_FILE)
    print(f"Corecturi încărcate: {len(corrections)}")

    with open(input_file, encoding="utf-8") as f:
        content = f.read()

    corrected = apply_corrections(content, corrections)

    changes = sum(1 for wrong, _ in corrections if wrong in content)
    print(f"Termeni înlocuiți: {changes}")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(corrected)

    print(f"Salvat: {output_file}")


if __name__ == "__main__":
    main()
