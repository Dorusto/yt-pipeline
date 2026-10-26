import importlib.metadata
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = [ROOT / "requirements.txt", ROOT / "requirements-dev.txt"]
LICENCES = ROOT / "docs" / "licences.md"
NOTICE = ROOT / "NOTICE.md"

# Licence families: a table cell and the installed metadata must share one.
FAMILIES = {
    "apache": "apache",
    "mit": "mit",
    "bsd": "bsd",
    "gpl": "gpl",
    "lgpl": "lgpl",
    "hpnd": "hpnd",
    "isc": "isc",
    "mpl": "mpl",
    "psf": "python software foundation|psf",
    "ofl": "ofl|open font license",
}


def requirement_names():
    names = []
    for path in REQUIREMENTS:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            name = re.split(r"[<>=!~\[; ]", line, 1)[0].strip().lower()
            if name:
                names.append(name)
    return names


def _table_rows(section):
    rows = []
    header_seen = False
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells or not cells[0]:
            continue
        if set(cells[0]) <= set("-: "):
            continue
        if not header_seen:
            header_seen = True
            continue
        rows.append(cells)
    return rows


def _section(text, heading):
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def licence_rows():
    """Return {package name: licence cell} from the Python packages table."""
    text = LICENCES.read_text(encoding="utf-8")
    section = _section(text, "## Python packages")
    return {cells[0].lower(): cells[1] for cells in _table_rows(section)}


def _families(text):
    text = text.lower()
    return {
        family
        for family, pattern in FAMILIES.items()
        if re.search(rf"\b(?:{pattern})\b", text)
    }


def _metadata_licences(metadata):
    values = []
    for key in ("License-Expression", "License"):
        value = metadata.get(key)
        if value:
            values.append(value)
    values.extend(
        c for c in (metadata.get_all("Classifier") or []) if c.startswith("License ::")
    )
    return values


def test_every_requirement_has_a_licence_row():
    rows = licence_rows()
    missing = [name for name in requirement_names() if name not in rows]
    assert missing == [], f"requirements without a licence row: {missing}"


@pytest.mark.parametrize("name", requirement_names())
def test_requirement_licence_matches_installed_metadata(name):
    try:
        dist = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        pytest.skip(f"{name} is not installed")
    declared = _metadata_licences(dist.metadata)
    if not declared:
        pytest.skip(f"{name} declares no licence in its installed metadata")
    row = licence_rows()[name]
    assert _families(row) & _families(" ".join(declared)), (
        f"{name}: docs/licences.md says {row!r}, installed metadata says {declared!r}"
    )


def test_models_and_tools_rows_have_a_source_link():
    text = LICENCES.read_text(encoding="utf-8")
    missing = []
    for heading in ("## External programs", "## Models and weights"):
        for cells in _table_rows(_section(text, heading)):
            if len(cells) < 4 or "http" not in cells[-1]:
                missing.append(f"{heading}: {cells[0]}")
    assert missing == [], f"rows without a source link: {missing}"


def test_notice_and_licences_exist():
    assert LICENCES.is_file()
    assert NOTICE.is_file()
