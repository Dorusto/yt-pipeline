import json
import subprocess

import pytest

import audio_clean


def ffmpeg(args):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-y", "-v", "error", *args],
        check=True,
        capture_output=True,
    )


def make_tone(path, frequency=1000, duration=4.0, volume=1.13):
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration}:sample_rate=48000",
            "-af",
            f"volume={volume}",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )


def make_wind(path):
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=40:duration=4:sample_rate=48000",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=color=pink:duration=4:sample_rate=48000:amplitude=0.05",
            "-filter_complex",
            "[0]volume=1.0[a];[1]highpass=f=250,volume=2.0[b];[a][b]amix=inputs=2:duration=longest",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )


def make_noisy(path):
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=4:sample_rate=48000",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=color=pink:duration=4:sample_rate=48000:amplitude=0.03",
            "-filter_complex",
            "[0]volume=1.13[a];[1]volume=1.0[b];[a][b]amix=inputs=2:duration=longest",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )


def make_clean(path):
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=2:sample_rate=48000",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=0:d=2:s=48000:c=mono",
            "-filter_complex",
            "[0]volume=1.13[a];[a][1]concat=n=2:v=0:a=1",
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


def analyse(capsys, media, report):
    code = audio_clean.main([str(media), "--report", str(report), "--analyse-only", "--json"])
    assert code == 0
    return json.loads(capsys.readouterr().out)


def test_measure_tone(tmp_path, capsys):
    tone = tmp_path / "tone.wav"
    make_tone(tone)
    summary = analyse(capsys, tone, tmp_path / "report.json")
    before = summary["before"]
    assert abs(before["duration_s"] - 4.0) <= 0.05
    assert abs(before["rms_dbfs"] - (-20.0)) <= 1.0
    assert before["sample_rate"] == 48000
    assert len(before["segments"]) == 2
    assert before["segments"][0]["start_s"] == 0.0
    assert before["segments"][-1]["end_s"] == pytest.approx(4.0, abs=0.05)
    assert before["verdict"] != "windy"


def test_wind_detection(tmp_path, capsys):
    wind = tmp_path / "wind.wav"
    make_wind(wind)
    summary = analyse(capsys, wind, tmp_path / "report.json")
    before = summary["before"]
    assert before["wind"]["wind_detected"] is True
    assert before["wind"]["wind_ratio"] > 0.55
    assert before["verdict"] in ("windy", "windy+noisy")


def test_noise_detection_noisy_and_clean(tmp_path, capsys):
    noisy = tmp_path / "noisy.wav"
    make_noisy(noisy)
    noisy_before = analyse(capsys, noisy, tmp_path / "noisy.json")["before"]
    assert noisy_before["noise"]["noise_detected"] is True
    assert noisy_before["noise"]["snr_db"] < 12

    clean = tmp_path / "clean.wav"
    make_clean(clean)
    clean_before = analyse(capsys, clean, tmp_path / "clean.json")["before"]
    assert clean_before["noise"]["noise_detected"] is False
    assert clean_before["noise"]["snr_db"] >= 12


