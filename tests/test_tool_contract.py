import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = sorted((ROOT / "scripts").glob("*.py"))


@pytest.fixture(scope="module")
def help_output():
    cache = {}
    for path in TOOLS:
        result = subprocess.run(
            [sys.executable, str(path), "--help"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        cache[path] = (result.returncode, result.stdout + result.stderr)
    return cache


def test_every_tool_answers_help(help_output):
    for path, (returncode, output) in help_output.items():
        assert returncode == 0, f"{path.name} --help exited {returncode}"
        lowered = output.lower()
        assert "usage" in lowered or "utilizare" in lowered, f"{path.name} --help has no usage line"


def test_every_json_tool_reports_summary_fields(help_output):
    for path, (_, output) in help_output.items():
        if "--json" not in output:
            continue
        source = path.read_text(encoding="utf-8")
        assert "summary_fields" in source or (
            "wall_time_s" in source and "llm_cost_usd" in source
        ), f"{path.name} has --json but no summary fields"
