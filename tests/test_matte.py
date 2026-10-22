import json
import subprocess
import sys
import types
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


def run_with_fakes(monkeypatch, tmp_path, height, ratio=None):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False)
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(matte, "load_model", lambda torch, device: None)
    monkeypatch.setattr(matte, "process_frames", lambda *args, **kwargs: 1)
    monkeypatch.setattr(matte, "encode_prores", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        matte,
        "probe_video",
        lambda path: {
            "duration": 1.0,
            "fps": 25.0,
            "fps_str": "25/1",
            "width": 1920,
            "height": height,
        },
    )
    return matte.run(str(video), ratio=ratio)


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


@pytest.mark.parametrize(
    "height,expected",
    [
        (2160, 0.125),
        (1600, 0.125),
        (1080, 0.25),
        (900, 0.25),
        (720, 0.4),
        (480, 0.4),
    ],
)
def test_default_ratio_for_height(height, expected):
    assert matte.default_ratio_for_height(height) == expected


def test_auto_ratio_1080p(tmp_path, monkeypatch):
    summary = run_with_fakes(monkeypatch, tmp_path, 1080)
    assert summary["downsample_ratio"] == 0.25


def test_auto_ratio_720p(tmp_path, monkeypatch):
    summary = run_with_fakes(monkeypatch, tmp_path, 720)
    assert summary["downsample_ratio"] == 0.4


def test_auto_ratio_4k(tmp_path, monkeypatch):
    summary = run_with_fakes(monkeypatch, tmp_path, 2160)
    assert summary["downsample_ratio"] == 0.125


def test_explicit_ratio_is_used_and_reported(tmp_path, monkeypatch):
    summary = run_with_fakes(monkeypatch, tmp_path, 1080, ratio=0.6)
    assert summary["downsample_ratio"] == 0.6


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
