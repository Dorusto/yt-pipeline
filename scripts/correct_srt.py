#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Corectează termenii greșiți dintr-un fișier .srt folosind o listă de corecturi.

Utilizare:
    python3 correct_srt.py input.srt
    python3 correct_srt.py input.srt output_corectat.srt
    python3 correct_srt.py input.srt output_corectat.srt --json

Dacă nu specifici output, generează automat: input_corectat.srt

Fișierul de corecturi: corrections.txt (în același folder cu scriptul)
Format corrections.txt: gresit|corect  (câte o corecție per linie)
Liniile care încep cu # sunt ignorate.
Dacă corrections.txt lipsește, se folosește corrections_example.txt (0 corecturi).
"""

import json
import sys
import time

from common.corrections import (
    CORRECTIONS_EXAMPLE_FILE,
    CORRECTIONS_FILE,
    CorrectionsError,
    apply_corrections,
    load_corrections,
    resolve_corrections_file,
)
from common.summary import summary_fields


def main():
    started = time.monotonic()
    json_out = "--json" in sys.argv[1:]
    try:
        run(json_out, started)
    except CorrectionsError as exc:
        if json_out:
            print(json.dumps({"ok": False, "error": str(exc), **summary_fields(started)}))
        else:
            print(f"[error] {exc}", file=sys.stderr)
        sys.exit(2)


def run(json_out=False, started=None):
    started = time.monotonic() if started is None else started
    stream = sys.stderr if json_out else sys.stdout
    positionals = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not positionals:
        print("Utilizare: python3 correct_srt.py input.srt [output.srt] [--json]", file=stream)
        sys.exit(1)

    input_file = positionals[0]
    output_file = positionals[1] if len(positionals) > 1 else input_file.replace(".srt", "_corectat.srt")

    corrections_path = resolve_corrections_file(CORRECTIONS_FILE, CORRECTIONS_EXAMPLE_FILE)
    corrections = load_corrections(corrections_path) if corrections_path else []
    print(f"Corecturi încărcate: {len(corrections)}", file=stream)

    with open(input_file, encoding="utf-8") as f:
        content = f.read()

    corrected = apply_corrections(content, corrections)

    changes = sum(1 for wrong, _ in corrections if wrong in content)
    print(f"Termeni înlocuiți: {changes}", file=stream)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(corrected)

    print(f"Salvat: {output_file}", file=stream)
    if json_out:
        print(json.dumps({
            "ok": True,
            "output": str(output_file),
            "corrections": len(corrections),
            "changes": changes,
            **summary_fields(started),
        }))


if __name__ == "__main__":
    main()
