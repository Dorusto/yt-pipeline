import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "close_milestone_issues.py"


def test_dry_run_on_fake_list(tmp_path):
    issues_file = tmp_path / "issues.json"
    issues_file.write_text(
        json.dumps([{"number": 7, "title": "a"}, {"number": 9, "title": "b"}]),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--tag", "v0.3.0", "--repo",
         "Dorusto/yt-pipeline", "--dry-run", "--issues-file", str(issues_file)],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0
    assert "#7" in result.stdout
    assert "#9" in result.stdout
    assert ("Shipped in v0.3.0: "
            "https://github.com/Dorusto/yt-pipeline/releases/tag/v0.3.0") in result.stdout
    assert "milestone" in result.stdout


def test_missing_tag_is_usage_error():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 2


def test_release_workflow_closes_milestone():
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["permissions"]["issues"] == "write"
    runs = [step.get("run", "") for step in data["jobs"]["release"]["steps"]]
    assert any(
        ".github/scripts/close_milestone_issues.py" in run and "--tag" in run
        for run in runs
    )


def test_guide_mentions_milestone_close():
    text = (ROOT / "docs" / "guides" / "pilot-host.md").read_text(encoding="utf-8")
    assert "milestone" in text.lower()
