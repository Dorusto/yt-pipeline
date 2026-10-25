import json
import subprocess
import sys
import tempfile
import wave

import numpy as np
import pytest
import yaml

import beats
from common.exit_codes import EXIT_OK, EXIT_USAGE
from helpers import make_click


def write_click_track(path, times, rate=22050, amplitude=0.9, pulse_s=0.02):
    samples = np.zeros(int(rate * (max(times) + 1.0)))
    pulse_len = int(pulse_s * rate)
    pulse = np.sin(2 * np.pi * 1000 * np.arange(pulse_len) / rate)
    for time_s in times:
        start = int(round(time_s * rate))
        end = min(len(samples), start + pulse_len)
        samples[start:end] += amplitude * pulse[: end - start]
    data = np.clip(samples, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((data * 32767).astype("<i2").tobytes())


def make_drifting_click(path, duration=60.0, bpm=120.0, t0=0.23, drift=0.03):
    """Write a click track whose period drifts by ``drift`` over the track and return the click times."""
    period = 60.0 / bpm
    times = []
    time_s = t0
    while time_s < duration:
        times.append(time_s)
        time_s += period * (1.0 + drift * time_s / 60.0)
    write_click_track(path, times)
    return times


def make_attack_click(path, duration=8.0, bpm=120.0, rate=22050):
    """Write a click track with a realistic attack and return the true hit onsets."""
    period = 60.0 / bpm
    times = list(np.arange(0.0, duration, period))
    samples = np.zeros(int(rate * duration))
    attack_len = int(0.005 * rate)
    decay_len = int(0.040 * rate)
    body_len = attack_len + decay_len
    body = np.sin(2 * np.pi * 60 * np.arange(body_len) / rate)
    envelope = np.concatenate(
        [
            np.linspace(0.0, 1.0, attack_len),
            np.exp(-np.arange(decay_len) / (0.010 * rate)),
        ]
    )
    hat_len = int(0.030 * rate)
    hat_window = np.hanning(hat_len)
    rng = np.random.default_rng(7)
    for time_s in times:
        start = int(round(time_s * rate))
        end = min(len(samples), start + body_len)
        samples[start:end] += 0.9 * body[: end - start] * envelope[: end - start]
        hat_end = min(len(samples), start + hat_len)
        noise = rng.standard_normal(hat_end - start)
        samples[start:hat_end] += 0.3 * noise * hat_window[: hat_end - start]
    data = np.clip(samples, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((data * 32767).astype("<i2").tobytes())
    return times


def make_offbeat_click(path, duration=8.0, bpm=120.0, offset=0.35):
    """Write on-beat clicks plus weaker off-beat pulses and return the on-beat times."""
    period = 60.0 / bpm
    on_beat = list(np.arange(0.0, duration, period))
    off_beat = [time_s + offset * period for time_s in on_beat if time_s + offset * period < duration]
    rate = 22050
    samples = np.zeros(int(rate * duration))
    pulse_len = int(0.02 * rate)
    pulse = np.sin(2 * np.pi * 1000 * np.arange(pulse_len) / rate)
    for time_s, amplitude in [(t, 0.9) for t in on_beat] + [(t, 0.4) for t in off_beat]:
        start = int(round(time_s * rate))
        end = min(len(samples), start + pulse_len)
        samples[start:end] += amplitude * pulse[: end - start]
    data = np.clip(samples, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((data * 32767).astype("<i2").tobytes())
    return on_beat


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


def test_beats_are_not_late(tmp_path, capsys):
    click = tmp_path / "attack.wav"
    true_times = make_attack_click(click)
    summary = analyse(capsys, click)
    detected = summary["beat_s"]
    assert detected
    offsets = []
    for beat in detected:
        nearest = min(true_times, key=lambda true: abs(true - beat))
        assert abs(beat - nearest) <= 0.01
        offsets.append(beat - nearest)
    assert float(np.mean(offsets)) <= 0.0


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
        "--engine",
        "--json",
        "--force",
    ):
        assert flag in out
    assert "examples:" in out


def test_beats_follow_a_drifting_tempo(tmp_path, capsys):
    click = tmp_path / "drift.wav"
    true_times = make_drifting_click(click)
    summary = analyse(capsys, click)
    detected = summary["beat_s"]
    assert detected
    for beat in detected:
        assert min(abs(beat - true) for true in true_times) <= 0.03
    matched = sum(1 for true in true_times if min(abs(true - beat) for beat in detected) <= 0.03)
    assert matched >= 0.9 * len(true_times)


def test_beats_ignore_off_beat_noise(tmp_path, capsys):
    click = tmp_path / "offbeat.wav"
    on_beat = make_offbeat_click(click)
    summary = analyse(capsys, click)
    detected = summary["beat_s"]
    assert detected
    for beat in detected:
        assert min(abs(beat - true) for true in on_beat) <= 0.03


def test_grid_is_a_local_maximum(tmp_path, capsys):
    click = tmp_path / "click.wav"
    make_click(click, duration=8.0)
    summary = analyse(capsys, click)
    grid = summary["beat_s"]
    with tempfile.TemporaryDirectory() as directory:
        samples = beats.decode_mono(click, directory)
    envelope = beats.onset_envelope(samples)

    def score(shift):
        total = 0.0
        for beat in grid:
            index = int(round((beat + shift) * beats.ANALYSIS_RATE / beats.STFT_HOP))
            if 0 <= index < len(envelope):
                total += envelope[index]
        return total

    shifts = [step * 0.005 for step in range(-20, 21)]
    scores = [score(shift) for shift in shifts]
    best = shifts[int(np.argmax(scores))]
    assert abs(best - beats.ONSET_LATENCY_S) <= 0.01


def test_engine_beat_this_stub(tmp_path, capsys, monkeypatch):
    click = tmp_path / "click.wav"
    make_click(click, duration=8.0)
    beat_s = np.arange(0.0, 8.0, 0.5)
    downbeat_s = beat_s[::4]
    monkeypatch.setattr(
        beats,
        "beat_this_beats",
        lambda samples, rate=beats.ANALYSIS_RATE: (beat_s, downbeat_s),
    )
    summary = analyse(capsys, click, "--engine", "beat_this")
    assert summary["bpm"] == pytest.approx(120.0, abs=0.5)
    assert summary["beat_s"] == [round(float(value), 4) for value in beat_s]
    assert summary["downbeat_s"] == [round(float(value), 4) for value in downbeat_s]
    numpy_summary = analyse(capsys, click)
    assert set(summary) == set(numpy_summary)


def test_engine_beat_this_missing_package(tmp_path, capsys, monkeypatch):
    click = tmp_path / "click.wav"
    make_click(click, duration=8.0)
    monkeypatch.setitem(sys.modules, "beat_this", None)
    monkeypatch.setitem(sys.modules, "beat_this.inference", None)
    code = beats.main([str(click), "--engine", "beat_this", "--json"])
    assert code == EXIT_USAGE
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["exit_code"] == EXIT_USAGE
    assert "pip install beat-this" in payload["error"]
    assert "[error]" in captured.err


def test_engine_bad_value(tmp_path, capsys):
    click = tmp_path / "click.wav"
    make_click(click, duration=8.0)
    with pytest.raises(SystemExit) as excinfo:
        beats.main([str(click), "--engine", "bogus"])
    assert excinfo.value.code == EXIT_USAGE


def test_engine_numpy_matches_default(tmp_path, capsys):
    click = tmp_path / "click.wav"
    make_click(click, duration=8.0)
    default = analyse(capsys, click)
    explicit = analyse(capsys, click, "--engine", "numpy")
    default.pop("wall_time_s")
    explicit.pop("wall_time_s")
    assert explicit == default


def write_config(tmp_path, engine):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "models": {"director": "deepseek/deepseek-v4.1-flash"},
                "llm_budget_usd": 1.0,
                "llm_backend": "mock",
                "beats": {"engine": engine},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_engine_comes_from_config(tmp_path, capsys, monkeypatch):
    click = tmp_path / "click.wav"
    make_click(click, duration=8.0)
    monkeypatch.delenv("YT_PIPELINE_BEATS_ENGINE", raising=False)
    monkeypatch.setenv("YT_CONFIG", str(write_config(tmp_path, "beat_this")))
    beat_s = np.arange(0.0, 8.0, 0.5)
    downbeat_s = beat_s[::4]
    monkeypatch.setattr(
        beats,
        "beat_this_beats",
        lambda samples, rate=beats.ANALYSIS_RATE: (beat_s, downbeat_s),
    )
    summary = analyse(capsys, click)
    assert summary["beat_s"] == [round(float(value), 4) for value in beat_s]
    assert summary["downbeat_s"] == [round(float(value), 4) for value in downbeat_s]


def test_engine_flag_overrides_config(tmp_path, capsys, monkeypatch):
    click = tmp_path / "click.wav"
    make_click(click, duration=8.0)
    monkeypatch.delenv("YT_PIPELINE_BEATS_ENGINE", raising=False)
    monkeypatch.setenv("YT_CONFIG", str(write_config(tmp_path, "beat_this")))
    called = []
    monkeypatch.setattr(
        beats,
        "beat_this_beats",
        lambda samples, rate=beats.ANALYSIS_RATE: called.append(True)
        or (np.zeros(0), np.zeros(0)),
    )
    summary = analyse(capsys, click, "--engine", "numpy")
    assert called == []
    assert summary["beats"] > 0


def test_engine_unknown_config_value_is_a_usage_error(tmp_path, capsys, monkeypatch):
    click = tmp_path / "click.wav"
    make_click(click, duration=8.0)
    monkeypatch.delenv("YT_PIPELINE_BEATS_ENGINE", raising=False)
    monkeypatch.setenv("YT_CONFIG", str(write_config(tmp_path, "bogus")))
    code = beats.main([str(click), "--json"])
    assert code == EXIT_USAGE
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["exit_code"] == EXIT_USAGE
    assert "[error]" in captured.err
