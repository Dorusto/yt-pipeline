import json
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
        "v.mp4", sg.fixed_crop_filter(0), 0.0, 1.0, "a.ass", str(tmp_path / "o.mp4"), encoder="h264_nvenc"
    )
    assert used == "libx264"
    assert len(calls) == 2


def test_render_short_reraises_without_libx264(monkeypatch, tmp_path):
    def fake_run(cmd, check=True, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(sg.subprocess, "run", fake_run)
    monkeypatch.setattr(sg, "available_video_encoders", lambda: {"h264_nvenc"})
    with pytest.raises(subprocess.CalledProcessError):
        sg.render_short("v.mp4", sg.fixed_crop_filter(0), 0.0, 1.0, "a.ass", "o.mp4", encoder="h264_nvenc")


def test_fixed_crop_filter_is_the_old_shape():
    assert sg.fixed_crop_filter(800) == "crop=608:1080:800:0"


def test_piecewise_expression_is_linear_between_points():
    points = [
        {"t_s": 0.0, "x": 100, "y": 0},
        {"t_s": 1.0, "x": 200, "y": 0},
        {"t_s": 2.0, "x": 200, "y": 0},
    ]
    expr = sg.piecewise_expression(points, "x")
    assert expr.startswith("if(lt(t,")
    assert "100.00+(t-0.000)*100.0000" in expr
    assert expr.count("if(lt(t,") == 2
    assert expr.endswith(")")
    assert sg.piecewise_expression([{"t_s": 0.0, "x": 42, "y": 0}], "x") == "42.00"


def test_crop_filter_from_path_constant_uses_fixed_crop():
    record = {
        "crop_w": 608,
        "crop_h": 1080,
        "points": [
            {"t_s": 0.0, "x": 656, "y": 0, "source": "face", "confidence": 0.9},
            {"t_s": 1.0, "x": 656, "y": 0, "source": "face", "confidence": 0.9},
        ],
    }
    assert sg.crop_filter_from_path(record) == "crop=608:1080:656:0"


def test_crop_filter_from_path_moving_quotes_expression():
    record = {
        "crop_w": 608,
        "crop_h": 1080,
        "points": [
            {"t_s": 0.0, "x": 100, "y": 0, "source": "face", "confidence": 0.9},
            {"t_s": 1.0, "x": 300, "y": 0, "source": "face", "confidence": 0.9},
        ],
    }
    crop = sg.crop_filter_from_path(record)
    assert crop.startswith("crop=608:1080:'")
    assert crop.endswith(":0")
    assert "if(lt(t," in crop


def test_crop_filter_from_path_rejects_other_crop_sizes():
    record = {
        "crop_w": 405,
        "crop_h": 720,
        "points": [{"t_s": 0.0, "x": 0, "y": 0, "source": "face", "confidence": 0.9}],
    }
    with pytest.raises(RuntimeError):
        sg.crop_filter_from_path(record)


def test_simplify_points_keeps_the_ends():
    points = [{"t_s": float(i), "x": i, "y": 0} for i in range(120)]
    simplified = sg.simplify_points(points, max_points=40)
    assert len(simplified) == 40
    assert simplified[0] == points[0]
    assert simplified[-1] == points[-1]


def test_reframe_crop_path_invokes_the_tool(monkeypatch, tmp_path):
    out = tmp_path / "seg_reframe.json"
    record = {
        "crop_w": 608,
        "crop_h": 1080,
        "points": [{"t_s": 0.0, "x": 10, "y": 0, "source": "face", "confidence": 0.9}],
    }

    def fake_run(cmd, check=False, **kwargs):
        out.write_text(json.dumps(record), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(sg.subprocess, "run", fake_run)
    loaded = sg.reframe_crop_path("v.mp4", 0.0, 5.0, out)
    assert loaded["crop_w"] == 608
    assert out.exists()


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