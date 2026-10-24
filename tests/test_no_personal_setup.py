from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BANNED = (
    "~/Videos",
    "~/media",
    "/root/media",
    "~/projects",
    "~/Proiecte",
    "%h/media",
    "~/yt-watchdog",
    "Dorusto/resurse",
    "~/ws",
    '"name": "Doru"',
    '"name":"Doru"',
    "Monschau",
)

SCAN_DIRS = ("scripts", "prompts", ".claude/skills", "docs/guides", "docs/agents")
SCAN_FILES = ("README.md", "AGENTS.md", "CLAUDE.md", "docs/AGENT_SETUP.md", "docs/VISION.md", "config.yaml.example")
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".txt", ".json", ".example"}


def iter_files():
    for name in SCAN_DIRS:
        base = ROOT / name
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts:
                continue
            if path.suffix not in TEXT_SUFFIXES:
                continue
            yield path
    for name in SCAN_FILES:
        path = ROOT / name
        if path.is_file():
            yield path


def test_no_personal_setup_strings():
    hits = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for banned in BANNED:
                if banned in line:
                    hits.append(f"{path.relative_to(ROOT)}:{number}: {banned}")
    assert hits == [], "personal setup strings found:\n" + "\n".join(hits)
