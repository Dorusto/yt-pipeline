import json
import subprocess
import sys
from pathlib import Path

import pytest

import matte

PRORES_4444_ALPHA_PIX_FMTS = ("yuva444p10le", "yuva444p12le")


def make_clip(path, duration=1, rate=25, size="128x96"):
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={size}:rate={rate}:duration={duration}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )


def test_help_exits_0_and_lists_flags(capsys):
    with pytest.raises(SystemExit) as excinfo:
        matte.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--start", "--end", "--ratio", "--out", "--json"):
        assert flag in out


def test_missing_input_exits_2_naming_the_path(tmp_path, capsys):
    missing = tmp_path / "nope.mp4"
    rc = matte.main([str(missing), "--json"])
    assert rc == 2
    assert "nope.mp4" in capsys.readouterr().err


def test_start_not_before_end_exits_2(tmp_path, capsys):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    rc = matte.main([str(video), "--start", "2", "--end", "1", "--json"])
    assert rc == 2
    assert "2" in capsys.readouterr().err


def test_end_beyond_clip_exits_2(tmp_path, capsys):
    video = tmp_path / "clip.mp4"
    make_clip(video, duration=1)
    rc = matte.main([str(video), "--end", "5", "--json"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "5.0" in err and "1.0" in err


def test_negative_start_exits_2(tmp_path, capsys):
    video = tmp_path / "clip.mp4"
    make_clip(video, duration=1)
    rc = matte.main([str(video), "--start", "-1", "--json"])
    assert rc == 2
    assert "-1.0" in capsys.readouterr().err


def test_ratio_out_of_range_exits_2(tmp_path, capsys):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    for bad in ("0", "2"):
        rc = matte.main([str(video), "--ratio", bad, "--json"])
        assert rc == 2
        assert "ratio" in capsys.readouterr().err


def test_default_out_is_stem_matte_mov(tmp_path):
    video = tmp_path / "clip.mp4"
    assert matte.resolve_output(video, None) == tmp_path / "clip_matte.mov"


def test_explicit_out_is_used(tmp_path):
    video = tmp_path / "clip.mp4"
    target = tmp_path / "custom.mov"
    assert matte.resolve_output(video, str(target)) == target


def test_out_equal_to_input_exits_2_before_torch(tmp_path, monkeypatch, capsys):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "torch", None)
    rc = matte.main([str(video), "--out", str(video), "--json"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "overwrite" in err
    assert "torch" not in err.lower()


def test_integration_prores4444_with_alpha(tmp_path, capsys):
    pytest.importorskip("torch")
    video = tmp_path / "in.mp4"
    make_clip(video, duration=1, rate=25, size="128x96")
    out = tmp_path / "out.mov"
    rc = matte.main([str(video), "--out", str(out), "--ratio", "0.25", "--json"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["frames"] == 25
    assert summary["width"] == 128
    assert summary["height"] == 96
    assert summary["fps"] == 25.0
    assert summary["device"] in ("cpu", "cuda")
    assert summary["llm_cost_usd"] == 0.0
    assert Path(summary["output"]) == out
    assert out.is_file()

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=pix_fmt,nb_read_frames",
            "-of",
            "json",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["pix_fmt"] in PRORES_4444_ALPHA_PIX_FMTS
    assert int(stream["nb_read_frames"]) == 25
