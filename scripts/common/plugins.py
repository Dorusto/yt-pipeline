"""The plugin -> tools grouping, read from the marketplace and the skills."""

import json
import re
from pathlib import Path

MARKETPLACE_PATH = Path(".claude-plugin") / "marketplace.json"
SKILLS_DIR = Path(".claude") / "skills"
TOOL_CALL_RE = re.compile(r"\byt ([a-z][a-z0-9_]*)\b")


def load_marketplace(root):
    """Return the marketplace document under ``root``."""
    return json.loads((Path(root) / MARKETPLACE_PATH).read_text(encoding="utf-8"))


def plugin_names(root):
    """Return the plugin names in marketplace order."""
    return [entry["name"] for entry in load_marketplace(root)["plugins"]]


def plugin_skills(root, plugin):
    """Return the skill names of one plugin, in marketplace order."""
    for entry in load_marketplace(root)["plugins"]:
        if entry["name"] == plugin:
            return [Path(skill).name for skill in entry["skills"]]
    return []


def skill_tools(root, skill):
    """Return the tool names a skill names through ``yt <tool>``."""
    text = (Path(root) / SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
    return set(TOOL_CALL_RE.findall(text))


def plugin_tools(root, plugin):
    """Return the sorted tools of one plugin that exist in ``root/scripts``."""
    tools = set()
    for skill in plugin_skills(root, plugin):
        tools |= skill_tools(root, skill)
    scripts = Path(root) / "scripts"
    return sorted(name for name in tools if (scripts / f"{name}.py").is_file())
