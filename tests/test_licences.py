import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = [ROOT / "requirements.txt", ROOT / "requirements-dev.txt"]
LICENCES = ROOT / "docs" / "licences.md"
NOTICE = ROOT / "NOTICE.md"


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


def licence_rows():
    text = LICENCES.read_text(encoding="utf-8")
    section = text.split("## Python packages", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if cells and cells[0] and not set(cells[0]) <= set("-: "):
                rows.append(cells[0].lower())
    return rows


def test_every_requirement_has_a_licence_row():
    rows = licence_rows()
    missing = [name for name in requirement_names() if name not in rows]
    assert missing == [], f"requirements without a licence row: {missing}"


def test_notice_and_licences_exist():
    assert LICENCES.is_file()
    assert NOTICE.is_file()
