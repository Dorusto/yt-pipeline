import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md"))
TOOLS = sorted((ROOT / "scripts").glob("*.py"))
TOKEN = re.compile(r"\b[a-z_][a-z0-9_]*\.py\b")


def test_skills_name_only_existing_tools():
    missing = []
    for skill in SKILLS:
        text = skill.read_text(encoding="utf-8")
        for token in TOKEN.findall(text):
            if not (ROOT / "scripts" / token).exists() and not (
                ROOT / "scripts" / "common" / token
            ).exists():
                missing.append(f"{skill.parent.name}: {token}")
    assert missing == []


def test_every_tool_is_named():
    skill_text = "\n".join(skill.read_text(encoding="utf-8") for skill in SKILLS)
    tools_md = (ROOT / "docs" / "guides" / "tools.md").read_text(encoding="utf-8")
    haystack = skill_text + "\n" + tools_md
    unnamed = [
        path.name
        for path in TOOLS
        if path.name not in haystack and f"yt {path.stem}" not in haystack
    ]
    assert unnamed == []


def test_rules_checked_has_one_channel_style_row():
    text = (ROOT / "docs" / "agents" / "rules-checked.md").read_text(encoding="utf-8")
    rows = [
        line
        for line in text.splitlines()
        if line.startswith("|") and "channel.style" in line
    ]
    assert len(rows) == 1
    assert "tests/test_config.py" in rows[0]
    assert "tests/test_metadata.py" in rows[0]
