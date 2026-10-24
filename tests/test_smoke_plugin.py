import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "smoke_plugin.sh"
GUIDE = ROOT / "docs" / "AGENT_SETUP.md"


def run_script(*args):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True, text=True, cwd=ROOT,
    )


def test_script_is_executable():
    assert os.access(SCRIPT, os.X_OK)


def test_help_exits_zero():
    result = run_script("--help")
    assert result.returncode == 0
    assert "Usage" in result.stdout


def test_dry_run_prints_steps():
    result = run_script("--dry-run")
    assert result.returncode == 0
    out = result.stdout.lower()
    for step in ("clone", "venv", "install", "plugin", "recap"):
        assert step in out


def test_missing_source_is_usage_error():
    result = run_script()
    assert result.returncode == 2


def test_guide_has_smoke_section():
    text = GUIDE.read_text(encoding="utf-8")
    assert "Fresh-container smoke test" in text
    assert "deploy/smoke_plugin.sh" in text
