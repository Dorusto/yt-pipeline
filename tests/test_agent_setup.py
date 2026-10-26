import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "AGENT_SETUP.md"
SKILLS = sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md"))

BACKTICKED = re.compile(r"`(yt-[a-z0-9-]+)`")
NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
DESCRIPTION_RE = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)
NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

PLUGIN_NAMES = {
    "yt-pipeline",
    "yt-director",
    "yt-publish",
    "yt-shorts",
    "yt-editor",
    "yt-admin",
}


def skill_names():
    return {path.parent.name for path in SKILLS}


def test_guide_lists_every_skill():
    text = GUIDE.read_text(encoding="utf-8")
    named = set(BACKTICKED.findall(text))
    assert skill_names() <= named


def test_guide_names_no_unknown_skill():
    text = GUIDE.read_text(encoding="utf-8")
    named = set(BACKTICKED.findall(text))
    assert named - skill_names() <= PLUGIN_NAMES


def test_skills_have_valid_frontmatter():
    for skill in SKILLS:
        text = skill.read_text(encoding="utf-8")
        assert text.startswith("---")
        block = text.split("---", 2)[1]
        name = NAME_RE.search(block)
        assert name, skill
        assert name.group(1) == skill.parent.name
        assert NAME_PATTERN.fullmatch(name.group(1))
        description = DESCRIPTION_RE.search(block)
        assert description, skill
        assert 1 <= len(description.group(1)) <= 1024


def test_skills_call_tools_through_yt():
    offenders = [
        skill.parent.name
        for skill in SKILLS
        if ".venv/bin/python" in skill.read_text(encoding="utf-8")
    ]
    assert offenders == []
