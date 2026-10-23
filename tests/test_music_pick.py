import json
import subprocess
import sys
from pathlib import Path

import pytest

import music_pick

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "music_pick.py"

MEASUREMENTS = {
    "slow calm.mp3": {"bpm": 80.0, "duration_s": 200.0, "energy_mean_dbfs": -24.0, "energy_peak_dbfs": -12.0},
    "mid groove.mp3": {"bpm": 110.0, "duration_s": 180.0, "energy_mean_dbfs": -16.0, "energy_peak_dbfs": -8.0},
    "fast party.mp3": {"bpm": 140.0, "duration_s": 150.0, "energy_mean_dbfs": -10.0, "energy_peak_dbfs": -4.0},
    "gasoline rush.mp3": {"bpm": 128.0, "duration_s": 220.0, "energy_mean_dbfs": -12.0, "energy_peak_dbfs": -5.0},
}


def fake_measure(track, cache, force=False):
    return dict(MEASUREMENTS.get(track.name) or {})


def build_library(root, names=None):
    library = root / "music"
    library.mkdir(parents=True, exist_ok=True)
    for name in names or list(MEASUREMENTS):
        (library / name).write_bytes(b"fake")
    return library


def run_pick(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
    )


def test_calm_brief_prefers_the_slow_track(tmp_path, monkeypatch):
    monkeypatch.setattr(music_pick, "measure_track", fake_measure)
    library = build_library(tmp_path)
    out = tmp_path / "choice.json"
    summary = music_pick.run(library, brief="about 60 seconds, calm start", out=out)
    assert summary["chosen"] == "slow calm.mp3"
    assert Path(summary["path"]).is_absolute()
    assert "calm" in summary["why"]


def test_energetic_brief_prefers_the_fast_track(tmp_path, monkeypatch):
    monkeypatch.setattr(music_pick, "measure_track", fake_measure)
    library = build_library(tmp_path)
    summary = music_pick.run(library, brief="fast energetic party edit")
    assert summary["chosen"] == "fast party.mp3"


def test_shot_tags_lift_a_track(tmp_path, monkeypatch):
    monkeypatch.setattr(music_pick, "measure_track", fake_measure)
    monkeypatch.setattr(music_pick, "write_beats", lambda chosen, workspace: Path(workspace) / "beats.json")
    library = build_library(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    without = music_pick.run(library, brief="about 60 seconds", out=tmp_path / "no-tags.json")
    assert without["chosen"] != "gasoline rush.mp3"
    (workspace / "shotlog.json").write_text(
        json.dumps({"shots": [{"tags": ["gasoline", "rush"], "summary": "a gasoline station"}]}),
        encoding="utf-8",
    )
    with_tags = music_pick.run(library, brief="about 60 seconds", workspace=workspace)
    assert with_tags["chosen"] == "gasoline rush.mp3"
    winner = next(row for row in with_tags["scores"] if row["file"] == "gasoline rush.mp3")
    assert winner["tag"] == 1.0


def test_cache_is_written_and_reused(tmp_path, monkeypatch):
    calls = []

    def counting(track, cache, force=False):
        calls.append(track.name)
        data = fake_measure(track, cache, force)
        if cache is not None:
            music_pick.write_cached(cache, data)
        return data

    monkeypatch.setattr(music_pick, "measure_track", counting)
    library = build_library(tmp_path, ["slow calm.mp3"])
    cache = music_pick.cache_path(library, library / "slow calm.mp3")
    music_pick.run(library, brief="calm", out=tmp_path / "a.json")
    assert calls == ["slow calm.mp3"]
    assert cache.is_file()
    assert music_pick.load_cached(cache)["bpm"] == 80.0
    music_pick.run(library, brief="calm", out=tmp_path / "b.json", force=True)
    assert calls == ["slow calm.mp3", "slow calm.mp3"]


def test_empty_library_exits_one(tmp_path):
    library = tmp_path / "empty"
    library.mkdir()
    result = run_pick("--library", library, "--brief", "hello", "--json")
    assert result.returncode == 1
    assert "[error]" in result.stderr


def write_wav(path, seconds=3.0, frequency=220.0):
    import math
    import struct
    import wave

    rate = 22050
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * frequency * t / rate)))
            for t in range(int(rate * seconds))
        )
        handle.writeframes(frames)


