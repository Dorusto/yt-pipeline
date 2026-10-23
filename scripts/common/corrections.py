"""Find/replace corrections for transcript text (the publish chain's ``corrections.txt``).

Format: one ``wrong|correct`` pair per line, ``#`` lines ignored; the replace is
plain, case-sensitive text substitution over the whole file. A leading ``*`` on
the wrong side also fixes the capitalised variant (first letter only), off by
default. ``correct_srt.py`` applies the list to an .srt and
``shorts_generator.py`` applies it to the word text of a timeline
``_words.json``. A missing corrections file falls back to
``corrections_example.txt`` (no corrections).
"""

import sys
from collections import namedtuple
from pathlib import Path

CORRECTIONS_FILE = Path(__file__).resolve().parents[1] / "corrections.txt"
CORRECTIONS_EXAMPLE_FILE = Path(__file__).resolve().parents[1] / "corrections_example.txt"

Correction = namedtuple("Correction", "wrong correct capitalised")


class CorrectionsError(Exception):
    """A user-facing error: the corrections file cannot be read."""


def load_corrections(path):
    """Return the ``[Correction]`` entries of a corrections file."""
    corrections = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("|", 1)
                if len(parts) == 2:
                    wrong, correct = parts
                    capitalised = wrong.startswith("*")
                    if capitalised:
                        wrong = wrong[1:]
                    corrections.append(Correction(wrong, correct, capitalised))
    except FileNotFoundError:
        raise CorrectionsError(f"cannot read corrections '{path}': file not found")
    except OSError as exc:
        raise CorrectionsError(f"cannot read corrections '{path}': {exc}")
    return corrections


def resolve_corrections_file(path, example=None):
    """Return the corrections file to read, falling back to the example when missing."""
    path = Path(path)
    if path.exists():
        return path
    example = Path(example) if example is not None else CORRECTIONS_EXAMPLE_FILE
    print(
        f"[warning] Fișierul de corecturi lipsește: {path}. "
        f"Folosesc exemplul: {example}",
        file=sys.stderr,
    )
    if example.exists():
        return example
    return None


def apply_corrections(text, corrections, capitalised=False):
    """Apply every ``Correction`` to ``text`` in order, optionally fixing capitalised variants."""
    for correction in corrections:
        text = text.replace(correction.wrong, correction.correct)
        if (capitalised or correction.capitalised) and correction.wrong[:1].islower():
            cap_wrong = correction.wrong[0].upper() + correction.wrong[1:]
            cap_correct = correction.correct[:1].upper() + correction.correct[1:]
            text = text.replace(cap_wrong, cap_correct)
    return text
