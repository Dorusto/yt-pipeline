import json
from pathlib import Path

import pytest

import shorts_generator as sg

CANDIDATES = {
    "schema_version": "0.1",
    "generated_at": "2026-10-21T00:00:00+00:00",
    "tool": "scripts/shorts_rank.py",
    "candidates": [
        {
            "rank": 1,
            "start_s": 1.5,
            "end_s": 25.0,
            "duration_s": 23.5,
            "score": 88.0,
            "hook": 90,
            "payoff": 86,
            "reason": "strong hook",
            "chapter_start_s": None,
            "chapter_title": None,
        },
        {
            "rank": 2,
            "start_s": 30.0,
            "end_s": 55.0,
            "duration_s": 25.0,
            "score": 80.0,
            "hook": 82,
            "payoff": 78,
            "reason": "clear payoff",
            "chapter_start_s": None,
            "chapter_title": None,
        },
    ],
}


def write_config(tmp_path, text):
    path = tmp_path / "shorts_config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def write_candidates(tmp_path, document=CANDIDATES):
    path = tmp_path / "shorts_candidates.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def write_srt(tmp_path):
    path = tmp_path / "Clip_RO.srt"
    path.write_text(
        "1\n00:00:00,000 --> 00:00:05,000\nunu doi trei\n\n"
        "2\n00:00:05,000 --> 00:00:10,000\npatru cinci\n\n",
        encoding="utf-8",
    )
    return path


def test_format_time_round_trips():
    assert sg.format_time(0.0) == "00:00:00.0"
    assert sg.format_time(12.5) == "00:00:12.5"
    assert sg.format_time(3661.25) == "01:01:01.2"
    assert sg.parse_time(sg.format_time(62.5)) == 62.5


def test_load_and_validate_config_skips_segments_when_not_required(monkeypatch, tmp_path):
    cfg = write_config(tmp_path, 'video: "Clip.mp4"\nsrt: "Clip_RO.srt"\n')
    monkeypatch.setattr(sg, "CONFIG_PATH", cfg)
    config = sg.load_and_validate_config(interactive=False, require_segments=False)
    assert config["video"] == "Clip.mp4"
    with pytest.raises(RuntimeError):
        sg.load_and_validate_config(interactive=False, require_segments=True)


def test_load_and_validate_config_still_demands_video_and_srt(monkeypatch, tmp_path):
    cfg = write_config(tmp_path, "segments:\n  - name: Hook\n    start: '00:00:00'\n    end: '00:00:10'\n")
    monkeypatch.setattr(sg, "CONFIG_PATH", cfg)
    with pytest.raises(RuntimeError):
        sg.load_and_validate_config(interactive=False, require_segments=False)


def test_load_candidates_segments(tmp_path):
    path = write_candidates(tmp_path)
    segments = sg.load_candidates_segments(str(path))
    assert segments == [
        {"name": "short1", "start": "00:00:01.5", "end": "00:00:25.0"},
        {"name": "short2", "start": "00:00:30.0", "end": "00:00:55.0"},
    ]


def test_load_candidates_segments_bad_file_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"candidates": []}', encoding="utf-8")
    with pytest.raises(RuntimeError):
        sg.load_candidates_segments(str(bad))


def test_main_with_candidates_uses_file_segments(monkeypatch, tmp_path, capsys):
    cfg = write_config(tmp_path, 'video: "Clip.mp4"\nsrt: "Clip_RO.srt"\n')
    monkeypatch.setattr(sg, "CONFIG_PATH", cfg)
    video = tmp_path / "Clip.mp4"
    video.write_bytes(b"")
    srt = write_srt(tmp_path)
    candidates = write_candidates(tmp_path)

    record = {
        "crop_w": 608,
        "crop_h": 1080,
        "points": [{"t_s": 0.0, "x": 656, "y": 0, "source": "face", "confidence": 0.9}],
    }
    monkeypatch.setattr(sg, "reframe_crop_path", lambda *args, **kwargs: record)
    renders = []

    def fake_render(video_path, crop_filter, start, end, ass_path, output_path, encoder=None):
        renders.append((start, end, Path(output_path).name))
        return encoder or "libx264"

    monkeypatch.setattr(sg, "render_short", fake_render)

    code = sg.main([
        "--video", str(video), "--srt", str(srt), "--candidates", str(candidates),
        "--yes", "--json", "--encoder", "libx264",
    ])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert [short["name"] for short in summary["shorts"]] == ["short1", "short2"]
    assert [short["start"] for short in summary["shorts"]] == ["00:00:01.5", "00:00:30.0"]
    assert renders == [(1.5, 25.0, "Short1-short1.mp4"), (30.0, 55.0, "Short2-short2.mp4")]


def test_main_bad_candidates_exit_1(monkeypatch, tmp_path, capsys):
    cfg = write_config(tmp_path, 'video: "Clip.mp4"\nsrt: "Clip_RO.srt"\n')
    monkeypatch.setattr(sg, "CONFIG_PATH", cfg)
    video = tmp_path / "Clip.mp4"
    video.write_bytes(b"")
    srt = write_srt(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text('{"candidates": []}', encoding="utf-8")

    code = sg.main([
        "--video", str(video), "--srt", str(srt), "--candidates", str(bad),
        "--yes", "--encoder", "libx264",
    ])
    assert code == 1
    captured = capsys.readouterr()
    assert "[error]" in captured.err
    assert "candidates" in captured.err
