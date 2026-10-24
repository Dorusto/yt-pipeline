import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import edit_drop  # noqa: E402


def make_project(tmp_path, shots=3):
    ws = tmp_path / "trip"
    v1 = ws / "versions" / "v001"
    v1.mkdir(parents=True)
    edit = {"schema_version": "x", "video": [
        {"id": f"v{i}", "clip": f"clip{i}.mp4", "in_s": 0.0, "out_s": 2.0} for i in range(1, shots + 1)
    ]}
    (v1 / "edit.json").write_text(json.dumps(edit), encoding="utf-8")
    (v1 / "recap_720p.mp4").write_bytes(b"x")
    (v1 / "version.json").write_text(json.dumps({"created_at": "2026-10-15T11:00:00+0000",
                                                 "files": ["edit.json", "recap_720p.mp4"],
                                                 "brief": "b"}), encoding="utf-8")
    return ws


def make_project_with_sources(tmp_path, shots=3, duration=10.0):
    ws = tmp_path / "trip"
    v1 = ws / "versions" / "v001"
    v1.mkdir(parents=True)
    edit = {"schema_version": "x",
            "sources": [{"file": f"clip{i}.mp4", "path": f"/x/clip{i}.mp4", "duration_s": duration}
                        for i in range(1, shots + 1)],
            "video": [{"id": f"v{i}", "clip": f"clip{i}.mp4", "in_s": 2.0, "out_s": 4.0}
                      for i in range(1, shots + 1)]}
    (v1 / "edit.json").write_text(json.dumps(edit), encoding="utf-8")
    (v1 / "recap_720p.mp4").write_bytes(b"x")
    (v1 / "version.json").write_text(json.dumps({"created_at": "2026-10-15T11:00:00+0000",
                                                 "files": ["edit.json", "recap_720p.mp4"],
                                                 "brief": "b"}), encoding="utf-8")
    return ws


def test_dry_run_names_the_removed_shot(tmp_path, capsys):
    ws = make_project(tmp_path)
    code = edit_drop.main(["--workspace", str(ws), "--drop", "2", "--dry-run", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["removed"] == ["v2"] and out["shots_left"] == 2
    assert not (ws / "edit.json").exists()


def test_bad_shot_number_is_a_usage_error(tmp_path, capsys):
    ws = make_project(tmp_path)
    code = edit_drop.main(["--workspace", str(ws), "--drop", "9", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["ok"] is False and "does not exist" in out["error"]


def test_drop_keeps_the_other_shots_in_order():
    edit = {"video": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    new_edit, removed = edit_drop.drop_shots(edit, [2])
    assert [shot["id"] for shot in new_edit["video"]] == ["a", "c"]
    assert removed == [{"id": "b"}]


class FakeRender:
    def __init__(self, returncode, stderr=""):
        self.returncode = returncode
        self.stdout = "{}"
        self.stderr = stderr


def test_render_failure_leaves_the_workspace_unchanged(tmp_path, monkeypatch, capsys):
    ws = make_project(tmp_path)
    monkeypatch.setattr(edit_drop.subprocess, "run", lambda *a, **k: FakeRender(3, "boom"))
    code = edit_drop.main(["--workspace", str(ws), "--drop", "2", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 3 and out["ok"] is False and "render failed" in out["error"]
    assert not (ws / "edit.json").exists()
    assert not (ws / "edit.json.tmp").exists()


def test_render_success_replaces_the_edit(tmp_path, monkeypatch, capsys):
    ws = make_project(tmp_path)
    seen = {}

    def fake_run(command, **kwargs):
        if any("render.py" in part for part in command):
            seen["command"] = command
        return FakeRender(0)

    monkeypatch.setattr(edit_drop.subprocess, "run", fake_run)
    code = edit_drop.main(["--workspace", str(ws), "--drop", "2", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["version"] == "v002"
    edit = json.loads((ws / "edit.json").read_text(encoding="utf-8"))
    assert [shot["id"] for shot in edit["video"]] == ["v1", "v3"]
    assert not (ws / "edit.json.tmp").exists()
    edit_arg = seen["command"][seen["command"].index("--edit") + 1]
    assert edit_arg.endswith("edit.json.tmp")


def test_trim_longer_extends_the_out_edge(tmp_path, capsys):
    ws = make_project_with_sources(tmp_path)
    code = edit_drop.main(["--workspace", str(ws), "--trim", "2:+1", "--dry-run", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["trimmed"] == [{"shot": 2, "delta": 1.0, "in_s": 2.0, "out_s": 5.0}]


def test_trim_longer_clamps_to_the_clip_and_takes_the_rest_from_the_in_edge(tmp_path, capsys):
    ws = make_project_with_sources(tmp_path, duration=4.5)
    code = edit_drop.main(["--workspace", str(ws), "--trim", "1:+1", "--dry-run", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["trimmed"][0]["out_s"] == 4.5 and out["trimmed"][0]["in_s"] == 1.5


def test_trim_shorter_trims_the_out_edge(tmp_path, capsys):
    ws = make_project_with_sources(tmp_path)
    code = edit_drop.main(["--workspace", str(ws), "--trim", "1:-1", "--dry-run", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["trimmed"][0]["out_s"] == 3.0


def test_trim_shorter_at_the_floor_is_a_usage_error(tmp_path, capsys):
    ws = make_project_with_sources(tmp_path)
    edit = json.loads((ws / "versions" / "v001" / "edit.json").read_text(encoding="utf-8"))
    edit["video"][0]["out_s"] = 3.0
    (ws / "versions" / "v001" / "edit.json").write_text(json.dumps(edit), encoding="utf-8")
    code = edit_drop.main(["--workspace", str(ws), "--trim", "1:-1", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and "shortest" in out["error"]


def test_trim_longer_without_a_clip_duration_is_a_usage_error(tmp_path, capsys):
    ws = make_project(tmp_path)
    code = edit_drop.main(["--workspace", str(ws), "--trim", "1:+1", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and "unknown" in out["error"]


def test_drop_and_trim_together_is_a_usage_error(tmp_path, capsys):
    ws = make_project(tmp_path)
    code = edit_drop.main(["--workspace", str(ws), "--drop", "1", "--trim", "2:+1", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and "either" in out["error"]


def test_trim_render_success_replaces_the_edit(tmp_path, monkeypatch, capsys):
    ws = make_project_with_sources(tmp_path)
    monkeypatch.setattr(edit_drop.subprocess, "run", lambda *a, **k: FakeRender(0))
    code = edit_drop.main(["--workspace", str(ws), "--trim", "2:+1", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["version"] == "v002"
    edit = json.loads((ws / "edit.json").read_text(encoding="utf-8"))
    assert edit["video"][1]["out_s"] == 5.0
