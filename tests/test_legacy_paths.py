import os
import sys
from pathlib import Path

os.environ.setdefault("OPENROUTER_API_KEY", "test")

import analyze_srt
import daily_summary_srt
import translate_srt

SAMPLE_SRT = "1\n00:00:00,000 --> 00:00:02,000\nsalut lume\n\n"


def test_make_output_dir_returns_video_parent(tmp_path):
    video = tmp_path / "sub" / "clip.mp4"
    video.parent.mkdir()
    video.write_bytes(b"")
    assert analyze_srt.make_output_dir(video) == video.resolve().parent
    assert analyze_srt.make_output_dir(str(video)) == video.resolve().parent


def test_group_by_day_returns_paths(tmp_path):
    transcripts = tmp_path / "transcripts_raw"
    transcripts.mkdir()
    srt = transcripts / "DJI_20260101120000_001_D.srt"
    srt.write_text(SAMPLE_SRT, encoding="utf-8")
    days, skipped = daily_summary_srt.group_by_day(transcripts)
    assert skipped == []
    assert list(days) == ["2026-01-01"]
    assert days["2026-01-01"][0][1] == srt


def test_daily_summary_default_output_next_to_parent(tmp_path, monkeypatch):
    transcripts = tmp_path / "transcripts_raw"
    transcripts.mkdir()
    (transcripts / "DJI_20260101120000_001_D.srt").write_text(SAMPLE_SRT, encoding="utf-8")
    monkeypatch.setattr(daily_summary_srt, "summarize_day", lambda date, text: "rezumat")
    monkeypatch.setattr(sys, "argv", ["daily_summary_srt.py", str(transcripts)])
    daily_summary_srt.main()
    assert (tmp_path / "rezumat_pe_zile.md").exists()


def test_translate_en_output_next_to_video(tmp_path, monkeypatch):
    srt = tmp_path / "subtitles" / "Clip_RO.srt"
    srt.parent.mkdir()
    srt.write_text(SAMPLE_SRT, encoding="utf-8")
    video = tmp_path / "video" / "Clip.mp4"
    video.parent.mkdir()
    video.write_bytes(b"")
    monkeypatch.setattr(translate_srt, "translate_batch", lambda texts: {})
    monkeypatch.setattr(sys, "argv", ["translate_srt.py", str(srt), str(video)])
    translate_srt.main()
    assert (video.parent / "Clip_RO_EN.srt").exists()


def test_translate_default_en_name_next_to_input(tmp_path, monkeypatch):
    srt = tmp_path / "Clip_RO.srt"
    srt.write_text(SAMPLE_SRT, encoding="utf-8")
    monkeypatch.setattr(translate_srt, "translate_batch", lambda texts: {})
    monkeypatch.setattr(sys, "argv", ["translate_srt.py", str(srt)])
    translate_srt.main()
    assert (tmp_path / "Clip_RO_EN.srt").exists()
