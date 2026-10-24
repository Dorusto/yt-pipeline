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
    unnamed = [
        path.name
        for path in TOOLS
        if path.name not in skill_text and path.name not in tools_md
    ]
    assert unnamed == []
