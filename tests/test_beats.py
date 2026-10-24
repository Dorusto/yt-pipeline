import json
import subprocess

import pytest

import beats
from common.exit_codes import EXIT_OK, EXIT_USAGE
from helpers import make_click


def ffmpeg(args):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-y", "-v", "error", *args],
        check=True,
        capture_output=True,
    )


def make_burst(path):
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.5:sample_rate=22050",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=0:d=1.5:s=22050",
            "-filter_complex",
            "[0][1]concat=n=2:v=0:a=1",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )


def make_silent_video(path):
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:d=1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )


def analyse(capsys, music, *extra):
    code = beats.main([str(music), *extra, "--json"])
    assert code == EXIT_OK
    return json.loads(capsys.readouterr().out)


def boundaries(summary):
    return [summary["cut_grid"][0]["start_s"]] + [row["end_s"] for row in summary["cut_grid"]]


def test_tempo(tmp_path, capsys):
    click = tmp_path / "click.wav"
    make_click(click, duration=8.0)
    summary = analyse(capsys, click)
    assert abs(summary["bpm"] - 120.0) <= 2.0
    assert summary["beat_period_s"] == pytest.approx(0.5, abs=0.02)
    assert summary["beats"] == pytest.approx(16, abs=1)


def test_phase(tmp_path, capsys):
    click = tmp_path / "click.wav"
    make_click(click, duration=8.0)
    summary = analyse(capsys, click)
    assert abs(summary["beat_s"][0] - 0.0) <= 0.03
    for beat in summary["beat_s"]:
        assert abs(beat - round(beat / 0.5) * 0.5) <= 0.03


def test_downbeats(tmp_path, capsys):
    click = tmp_path / "click_accents.wav"
    make_click(click, duration=8.0, accent=True)
    summary = analyse(capsys, click)
    assert summary["downbeats"] == pytest.approx(summary["beats"] / 4, abs=1)
    assert abs(summary["downbeat_s"][0] - 0.0) <= 0.03


def test_energy(tmp_path, capsys):
    burst = tmp_path / "burst.wav"
    make_burst(burst)
    summary = analyse(capsys, burst)
    assert summary["energy_peak_dbfs"] > summary["energy_mean_dbfs"] + 40.0
    assert len(summary["energy"]) >= 2
    loud = [window for window in summary["energy"] if window["dbfs"] > summary["energy_mean_dbfs"]]
    quiet = [window for window in summary["energy"] if window["dbfs"] < summary["energy_mean_dbfs"]]
    assert loud and quiet


def test_cut_grid(tmp_path, capsys):
    click = tmp_path / "click_long.wav"
    make_click(click, duration=96.0, accent=True)
    summary = analyse(capsys, click, "--target-duration-s", "90", "--slots", "15")
    rows = summary["cut_grid"]
    assert len(rows) == 15
    assert rows[0]["start_s"] == 0
    assert rows[-1]["end_s"] == 90
    times = boundaries(summary)
    for time in times:
        assert abs(time - round(time / 0.5) * 0.5) <= 0.03
    assert all(later > earlier for earlier, later in zip(times, times[1:]))
    for row in rows:
        length = row["end_s"] - row["start_s"]
        assert row["beats"] == round(length / 0.5)
        assert abs(length - row["beats"] * 0.5) <= 0.03
    assert sum(row["beats"] for row in rows) == pytest.approx(180, abs=1)


def test_music_shorter_than_edit(tmp_path, capsys):
    click = tmp_path / "click_short.wav"
    make_click(click, duration=8.0)
    summary = analyse(capsys, click, "--target-duration-s", "90", "--slots", "15")
    assert summary["music_shorter_than_edit"] is True
    assert summary["cut_grid"][0]["start_s"] == 0
    assert summary["cut_grid"][-1]["end_s"] == 90


def test_json_summary_keys_and_zero_cost(tmp_path, capsys):
    click = tmp_path / "click.wav"
    make_click(click, duration=8.0)
    summary = analyse(capsys, click, "--target-duration-s", "90")
    expected = {
        "status",
        "out",
        "input",
        "duration_s",
        "bpm",
        "beat_period_s",
        "beats",
        "downbeats",
        "beat_s",
        "downbeat_s",
        "energy",
        "energy_mean_dbfs",
        "energy_peak_dbfs",
        "cut_grid",
        "music_shorter_than_edit",
        "warnings",
        "wall_time_s",
        "llm_cost_usd",
    }
    assert expected <= set(summary)
    assert summary["status"] == "ok"
    assert summary["llm_cost_usd"] == 0.0
    assert summary["wall_time_s"] >= 0


def test_out_writes_same_object(tmp_path, capsys):
    click = tmp_path / "click.wav"
    make_click(click, duration=8.0)
    out = tmp_path / "beats.json"
    summary = analyse(capsys, click, "--out", str(out))
    assert summary["out"] == str(out)
    assert out.exists()
    assert json.loads(out.read_text()) == summary


def test_error_missing_file(tmp_path, capsys):
    code = beats.main([str(tmp_path / "nope.wav"), "--json"])
    assert code == EXIT_USAGE
    assert "[error]" in capsys.readouterr().err


def test_error_no_audio_stream(tmp_path, capsys):
    video = tmp_path / "silent.mp4"
    make_silent_video(video)
    code = beats.main([str(video), "--json"])
    assert code == EXIT_USAGE
    assert "[error]" in capsys.readouterr().err


def test_rerun_safety_and_source_untouched(tmp_path, capsys):
    click = tmp_path / "click.wav"
    make_click(click, duration=8.0)
    original = (click.stat().st_size, click.stat().st_mtime_ns)
    out = tmp_path / "beats.json"
    assert beats.main([str(click), "--out", str(out), "--json"]) == EXIT_OK
    capsys.readouterr()
    out_before = (out.stat().st_size, out.stat().st_mtime_ns)
    assert (click.stat().st_size, click.stat().st_mtime_ns) == original

    assert beats.main([str(click), "--out", str(out), "--json"]) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "skipped"
    assert (click.stat().st_size, click.stat().st_mtime_ns) == original
    assert (out.stat().st_size, out.stat().st_mtime_ns) == out_before


def test_force_rewrites(tmp_path, capsys):
    click = tmp_path / "click.wav"
    make_click(click, duration=8.0)
    out = tmp_path / "beats.json"
    assert beats.main([str(click), "--out", str(out), "--json"]) == EXIT_OK
    capsys.readouterr()
    assert beats.main([str(click), "--out", str(out), "--json", "--force"]) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "ok"


def test_help_lists_flags_with_examples(capsys):
    with pytest.raises(SystemExit) as excinfo:
        beats.main(["--help"])
    assert excinfo.value.code == EXIT_OK
    out = capsys.readouterr().out
    for flag in (
        "--out",
        "--target-duration-s",
        "--slots",
        "--min-beat-s",
        "--max-beat-s",
        "--json",
        "--force",
    ):
        assert flag in out
    assert "examples:" in out
