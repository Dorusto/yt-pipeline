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
