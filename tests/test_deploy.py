import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def run_update(*args):
    return subprocess.run(
        ["bash", str(ROOT / "deploy" / "update.sh"), *args],
        capture_output=True, text=True, cwd=ROOT,
    )


def test_update_dry_run_prints_steps():
    result = run_update("v0.1.0", "--dry-run")
    assert result.returncode == 0
    out = result.stdout.lower()
    for step in ("fetch", "checkout", "install", "restart", "smoke"):
        assert step in out


def test_update_requires_tag():
    result = run_update()
    assert result.returncode == 2


def test_service_unit_uses_environment_file():
    text = (ROOT / "deploy" / "yt-pipeline-web.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=/etc/yt-pipeline/env" in text
    assert "scripts/serve.py" in text


def test_pilot_host_guide_has_placeholders_and_limits():
    text = (ROOT / "docs" / "guides" / "pilot-host.md").read_text(encoding="utf-8")
    for token in ("<container>", "<nas-share>", "<tag>"):
        assert token in text
    for topic in ("quota", "credit", "project_totals_url"):
        assert topic in text


def test_deploy_workflow_triggers_and_runner():
    text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    triggers = data.get("on", data.get(True))
    assert set(triggers) == {"release", "workflow_dispatch"}
    assert "pull_request" not in triggers
    assert triggers["release"]["types"] == ["published"]
    job = data["jobs"]["deploy"]
    assert "PILOT_RUNNERS" in str(job["strategy"]["matrix"]["runner"])
    runs_on = job["runs-on"]
    assert "self-hosted" in runs_on
    assert any("matrix.runner" in str(item) for item in runs_on)
    runs = [step.get("run", "") for step in job["steps"]]
    assert any("deploy/update.sh" in run for run in runs)
    assert job["concurrency"]["cancel-in-progress"] is False
    assert "matrix.runner" in job["concurrency"]["group"]
