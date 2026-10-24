import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
HOOKS = ROOT / "hooks" / "hooks.json"

PLUGIN_ORDER = ["yt-director", "yt-publish", "yt-shorts", "yt-editor", "yt-admin"]

PLUGIN_SKILLS = {
    "yt-director": [
        "yt-recap",
        "yt-director",
        "yt-llm-director",
        "yt-beats",
        "yt-music-pick",
        "yt-edit-drop",
        "yt-render",
        "yt-thumbs",
        "yt-explain",
        "yt-ingest",
        "yt-index",
        "yt-describe",
        "yt-quality",
        "yt-similarity",
        "yt-eval-edit",
        "yt-mode-b",
        "yt-rough-cut",
        "yt-router",
    ],
    "yt-publish": [
        "yt-transcript",
        "yt-publish",
        "yt-recap-meta",
        "yt-thumbnail",
        "yt-audio-clean",
    ],
    "yt-shorts": ["yt-shorts", "yt-reframe"],
    "yt-editor": [
        "yt-filmcraft",
        "yt-export-kdenlive",
        "yt-finish-pip",
        "yt-graphics",
        "yt-matte",
        "yt-motion",
    ],
    "yt-admin": ["yt-costs", "yt-watchdog", "yt-eval"],
}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_marketplace_manifest():
    marketplace = load(MARKETPLACE)
    assert marketplace["name"] == "yt-pipeline"
    assert marketplace["owner"]["name"]
    plugins = marketplace["plugins"]
    assert [entry["name"] for entry in plugins] == PLUGIN_ORDER
    for entry in plugins:
        assert entry["source"] == "./"
        assert (ROOT / entry["source"]).resolve() == ROOT
        skills = entry["skills"]
        assert isinstance(skills, list) and skills
        for skill in skills:
            assert skill.startswith("./")
            resolved = (ROOT / skill).resolve()
            assert resolved.is_dir()
            assert (resolved / "SKILL.md").is_file()


def test_every_skill_in_exactly_one_plugin():
    marketplace = load(MARKETPLACE)
    mapping = {}
    for entry in marketplace["plugins"]:
        mapping[entry["name"]] = [
            Path(skill).name for skill in entry["skills"]
        ]
    seen = []
    for names in mapping.values():
        seen.extend(names)
    assert len(seen) == len(set(seen))
    on_disk = {
        path.parent.name
        for path in (ROOT / ".claude" / "skills").glob("*/SKILL.md")
    }
    assert set(seen) == on_disk
    assert mapping == PLUGIN_SKILLS


def test_no_root_plugin_manifest():
    assert not (ROOT / ".claude-plugin" / "plugin.json").exists()


def test_hooks_manifest():
    hooks = load(HOOKS)
    assert "hooks" in hooks
    assert isinstance(hooks["hooks"]["SessionStart"], list)
    assert hooks["hooks"]["SessionStart"]


def test_bin_yt_is_executable():
    assert os.access(ROOT / "bin" / "yt", os.X_OK)