def test_cli_json_summary_shape(tmp_path):
    library = build_library(tmp_path, ["slow calm.wav", "fast party.wav"])
    write_wav(library / "slow calm.wav", frequency=180.0)
    write_wav(library / "fast party.wav", frequency=440.0)
    result = run_pick("--library", library, "--brief", "calm", "--json", "--out", tmp_path / "c.json")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    for key in ("chosen", "path", "why", "scores", "wall_time_s", "llm_cost_usd", "tracks", "library", "status"):
        assert key in summary
    assert summary["llm_cost_usd"] == 0.0
    assert summary["tracks"] == 2
    assert json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))["chosen"] == summary["chosen"]


def test_workspace_writes_beats_json(tmp_path, monkeypatch):
    monkeypatch.setattr(music_pick, "measure_track", fake_measure)
    monkeypatch.setattr(
        music_pick,
        "write_beats",
        lambda chosen, workspace: (Path(workspace) / "beats.json").write_text("{}", encoding="utf-8"),
    )
    library = build_library(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    summary = music_pick.run(library, brief="calm", workspace=workspace)
    assert (workspace / "beats.json").is_file()
    assert (workspace / "music_choice.json").is_file()
    assert summary["chosen"] == "slow calm.mp3"


CONFIG_WITHOUT_MUSIC = (
    "models:\n  director: deepseek/deepseek-v4.1-flash\n"
    "llm_budget_usd: 5.0\nllm_backend: mock\n"
)


def write_config(tmp_path, library=None):
    text = CONFIG_WITHOUT_MUSIC
    if library is not None:
        text += f"music:\n  library: {library}\n"
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def isolate_library_config(tmp_path, monkeypatch, library=None):
    monkeypatch.delenv("YT_PIPELINE_MUSIC_LIBRARY", raising=False)
    monkeypatch.setenv("YT_CONFIG", str(write_config(tmp_path, library=library)))


def test_missing_library_names_the_three_ways(tmp_path, monkeypatch):
    isolate_library_config(tmp_path, monkeypatch)
    result = run_pick("--brief", "calm", "--list")
    assert result.returncode == 1
    for way in ("--library", "YT_PIPELINE_MUSIC_LIBRARY", "music.library"):
        assert way in result.stderr
    result = run_pick("--brief", "calm")
    assert result.returncode == 1
    assert "no music library configured" in result.stderr


def test_resolve_library_chain(tmp_path, monkeypatch):
    isolate_library_config(tmp_path, monkeypatch)
    with pytest.raises(music_pick.MusicPickError) as exc:
        music_pick.resolve_library(None)
    assert "no music library configured" in str(exc.value)
    monkeypatch.setenv("YT_PIPELINE_MUSIC_LIBRARY", str(tmp_path / "from-env"))
    assert music_pick.resolve_library(None) == (tmp_path / "from-env").expanduser()
    assert music_pick.resolve_library(str(tmp_path / "from-flag")) == (tmp_path / "from-flag").expanduser()


def test_library_from_env_and_config(tmp_path, monkeypatch):
    library = build_library(tmp_path, ["slow calm.mp3"])
    isolate_library_config(tmp_path, monkeypatch, library=str(library))
    result = run_pick("--list", "--json")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["library"] == str(library.resolve())
    assert summary["tracks"][0]["file"] == "slow calm.mp3"
    isolate_library_config(tmp_path, monkeypatch)
    monkeypatch.setenv("YT_PIPELINE_MUSIC_LIBRARY", str(library))
    summary = json.loads(run_pick("--list", "--json").stdout)
    assert summary["library"] == str(library.resolve())


def test_list_reports_duration_bpm_and_licence(tmp_path, monkeypatch):
    monkeypatch.setattr(music_pick, "measure_track", fake_measure)
    library = build_library(tmp_path)
    (library / "slow calm.license").write_text("cc-by", encoding="utf-8")
    (library / "LICENCE.txt").write_text("cc0", encoding="utf-8")
    summary = music_pick.run_list(library)
    assert summary["status"] == "listed"
    assert summary["llm_cost_usd"] == 0.0
    rows = {row["file"]: row for row in summary["tracks"]}
    assert rows["slow calm.mp3"]["licence_status"] == "sidecar"
    assert rows["slow calm.mp3"]["licence_file"].endswith("slow calm.license")
    assert rows["mid groove.mp3"]["licence_status"] == "library"
    assert rows["mid groove.mp3"]["licence_file"].endswith("LICENCE.txt")
    assert rows["mid groove.mp3"]["duration_s"] == 180.0
    assert rows["mid groove.mp3"]["bpm"] == 110.0
    for row in rows.values():
        assert Path(row["path"]).is_absolute()


def test_list_marks_missing_licence_and_keeps_unmeasurable_tracks(tmp_path, monkeypatch):
    monkeypatch.setattr(music_pick, "measure_track", lambda track, cache, force=False: None)
    library = build_library(tmp_path, ["mid groove.mp3"])
    summary = music_pick.run_list(library)
    row = summary["tracks"][0]
    assert row["licence_status"] == "missing"
    assert row["licence_file"] is None
    assert row["bpm"] is None
    assert row["duration_s"] is None


def test_cli_list_json_shape(tmp_path):
    library = build_library(tmp_path, ["slow calm.wav", "fast party.wav"])
    write_wav(library / "slow calm.wav", frequency=180.0)
    write_wav(library / "fast party.wav", frequency=440.0)
    (library / "fast party.licence").write_text("cc-by", encoding="utf-8")
    result = run_pick("--library", library, "--list", "--json")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "listed"
    assert summary["wall_time_s"] >= 0.0
    assert summary["llm_cost_usd"] == 0.0
    assert {row["file"] for row in summary["tracks"]} == {"slow calm.wav", "fast party.wav"}
    for row in summary["tracks"]:
        for key in ("path", "duration_s", "bpm", "licence_status", "licence_file"):
            assert key in row
        assert row["duration_s"] is not None
        assert row["bpm"] is not None
    plain = run_pick("--library", library, "--list")
    assert plain.returncode == 0
    assert "licence: sidecar (fast party.licence)" in plain.stdout
    assert "2 tracks" in plain.stdout


def test_broken_config_is_one_error_line_and_exit_two(tmp_path, monkeypatch):
    bad = tmp_path / "config.yaml"
    bad.write_text("models: []\n", encoding="utf-8")
    monkeypatch.delenv("YT_PIPELINE_MUSIC_LIBRARY", raising=False)
    monkeypatch.setenv("YT_CONFIG", str(bad))
    result = run_pick("--brief", "calm", "--list")
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert result.stderr.startswith("[error]")
    assert "Traceback" not in result.stderr


def test_invalid_yaml_config_is_not_a_traceback(tmp_path, monkeypatch):
    bad = tmp_path / "config.yaml"
    bad.write_text("models: [\ninvalid: yaml\n", encoding="utf-8")
    monkeypatch.delenv("YT_PIPELINE_MUSIC_LIBRARY", raising=False)
    monkeypatch.setenv("YT_CONFIG", str(bad))
    result = run_pick("--brief", "calm", "--list")
    assert result.returncode == 2
    assert result.stderr.startswith("[error]")
    assert "Traceback" not in result.stderr


def test_no_library_message_is_the_shared_one(tmp_path, monkeypatch):
    import common.config as config_module

    isolate_library_config(tmp_path, monkeypatch)
    with pytest.raises(music_pick.MusicPickError) as exc:
        music_pick.resolve_library(None)
    assert str(exc.value) == config_module.no_music_library_message("--library")
