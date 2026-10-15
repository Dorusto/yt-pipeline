import json
import subprocess
import sys
from pathlib import Path

import eval_edit

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_edit.py"


def write_edit(workspace, slots):
    workspace.mkdir(parents=True, exist_ok=True)
    edit = {
        "schema_version": 1,
        "generated_at": "2026-10-15T19:00:00+0000",
        "brief": "test brief",
        "sources": [],
        "video": slots,
    }
    path = workspace / "edit.json"
    path.write_text(json.dumps(edit), encoding="utf-8")
    return path


def write_expect(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def run_eval(workspace, expect, *flags):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--workspace", str(workspace), "--expect", str(expect), *flags],
        capture_output=True,
        text=True,
    )


def video_slot(key, in_s, out_s, **extra):
    slot = {"id": key or "v", "clip": "clip.mp4", "in_s": in_s, "out_s": out_s, "score": 0.9, "reason": "test"}
    slot.update(extra)
    if key:
        slot["shot_key"] = key
    return slot


def photo_slot(key=None, duration_s=3.0, **extra):
    slot = {"id": key or "photo_01", "kind": "photo", "photo": "shot.jpg", "duration_s": duration_s, "score": 0.8, "reason": "test"}
    slot.update(extra)
    if key:
        slot["shot_key"] = key
    return slot


def test_help_exits_0():
    result = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "--expect" in result.stdout
    assert "--json" in result.stdout


