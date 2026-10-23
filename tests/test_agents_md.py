from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
CLAUDE = ROOT / "CLAUDE.md"
TOPICS = (
    ROOT / "docs" / "agents" / "code-style.md",
    ROOT / "docs" / "agents" / "testing.md",
    ROOT / "docs" / "agents" / "conventions.md",
)


def test_agents_md_points_to_the_rules_skills_and_topics():
    text = AGENTS.read_text(encoding="utf-8")
    assert "docs/RULES.md" in text
    assert ".claude/skills/" in text
    for topic in TOPICS:
        assert topic.name in text


def test_agents_md_is_short():
    assert len(AGENTS.read_text(encoding="utf-8").splitlines()) <= 60


def test_claude_md_points_to_agents_md():
    text = CLAUDE.read_text(encoding="utf-8")
    assert "AGENTS.md" in text
    assert len(text.splitlines()) <= 10


def test_topic_files_exist_and_point_to_rules():
    for topic in TOPICS:
        assert topic.is_file()
        assert "RULES.md" in topic.read_text(encoding="utf-8")
