import json
import re
import shutil
import types
from pathlib import Path

import pytest

import doctor


EXPECTED_NAMES = [
    "python_deps",
    "ffmpeg",
    "ffprobe",
    "config",
    "models",
    "openrouter_api_key",
    "disk_space",
    "workspace_writable",
    "workspace_outside_source",
    "originals_read_only",
]


def clean_config():
    return {
        "models": {
            "vision": "z-ai/glm-5.3-flash",
            "director": "deepseek/deepseek-v4.1-flash",
        },
        "llm_backend": "openrouter",
        "llm_budget_usd": 5.0,
    }


def patch_config(monkeypatch, config, filename="config.yaml"):
    monkeypatch.setattr(
        doctor, "load_project_config", lambda: (config, Path(filename))
    )


@pytest.fixture(autouse=True)
def clean_project(monkeypatch):
    patch_config(monkeypatch, clean_config())
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-fake-for-tests")


def run_main(capsys, argv):
    code = doctor.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def find_check(summary, name):
    return next(check for check in summary["checks"] if check["name"] == name)


def test_all_checks_present_in_order(capsys):
    code, out, err = run_main(capsys, ["--json"])
    summary = json.loads(out)
    assert [check["name"] for check in summary["checks"]] == EXPECTED_NAMES

    code, out, err = run_main(capsys, [])
    names = [
        re.match(r"\[(\w+)\s*\]\s+(\S+)", line).group(2)
        for line in out.splitlines()
        if line.startswith("[")
    ]
    assert names == EXPECTED_NAMES


def test_missing_api_key_fails(monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    code, out, err = run_main(capsys, ["--json"])
    summary = json.loads(out)
    check = find_check(summary, "openrouter_api_key")
    assert code == 1
    assert summary["ok"] is False
    assert check["status"] == "fail"
    assert "OPENROUTER_API_KEY" in check["fix"]


def test_api_key_never_printed(monkeypatch, capsys):
    secret = "sk-or-v1-supersecret123"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    for argv in (["--json"], []):
        code, out, err = run_main(capsys, argv)
        assert code == 0
        assert secret not in out
        assert secret not in err


def test_mock_backend_needs_no_key(monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = clean_config()
    config["llm_backend"] = "mock"
    patch_config(monkeypatch, config)
    code, out, err = run_main(capsys, ["--json"])
    check = find_check(json.loads(out), "openrouter_api_key")
    assert check["status"] == "ok"
    assert "mock" in check["detail"]


def test_missing_model_role_fails(monkeypatch, capsys):
    config = clean_config()
    del config["models"]["director"]
    patch_config(monkeypatch, config)
    code, out, err = run_main(capsys, ["--json"])
    check = find_check(json.loads(out), "models")
    assert check["status"] == "fail"
    assert "director" in check["fix"]


def test_workspace_inside_source_fails(capsys, tmp_path):
    source = tmp_path / "raw"
    source.mkdir()
    workspace = source / "ws"
    code, out, err = run_main(
        capsys,
        ["--source", str(source), "--workspace", str(workspace), "--json"],
    )
    summary = json.loads(out)
    assert code == 1
    assert find_check(summary, "workspace_outside_source")["status"] == "fail"


def test_originals_writable_warns_and_read_only_ok(capsys, tmp_path):
    source = tmp_path / "raw"
    source.mkdir()
    code, out, err = run_main(capsys, ["--source", str(source), "--json"])
    summary = json.loads(out)
    assert find_check(summary, "originals_read_only")["status"] == "warn"
    assert code == 0

    source.chmod(0o555)
    try:
        code, out, err = run_main(capsys, ["--source", str(source), "--json"])
        summary = json.loads(out)
        assert find_check(summary, "originals_read_only")["status"] == "ok"
    finally:
        source.chmod(0o755)


def test_disk_space_thresholds(monkeypatch, capsys):
    state = {"free": 0}

    def fake_disk_usage(path):
        return types.SimpleNamespace(total=0, used=0, free=state["free"])

    monkeypatch.setattr(shutil, "disk_usage", fake_disk_usage)
    for free_gb, expected in ((1, "fail"), (3, "warn"), (100, "ok")):
        state["free"] = free_gb * 1024 ** 3
        code, out, err = run_main(capsys, ["--json"])
        assert find_check(json.loads(out), "disk_space")["status"] == expected


def test_workspace_writable_probe(capsys, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    bad_workspace = blocker / "ws"
    code, out, err = run_main(capsys, ["--workspace", str(bad_workspace), "--json"])
    summary = json.loads(out)
    assert find_check(summary, "workspace_writable")["status"] == "fail"

    good_workspace = tmp_path / "ws"
    code, out, err = run_main(capsys, ["--workspace", str(good_workspace), "--json"])
    summary = json.loads(out)
    assert find_check(summary, "workspace_writable")["status"] == "ok"
    assert not (good_workspace / ".doctor-probe").exists()


def test_json_summary_fields(capsys):
    code, out, err = run_main(capsys, ["--json"])
    summary = json.loads(out)
    assert summary["tool"] == "scripts/doctor.py"
    assert isinstance(summary["ok"], bool)
    assert isinstance(summary["checks"], list) and summary["checks"]
    assert isinstance(summary["fixes"], list)
    assert isinstance(summary["wall_time_s"], (int, float))
    assert summary["llm_cost_usd"] == 0.0


def test_all_good_prints_all_good(monkeypatch, capsys):
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda path: types.SimpleNamespace(total=0, used=0, free=100 * 1024 ** 3),
    )
    code, out, err = run_main(capsys, [])
    assert code == 0
    assert out.rstrip().splitlines()[-1] == "all good"