def test_pass_when_every_expectation_holds(tmp_path):
    workspace = tmp_path / "ws"
    write_edit(
        workspace,
        [
            video_slot("0072/s5", 0.0, 30.0),
            video_slot("0072/s7b1", 100.0, 130.0),
        ],
    )
    expect = write_expect(
        tmp_path / "expect.yaml",
        'must_include:\n  - any_of: ["0072/s5"]\n    note: "the tunnel approach"\n'
        '  - any_of: ["0072/s7b1", "0072/s7b5"]\n    note: "one of the cave-exit heroes"\n'
        "length_s:\n  target: 60\n  tolerance: 3\n",
    )
    result = run_eval(workspace, expect, "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] is True
    assert [check["passed"] for check in report["checks"]] == [True, True, True]
    assert report["checks"][0]["found"] == ["0072/s5"]
    assert report["checks"][1]["found"] == ["0072/s7b1"]


def test_fail_when_a_shot_is_missing(tmp_path):
    workspace = tmp_path / "ws"
    write_edit(workspace, [video_slot("0072/s7b1", 0.0, 60.0)])
    expect = write_expect(
        tmp_path / "expect.yaml",
        'must_include:\n  - any_of: ["0072/s5"]\n    note: "the tunnel approach"\n',
    )
    result = run_eval(workspace, expect, "--json")
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["passed"] is False
    assert report["checks"][0]["passed"] is False
    assert report["checks"][0]["found"] == []


def test_any_of_passes_with_the_other_key(tmp_path):
    workspace = tmp_path / "ws"
    write_edit(workspace, [video_slot("0072/s7b5", 0.0, 60.0)])
    expect = write_expect(
        tmp_path / "expect.yaml",
        'must_include:\n  - any_of: ["0072/s7b1", "0072/s7b5"]\n    note: "one of the cave-exit heroes"\n',
    )
    result = run_eval(workspace, expect, "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] is True
    assert report["checks"][0]["found"] == ["0072/s7b5"]


def test_length_fail(tmp_path):
    expect = write_expect(
        tmp_path / "expect.yaml",
        "length_s:\n  target: 60\n  tolerance: 3\n",
    )
    workspace = tmp_path / "ws_short"
    write_edit(workspace, [video_slot(None, 0.0, 50.0)])
    result = run_eval(workspace, expect, "--json")
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["checks"][0]["passed"] is False
    assert report["checks"][0]["got"] == 50.0

    workspace_ok = tmp_path / "ws_ok"
    write_edit(workspace_ok, [video_slot(None, 0.0, 57.5)])
    result = run_eval(workspace_ok, expect, "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["checks"][0]["passed"] is True
    assert report["checks"][0]["got"] == 57.5


def test_middle_speed_length(tmp_path):
    slot = {"id": "v1", "clip": "clip.mp4", "in_s": 0.0, "out_s": 8.0, "middle_speed": 2.0}
    assert eval_edit.slot_screen_length(slot) == 6.0
    workspace = tmp_path / "ws"
    write_edit(workspace, [slot])
    expect = write_expect(tmp_path / "expect.yaml", "length_s:\n  target: 6\n  tolerance: 0\n")
    result = run_eval(workspace, expect, "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["checks"][0]["got"] == 6.0


def test_photo_duration_counts(tmp_path):
    workspace = tmp_path / "ws"
    write_edit(workspace, [photo_slot(duration_s=3.0)])
    expect = write_expect(tmp_path / "expect.yaml", "length_s:\n  target: 3\n  tolerance: 0\n")
    result = run_eval(workspace, expect, "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["checks"][0]["got"] == 3.0


def test_plain_string_expectation(tmp_path):
    workspace = tmp_path / "ws"
    write_edit(workspace, [video_slot("0072/s5", 0.0, 60.0)])
    expect = write_expect(tmp_path / "expect.yaml", 'must_include:\n  - "0072/s5"\n')
    result = run_eval(workspace, expect, "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["checks"][0]["any_of"] == ["0072/s5"]
    assert report["checks"][0]["passed"] is True
    assert report["checks"][0]["note"] == ""


def test_missing_edit_json_exits_1(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    expect = write_expect(tmp_path / "expect.yaml", 'must_include:\n  - "0072/s5"\n')
    result = run_eval(workspace, expect)
    assert result.returncode == 1
    assert result.stderr.startswith("[error] ")


def test_json_shape(tmp_path):
    workspace = tmp_path / "ws"
    write_edit(workspace, [video_slot("0072/s5", 0.0, 60.0)])
    expect = write_expect(tmp_path / "expect.yaml", "must_include: []\n")
    result = run_eval(workspace, expect, "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["checks"] == []
    assert report["passed"] is True
    assert "wall_time_s" in report
    assert report["llm_cost_usd"] == 0.0
    assert report["tool"] == "scripts/eval_edit.py"
    assert report["edit"].endswith("edit.json")


def test_photo_key_fallbacks(tmp_path):
    workspace = tmp_path / "ws"
    write_edit(
        workspace,
        [
            photo_slot(key="0072/p1"),
            {"id": "photo_02", "kind": "photo", "photo": "sunset.jpg", "duration_s": 3.0},
            {"kind": "photo", "photo": "river.jpg", "duration_s": 3.0},
        ],
    )
    expect = write_expect(
        tmp_path / "expect.yaml",
        'must_include:\n  - "0072/p1"\n  - "photo_02"\n  - "river/p1"\n',
    )
    result = run_eval(workspace, expect, "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert all(check["passed"] for check in report["checks"])


def test_human_table_rows(tmp_path):
    workspace = tmp_path / "ws"
    write_edit(workspace, [video_slot("0072/s7b1", 0.0, 50.0)])
    expect = write_expect(
        tmp_path / "expect.yaml",
        'must_include:\n  - any_of: ["0072/s5"]\n    note: "the tunnel approach"\n'
        '  - any_of: ["0072/s7b1", "0072/s7b5"]\n    note: "one of the cave-exit heroes"\n'
        "length_s:\n  target: 60\n  tolerance: 3\n",
    )
    result = run_eval(workspace, expect)
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert lines[0].startswith("expectation")
    assert "must_include 0072/s5" in lines[1] and "FAIL" in lines[1] and "the tunnel approach" in lines[1]
    assert "must_include 0072/s7b1|0072/s7b5" in lines[2] and "PASS" in lines[2]
    assert "length_s 60+-3 (got 50.00)" in lines[3] and "FAIL" in lines[3]
    assert lines[4] == "1 passed, 2 failed"


def test_out_writes_report_and_is_rerun_safe(tmp_path):
    workspace = tmp_path / "ws"
    write_edit(workspace, [video_slot("0072/s5", 0.0, 60.0)])
    expect = write_expect(tmp_path / "expect.yaml", 'must_include:\n  - "0072/s5"\n')
    out = tmp_path / "report.json"
    first = run_eval(workspace, expect, "--out", str(out))
    assert first.returncode == 0, first.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["passed"] is True
    assert not (tmp_path / "report.json.tmp").exists()
    second = run_eval(workspace, expect, "--out", str(out))
    assert second.returncode == 0, second.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["passed"] is True


def test_unreadable_expectations_exits_1(tmp_path):
    workspace = tmp_path / "ws"
    write_edit(workspace, [video_slot("0072/s5", 0.0, 60.0)])
    expect = write_expect(tmp_path / "expect.yaml", "must_include: [unclosed\n")
    result = run_eval(workspace, expect)
    assert result.returncode == 1
    assert result.stderr.startswith("[error] ")
