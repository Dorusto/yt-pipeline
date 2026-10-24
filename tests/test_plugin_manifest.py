import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
HOOKS = ROOT / "hooks" / "hooks.json"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_manifest():
    manifest = load(PLUGIN)
    assert manifest["name"] == "yt-pipeline"
    assert manifest["license"] == "AGPL-3.0-or-later"
    assert isinstance(manifest["version"], str) and manifest["version"]
    assert isinstance(manifest["description"], str) and manifest["description"]
    assert manifest["author"]["name"]
    skills = manifest["skills"]
    assert isinstance(skills, list) and skills
    for entry in skills:
        assert entry.startswith("./")
        resolved = (ROOT / entry).resolve()
        assert resolved.is_dir()
        assert any(resolved.glob("*/SKILL.md"))


def test_marketplace_manifest():
    marketplace = load(MARKETPLACE)
    assert marketplace["name"] == "yt-pipeline"
    assert marketplace["owner"]["name"]
    plugins = marketplace["plugins"]
    assert isinstance(plugins, list) and plugins
    first = plugins[0]
    assert first["name"] == "yt-pipeline"
    assert first["source"] == "./"
    assert (ROOT / first["source"]).resolve() == ROOT


def test_hooks_manifest():
    hooks = load(HOOKS)
    assert "hooks" in hooks
    assert isinstance(hooks["hooks"]["SessionStart"], list)
    assert hooks["hooks"]["SessionStart"]


def test_bin_yt_is_executable():
    assert os.access(ROOT / "bin" / "yt", os.X_OK)


def test_plugin_name_is_kebab_case():
    name = load(PLUGIN)["name"]
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name)
