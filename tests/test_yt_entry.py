import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
YT = ROOT / "scripts" / "yt.py"


def run(*args, env=None):
    return subprocess.run(
        [sys.executable, str(YT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_help_lists_tools():
    result = run("--help")
    assert result.returncode == 0
    output = result.stdout + result.stderr
    assert "usage" in output.lower()
    assert "doctor" in output


def test_tool_help_dispatches():
    result = run("doctor", "--help")
    assert result.returncode == 0
    assert "usage" in (result.stdout + result.stderr).lower()


def test_unknown_tool_is_usage_error():
    result = run("nosuchtool")
    assert result.returncode == 2
    assert "nosuchtool" in (result.stdout + result.stderr)


def test_no_arguments_is_usage_error():
    result = run()
    assert result.returncode == 2


def test_resolves_root_and_dispatches_to_stub(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "requirements.txt").write_text("", encoding="utf-8")
    (tmp_path / "scripts" / "echo_tool.py").write_text(
        "print('stub-ok')\n", encoding="utf-8"
    )
    env = {**os.environ, "YT_PIPELINE_HOME": str(tmp_path)}
    result = run("echo_tool", env=env)
    assert result.returncode == 0
    assert "stub-ok" in result.stdout


def test_json_info():
    result = run("--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["root"] == str(ROOT)
    assert "doctor" in payload["tools"]
    assert "wall_time_s" in payload
    assert "llm_cost_usd" in payload


def test_help_json_matches_json():
    plain = json.loads(run("--json").stdout)
    help_json = json.loads(run("--help", "--json").stdout)
    assert help_json["root"] == plain["root"]
    assert help_json["python"] == plain["python"]
    assert help_json["tools"] == plain["tools"]


def test_json_resolution_failure_is_structured(tmp_path):
    env = {**os.environ, "YT_PIPELINE_HOME": str(tmp_path / "missing")}
    result = run("--json", env=env)
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["exit_code"] == 2
    assert "error" in payload


def test_json_with_extra_argument_is_structured_usage_error():
    result = run("--json", "extra")
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["exit_code"] == 2


def test_unknown_tool_json_is_structured():
    result = run("nosuchtool", "--json")
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["exit_code"] == 2
    assert "nosuchtool" in payload["error"]


def test_bin_launcher():
    launcher = ROOT / "bin" / "yt"
    assert os.access(launcher, os.X_OK)
    result = subprocess.run(
        [str(launcher), "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "usage" in (result.stdout + result.stderr).lower()
