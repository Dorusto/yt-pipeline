import subprocess
from pathlib import Path

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
