from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".github" / "dependabot.yml"


def load():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_dependabot_config_exists_and_is_version_2():
    assert CONFIG.is_file()
    assert load()["version"] == 2


def test_dependabot_covers_pip_and_github_actions():
    ecosystems = {update["package-ecosystem"] for update in load()["updates"]}
    assert ecosystems == {"pip", "github-actions"}


def test_dependabot_is_weekly_grouped_and_in_working_hours():
    for update in load()["updates"]:
        schedule = update["schedule"]
        assert schedule["interval"] == "weekly"
        assert schedule["day"] == "monday"
        assert schedule["time"] == "09:00"
        assert schedule["timezone"] == "Europe/Bucharest"
        assert update["directory"] == "/"
        groups = update["groups"]
        assert groups, "each ecosystem needs one group"
        for group in groups.values():
            assert group["patterns"] == ["*"]
