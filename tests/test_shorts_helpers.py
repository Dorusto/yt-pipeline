import subprocess
from pathlib import Path

import pysubs2
import pytest

import shorts_generator as sg

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_RO.srt"


def test_module_imports_without_torch_or_whisperx():
    assert not hasattr(sg, "torch")
    assert not hasattr(sg, "whisperx")


def test_parse_time():
    assert sg.parse_time("00:01:02.500") == 62.5
    assert sg.parse_time("00:00:00") == 0.0


def test_parse_srt_full_range_returns_all_entries():
    segments = sg.parse_srt_for_alignment(str(SAMPLE), 0.0, 35.0)
    assert len(segments) == 10
    assert segments[0]["start"] == 0.0
    assert segments[-1]["end"] == pytest.approx(35.0)


def test_parse_srt_drops_entries_outside_range():
    segments = sg.parse_srt_for_alignment(str(SAMPLE), 0.0, 7.0)
    assert len(segments) == 2


def test_parse_srt_trims_straddling_start_at_sentence_boundary(tmp_path):
    srt = tmp_path / "trim.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:10,000\n"
        "unu doi trei patru. cinci șase șapte opt nouă zece.\n\n",
        encoding="utf-8",
    )
    segments = sg.parse_srt_for_alignment(str(srt), 1.0, 10.0)
    assert segments[0]["start"] == 0.0
    assert segments[0]["text"].startswith("cinci")


def test_words_from_srt_segments_are_monotonic():
    segments = sg.parse_srt_for_alignment(str(SAMPLE), 0.0, 35.0)
    words = sg.words_from_srt_segments(segments)
    assert len(words) > 50
    starts = [w["start"] for w in words]
    assert starts == sorted(starts)


def test_merge_hyphenated():
    words = [
        {"word": "task", "start": 0.0, "end": 0.5},
        {"word": "-ul", "start": 0.5, "end": 0.7},
        {"word": "meu", "start": 0.7, "end": 1.0},
    ]
    merged = sg.merge_hyphenated(words)
    assert [w["word"] for w in merged] == ["task-ul", "meu"]


def test_generate_karaoke_ass_writes_events(tmp_path):
    words = sg.words_from_srt_segments(sg.parse_srt_for_alignment(str(SAMPLE), 0.0, 7.0))
    out = tmp_path / "k.ass"
    sg.generate_karaoke_ass(words, str(out))
    subs = pysubs2.load(str(out))
    assert len(subs) > 0


def test_render_short_falls_back_to_libx264(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, check=True, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(sg.subprocess, "run", fake_run)
    monkeypatch.setattr(sg, "available_video_encoders", lambda: {"libx264"})
    used = sg.render_short(
        "v.mp4", 0, 0.0, 1.0, "a.ass", str(tmp_path / "o.mp4"), encoder="h264_nvenc"
    )
    assert used == "libx264"
    assert len(calls) == 2


def test_render_short_reraises_without_libx264(monkeypatch, tmp_path):
    def fake_run(cmd, check=True, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(sg.subprocess, "run", fake_run)
    monkeypatch.setattr(sg, "available_video_encoders", lambda: {"h264_nvenc"})
    with pytest.raises(subprocess.CalledProcessError):
        sg.render_short("v.mp4", 0, 0.0, 1.0, "a.ass", "o.mp4", encoder="h264_nvenc")


def test_missing_config_non_interactive_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(sg, "CONFIG_PATH", tmp_path / "absent.yaml")
    with pytest.raises(RuntimeError):
        sg.load_and_validate_config(interactive=False)


def test_incomplete_config_non_interactive_raises(monkeypatch, tmp_path):
    cfg = tmp_path / "shorts_config.yaml"
    cfg.write_text("video: MyVideo.mp4\n", encoding="utf-8")
    monkeypatch.setattr(sg, "CONFIG_PATH", cfg)
    with pytest.raises(RuntimeError):
        sg.load_and_validate_config(interactive=False)


def test_main_returns_1_when_config_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sg, "CONFIG_PATH", tmp_path / "absent.yaml")
    assert sg.main(["--yes", "--json"]) == 1
    assert '"ok": false' in capsys.readouterr().out