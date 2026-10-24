#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Corectează termenii greșiți dintr-un fișier .srt folosind o listă de corecturi.

Utilizare:
    python3 correct_srt.py input.srt
    python3 correct_srt.py input.srt output_corectat.srt
    python3 correct_srt.py input.srt output_corectat.srt --capitalised
    python3 correct_srt.py input.srt output_corectat.srt --json

Dacă nu specifici output, generează automat: input_corectat.srt

Fișierul de corecturi: corrections.txt (în același folder cu scriptul)
Format corrections.txt: gresit|corect  (câte o corecție per linie)
Un `*` la începutul părții greșite corectează și varianta cu majusculă
(doar prima literă): *gresit|corect repară și Gresit -> Corect.
Liniile care încep cu # sunt ignorate.
Dacă corrections.txt lipsește, se folosește corrections_example.txt (0 corecturi).
"""

import json
import sys
import time

from common.cli import handle_help
from common.corrections import (
    CORRECTIONS_EXAMPLE_FILE,
    CORRECTIONS_FILE,
    CorrectionsError,
    apply_corrections,
    count_changes,
    load_corrections,
    resolve_corrections_file,
)
from common.summary import summary_fields
from common.exit_codes import EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error


def main():
    started = time.monotonic()
    handle_help([
        "Utilizare: python3 correct_srt.py input.srt [output.srt] [--capitalised] [--json]",
        "Exemplu: python3 correct_srt.py raw.srt Clip_RO.srt --capitalised",
    ])
    json_out = "--json" in sys.argv[1:]
    try:
        run(json_out, started)
    except CorrectionsError as exc:
        sys.exit(report_error(exc, EXIT_USAGE, json_out, **summary_fields(started)))


def run(json_out=False, started=None):
    started = time.monotonic() if started is None else started
    stream = sys.stderr if json_out else sys.stdout
    positionals = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not positionals:
        print(
            "Utilizare: python3 correct_srt.py input.srt [output.srt] [--capitalised] [--json]",
            file=stream,
        )
        print(
            "Exemplu: python3 correct_srt.py raw.srt Clip_RO.srt --capitalised",
            file=stream,
        )
        sys.exit(EXIT_USAGE)

    input_file = positionals[0]
    output_file = positionals[1] if len(positionals) > 1 else input_file.replace(".srt", "_corectat.srt")
    capitalised = "--capitalised" in sys.argv[1:]

    corrections_path = resolve_corrections_file(CORRECTIONS_FILE, CORRECTIONS_EXAMPLE_FILE)
    corrections = load_corrections(corrections_path) if corrections_path else []
    print(f"Corecturi încărcate: {len(corrections)}", file=stream)

    with open(input_file, encoding="utf-8") as f:
        content = f.read()

    corrected = apply_corrections(content, corrections, capitalised=capitalised)

    changes = count_changes(content, corrections, capitalised=capitalised)
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