def test_cleanup_reduces_wind_and_normalises(tmp_path, capsys):
    wind = tmp_path / "wind.wav"
    make_wind(wind)
    out = tmp_path / "clean.wav"
    code = audio_clean.main(
        [
            str(wind),
            "--out",
            str(out),
            "--denoiser",
            "afftdn",
            "--report",
            str(tmp_path / "report.json"),
            "--json",
        ]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "written"
    assert out.exists()
    assert summary["after"]["wind"]["wind_ratio"] < summary["before"]["wind"]["wind_ratio"]
    assert abs(summary["after"]["rms_dbfs"] - (-16.0)) <= 2.0
    assert "highpass" in summary["applied"]
    assert "afftdn" in summary["applied"]


def test_denoiser_none_applies_highpass_and_loudnorm(tmp_path, capsys):
    wind = tmp_path / "wind.wav"
    make_wind(wind)
    out = tmp_path / "clean.flac"
    code = audio_clean.main(
        [str(wind), "--out", str(out), "--denoiser", "none", "--report", str(tmp_path / "r.json"), "--json"]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["applied"] == ["highpass", "loudnorm"]
    assert out.exists()


def test_afftdn_works_without_model(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(audio_clean, "DEFAULT_MODEL", tmp_path / "missing.rnnn")
    tone = tmp_path / "tone.wav"
    make_tone(tone)
    out = tmp_path / "clean.m4a"
    code = audio_clean.main(
        [str(tone), "--out", str(out), "--denoiser", "afftdn", "--report", str(tmp_path / "r.json"), "--json"]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["denoiser"] == "afftdn"
    assert "afftdn" in summary["applied"]
    assert summary["warnings"] == []
    assert out.exists()


def test_arnndn_default_falls_back_to_afftdn(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(audio_clean, "DEFAULT_MODEL", tmp_path / "missing.rnnn")
    tone = tmp_path / "tone.wav"
    make_tone(tone)
    out = tmp_path / "clean.wav"
    code = audio_clean.main(
        [str(tone), "--out", str(out), "--report", str(tmp_path / "r.json"), "--json"]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["denoiser"] == "afftdn"
    assert "afftdn" in summary["applied"]
    assert any("model" in warning for warning in summary["warnings"])


def test_arnndn_explicit_without_model_errors(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(audio_clean, "DEFAULT_MODEL", tmp_path / "missing.rnnn")
    tone = tmp_path / "tone.wav"
    make_tone(tone)
    out = tmp_path / "clean.wav"
    code = audio_clean.main([str(tone), "--out", str(out), "--denoiser", "arnndn"])
    assert code == 1
    captured = capsys.readouterr()
    assert "[error]" in captured.err
    assert "--model" in captured.err
    assert not out.exists()


def test_analyse_only_writes_report_and_no_audio(tmp_path, capsys):
    tone = tmp_path / "tone.wav"
    make_tone(tone)
    report = tmp_path / "report.json"
    code = audio_clean.main([str(tone), "--analyse-only", "--report", str(report), "--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "ok"
    assert summary["out"] is None
    assert report.exists()
    written = sorted(path.name for path in tmp_path.iterdir())
    assert written == ["report.json", "tone.wav"]


def test_no_audio_stream_reports_null(tmp_path, capsys):
    video = tmp_path / "silent.mp4"
    make_silent_video(video)
    report = tmp_path / "report.json"
    code = audio_clean.main([str(video), "--analyse-only", "--report", str(report), "--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["before"] is None
    assert summary["after"] is None
    assert any("no audio" in warning for warning in summary["warnings"])


def test_no_audio_stream_with_out_errors(tmp_path, capsys):
    video = tmp_path / "silent.mp4"
    make_silent_video(video)
    out = tmp_path / "clean.wav"
    code = audio_clean.main([str(video), "--out", str(out)])
    assert code == 1
    assert "[error]" in capsys.readouterr().err
    assert not out.exists()


def test_originals_untouched_and_rerun_is_skipped(tmp_path, capsys):
    tone = tmp_path / "tone.wav"
    make_tone(tone)
    original_before = (tone.stat().st_size, tone.stat().st_mtime_ns)
    out = tmp_path / "clean.wav"
    report = tmp_path / "report.json"
    assert (
        audio_clean.main([str(tone), "--out", str(out), "--report", str(report), "--json"]) == 0
    )
    capsys.readouterr()
    out_before = (out.stat().st_size, out.stat().st_mtime_ns)
    assert (tone.stat().st_size, tone.stat().st_mtime_ns) == original_before

    assert (
        audio_clean.main([str(tone), "--out", str(out), "--report", str(report), "--json"]) == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "skipped"
    assert (tone.stat().st_size, tone.stat().st_mtime_ns) == original_before
    assert (out.stat().st_size, out.stat().st_mtime_ns) == out_before


def test_json_summary_keys_and_zero_cost(tmp_path, capsys):
    tone = tmp_path / "tone.wav"
    make_tone(tone)
    out = tmp_path / "clean.wav"
    assert (
        audio_clean.main(
            [
                str(tone),
                "--out",
                str(out),
                "--report",
                str(tmp_path / "r.json"),
                "--denoiser",
                "none",
                "--json",
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    expected = {
        "status",
        "out",
        "report",
        "input",
        "denoiser",
        "before",
        "after",
        "applied",
        "warnings",
        "wall_time_s",
        "llm_cost_usd",
    }
    assert expected <= set(summary)
    assert summary["llm_cost_usd"] == 0.0
    assert summary["wall_time_s"] >= 0


def test_help_lists_flags_with_examples(capsys):
    with pytest.raises(SystemExit) as excinfo:
        audio_clean.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--out", "--report", "--analyse-only", "--denoiser", "--model", "--wind-hz", "--target-lufs", "--no-loudnorm", "--json", "--force"):
        assert flag in out
    assert "examples:" in out
