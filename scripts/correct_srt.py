#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Corectează termenii greșiți dintr-un fișier .srt folosind o listă de corecturi.

Utilizare:
    python3 correct_srt.py input.srt
    python3 correct_srt.py input.srt output_corectat.srt

Dacă nu specifici output, generează automat: input_corectat.srt

Fișierul de corecturi: corrections.txt (în același folder cu scriptul)
Format corrections.txt: gresit|corect  (câte o corecție per linie)
Liniile care încep cu # sunt ignorate.
Dacă corrections.txt lipsește, se folosește corrections_example.txt (0 corecturi).
"""

import sys

from common.corrections import (
    CORRECTIONS_EXAMPLE_FILE,
    CORRECTIONS_FILE,
    CorrectionsError,
    apply_corrections,
    load_corrections,
    resolve_corrections_file,
)


def main():
    try:
        run()
    except CorrectionsError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(2)


def run():
    if len(sys.argv) < 2:
        print("Utilizare: python3 correct_srt.py input.srt [output.srt]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else input_file.replace(".srt", "_corectat.srt")

    corrections_path = resolve_corrections_file(CORRECTIONS_FILE, CORRECTIONS_EXAMPLE_FILE)
    corrections = load_corrections(corrections_path) if corrections_path else []
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
