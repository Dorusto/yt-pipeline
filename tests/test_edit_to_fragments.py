import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

import edit_to_fragments as etf
import kdenlive_from_fragments as kf
from common.schema import validate_or_raise

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def make_edit(tmp_path, sources, video, name="edit.json"):
    edit = {
        "schema_version": "0.1",
        "generated_at": "2026-10-13T19:43:17+00:00",
        "brief": {"text": "test brief"},
        "sources": sources,
        "video": video,
    }
    path = tmp_path / name
    path.write_text(json.dumps(edit), encoding="utf-8")
    return path


def slot(slot_id, clip, in_s, out_s, score=0.9, reason="hero travel into the cave"):
    return {
        "id": slot_id,
        "clip": clip,
        "in_s": in_s,
        "out_s": out_s,
        "score": score,
        "reason": reason,
    }


def run_json(capsys, *argv):
    rc = etf.main(list(argv))
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out.strip() else None
    return rc, payload, captured


def load_fragments(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def make_clip(path, duration=3.0):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:size=320x180:rate=25:duration={duration}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_timecodes_round_trip_to_consumer_frames(tmp_path, capsys):
    edit = make_edit(
        tmp_path,
        [{"file": "a.mp4", "path": "/src/a.mp4"}],
        [slot("v1", "a.mp4", 1.5, 4.0)],
    )
    out = tmp_path / "fragments.yaml"
    rc, _, _ = run_json(capsys, "--edit", str(edit), "--out", str(out), "--json")
    assert rc == 0
    fragment = load_fragments(out)["fragments"][0]
    frames = kf.tc_to_frames(fragment["out"]) - kf.tc_to_frames(fragment["in"]) + 1
    assert frames == round((4.0 - 1.5) * kf.FPS)


def test_yaml_validates_and_carries_one_fragment_per_slot(tmp_path, capsys):
    sources = [
        {"file": "a.mp4", "path": "/src/a.mp4"},
        {"file": "b.mp4", "path": "/src/b.mp4"},
    ]
    video = [
        slot("v1", "a.mp4", 0.0, 2.0, reason="first"),
        slot("v2", "b.mp4", 1.0, 5.0, score=0.8, reason="second"),
    ]
    edit = make_edit(tmp_path, sources, video)
    out = tmp_path / "fragments.yaml"
    rc, _, _ = run_json(capsys, "--edit", str(edit), "--out", str(out), "--json")
    assert rc == 0
    config = load_fragments(out)
    validate_or_raise(config, "fragments")
    assert [fragment["file"] for fragment in config["fragments"]] == ["a.mp4", "b.mp4"]
    assert [fragment["slot"] for fragment in config["fragments"]] == ["v1", "v2"]
    for fragment in config["fragments"]:
        assert {"file", "in", "out", "label", "score", "reason", "slot"} <= set(fragment)
    assert config["fragments"][1]["score"] == 0.8
    assert config["fragments"][1]["reason"] == "second"
    assert config["fragments"][0]["label"].startswith("v1 · 0.90 · ")


def test_source_dir_and_kdenlive_out_land_in_yaml(tmp_path, capsys):
    edit = make_edit(
        tmp_path,
        [{"file": "a.mp4", "path": "/proxy/a.mp4"}],
        [slot("v1", "a.mp4", 0.0, 2.0)],
    )
    out = tmp_path / "fragments.yaml"
    raw = tmp_path / "raw"
    kdenlive = tmp_path / "recap.kdenlive"
    rc, _, _ = run_json(
        capsys,
        "--edit",
        str(edit),
        "--out",
        str(out),
        "--source-dir",
        str(raw),
        "--kdenlive-out",
        str(kdenlive),
        "--json",
    )
    assert rc == 0
    config = load_fragments(out)
    assert config["source_dir"] == str(raw)
    assert config["output"] == str(kdenlive)


def test_default_source_dir_and_kdenlive_out(tmp_path, capsys):
    edit = make_edit(
        tmp_path,
        [{"file": "a.mp4", "path": "/src/raw/a.mp4"}],
        [slot("v1", "a.mp4", 0.0, 2.0)],
    )
    out = tmp_path / "fragments.yaml"
    rc, _, _ = run_json(capsys, "--edit", str(edit), "--out", str(out), "--json")
    assert rc == 0
    config = load_fragments(out)
    assert config["source_dir"] == "/src/raw"
    assert config["output"] == str(out.with_suffix(".kdenlive"))


def test_invalid_edit_exits_1_and_writes_nothing(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "0.1"}), encoding="utf-8")
    out = tmp_path / "fragments.yaml"
    rc = etf.main(["--edit", str(bad), "--out", str(out)])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.err.startswith("[error]")
    assert not out.exists()


def test_rerun_skips_then_force_writes(tmp_path, capsys):
    edit = make_edit(
        tmp_path,
        [{"file": "a.mp4", "path": "/src/a.mp4"}],
        [slot("v1", "a.mp4", 0.0, 2.0)],
    )
    out = tmp_path / "fragments.yaml"
    rc, first, _ = run_json(capsys, "--edit", str(edit), "--out", str(out), "--json")
    assert rc == 0
    assert first["status"] == "written"

    later = edit.stat().st_mtime + 10
    os.utime(out, (later, later))
    rc, second, _ = run_json(capsys, "--edit", str(edit), "--out", str(out), "--json")
    assert rc == 0
    assert second["status"] == "skipped"

    rc, forced, _ = run_json(capsys, "--edit", str(edit), "--out", str(out), "--force", "--json")
    assert rc == 0
    assert forced["status"] == "written"


def test_json_summary_keys_and_no_llm_cost(tmp_path, capsys):
    edit = make_edit(
        tmp_path,
        [{"file": "a.mp4", "path": "/src/a.mp4"}],
        [slot("v1", "a.mp4", 1.5, 4.0)],
    )
    out = tmp_path / "fragments.yaml"
    rc, summary, _ = run_json(capsys, "--edit", str(edit), "--out", str(out), "--json")
    assert rc == 0
    for key in (
        "status",
        "out",
        "fragments",
        "source_dir",
        "kdenlive_out",
        "total_duration_s",
        "warnings",
        "wall_time_s",
        "llm_cost_usd",
    ):
        assert key in summary
    assert summary["status"] == "written"
    assert summary["fragments"] == 1
    assert summary["total_duration_s"] == pytest.approx(2.5)
    assert summary["warnings"] == []
    assert summary["llm_cost_usd"] == 0.0


@pytest.mark.skipif(shutil.which("melt") is None, reason="melt (Kdenlive's MLT CLI) is not installed")
def test_end_to_end_with_kdenlive_consumer(tmp_path, capsys):
    raw = tmp_path / "raw"
    raw.mkdir()
    make_clip(raw / "a.mp4")
    edit = make_edit(
        tmp_path,
        [{"file": "a.mp4", "path": str(raw / "a.mp4")}],
        [slot("v1", "a.mp4", 0.0, 2.0)],
    )
    out = tmp_path / "fragments.yaml"
    rc = etf.main(
        [
            "--edit",
            str(edit),
            "--out",
            str(out),
            "--source-dir",
            str(raw),
            "--json",
        ]
    )
    capsys.readouterr()
    assert rc == 0

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "kdenlive_from_fragments.py"), str(out)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "fragments.kdenlive").exists()
