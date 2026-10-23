import json
import os
from pathlib import Path

import thumbs


def edit_document():
    return {
        "schema_version": "0.1",
        "generated_at": "2026-10-23T00:00:00+00:00",
        "brief": {"text": "a calm recap"},
        "sources": [{"file": "a.mp4", "path": "/x/a.mp4", "duration_s": 10.0}],
        "video": [
            {"id": "v1", "clip": "a.mp4", "in_s": 0.0, "out_s": 4.0, "score": 0.5, "reason": "r"},
            {"id": "v2", "clip": "a.mp4", "in_s": 4.0, "out_s": 8.0, "score": 0.5, "reason": "r"},
        ],
    }


def make_workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "edit.json").write_text(json.dumps(edit_document()), encoding="utf-8")
    (workspace / "recap_720p.mp4").write_bytes(b"video")
    return workspace


def test_thumbs_writes_poster_and_shot_frames(tmp_path, monkeypatch, capsys):
    workspace = make_workspace(tmp_path)
    calls = []

    monkeypatch.setattr(thumbs, "probe_duration", lambda path: 10.0)

    def fake_extract(recap, time_s, out, width):
        calls.append((round(time_s, 3), Path(out).name, width))
        Path(out).write_bytes(b"jpg")

    monkeypatch.setattr(thumbs, "extract_frame", fake_extract)
    assert thumbs.main(["--workspace", str(workspace), "--json"]) == 0
    assert (workspace / "thumbs" / "poster.jpg").is_file()
    assert (workspace / "thumbs" / "shot-1.jpg").is_file()
    assert (workspace / "thumbs" / "shot-2.jpg").is_file()
    assert calls[0] == (3.0, "poster.jpg", 320)
    assert calls[1] == (2.0, "shot-1.jpg", 320)
    assert calls[2] == (6.0, "shot-2.jpg", 320)
    summary = json.loads(capsys.readouterr().out)
    assert summary["shots"] == 2
    assert summary["skipped"] is False


def test_thumbs_skips_when_newer(tmp_path, monkeypatch, capsys):
    workspace = make_workspace(tmp_path)
    thumbs_dir = workspace / "thumbs"
    thumbs_dir.mkdir()
    recap = workspace / "recap_720p.mp4"
    stamp = recap.stat().st_mtime + 100
    for name in ("poster.jpg", "shot-1.jpg", "shot-2.jpg"):
        path = thumbs_dir / name
        path.write_bytes(b"jpg")
        os.utime(path, (stamp, stamp))
    called = []
    monkeypatch.setattr(thumbs, "probe_duration", lambda path: 10.0)
    monkeypatch.setattr(thumbs, "extract_frame", lambda *args: called.append(args))
    assert thumbs.main(["--workspace", str(workspace), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["skipped"] is True
    assert called == []


def test_thumbs_missing_edit_is_usage_error(tmp_path, capsys):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert thumbs.main(["--workspace", str(workspace), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["exit_code"] == 2


def test_thumbs_json_fields(tmp_path, monkeypatch, capsys):
    workspace = make_workspace(tmp_path)
    monkeypatch.setattr(thumbs, "probe_duration", lambda path: 10.0)
    monkeypatch.setattr(thumbs, "extract_frame", lambda recap, time_s, out, width: Path(out).write_bytes(b"jpg"))
    assert thumbs.main(["--workspace", str(workspace), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["tool"] == "thumbs.py"
    assert isinstance(summary["wall_time_s"], (int, float))
    assert summary["llm_cost_usd"] == 0.0


def test_thumbs_none_duration_is_a_runtime_error(tmp_path, monkeypatch, capsys):
    workspace = make_workspace(tmp_path)
    monkeypatch.setattr(thumbs, "probe_duration", lambda path: None)
    assert thumbs.main(["--workspace", str(workspace), "--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["exit_code"] == 3
