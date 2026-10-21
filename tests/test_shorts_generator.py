import json
import re
import shutil
import subprocess
from pathlib import Path

import pysubs2
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

    def fake_render(video_path, crop_filter, start, end, ass_path, output_path, encoder=None, font_dir=None):
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


def write_words(tmp_path, words=None):
    path = tmp_path / "Clip_words.json"
    path.write_text(json.dumps(words or [
        {"word": "chezve", "start": 2.0, "end": 2.5},
        {"word": "bun", "start": 2.6, "end": 3.0},
        {"word": "prea", "start": 3.1, "end": 3.4},
    ]), encoding="utf-8")
    return path


def write_corrections(tmp_path, text="chezve|cezve\n"):
    path = tmp_path / "corrections.txt"
    path.write_text(text, encoding="utf-8")
    return path


def read_style_line(ass_path):
    for line in Path(ass_path).read_text(encoding="utf-8").splitlines():
        if line.startswith("Style: Default,"):
            return line
    raise AssertionError("no Default style line in the ASS")


def test_words_for_segment_uses_midpoint_and_shifts():
    words = [
        {"word": "before", "start": 0.0, "end": 0.4},
        {"word": "inside", "start": 1.0, "end": 1.4},
        {"word": "straddles", "start": 1.8, "end": 2.6},
        {"word": "after", "start": 3.0, "end": 3.4},
    ]
    selected = sg.words_for_segment(words, 1.0, 2.0)
    assert [word["word"] for word in selected] == ["inside"]
    assert selected[0]["start"] == 0.0
    assert selected[0]["end"] == pytest.approx(0.4)


def test_split_phrases_on_punctuation_and_pause():
    words = [
        {"word": "Salut.", "start": 0.0, "end": 0.5},
        {"word": "Ce", "start": 0.5, "end": 0.7},
        {"word": "faci?", "start": 0.7, "end": 1.0},
        {"word": "unu", "start": 1.0, "end": 1.4},
        {"word": "doi", "start": 2.2, "end": 2.6},
    ]
    phrases = sg.split_phrases(words)
    assert [[word["word"] for word in phrase] for phrase in phrases] == [
        ["Salut."],
        ["Ce", "faci?"],
        ["unu"],
        ["doi"],
    ]


def test_pack_lines_respects_the_character_limit():
    phrase = [
        {"word": word, "start": index, "end": index + 0.5}
        for index, word in enumerate("unu doi trei patru cinci sase sapte opt noua zece".split())
    ]
    lines = sg.pack_lines(phrase)
    assert all(len(" ".join(word["word"] for word in line)) <= sg.LINE_MAX_CHARS for line in lines)
    assert [word["word"] for line in lines for word in line] == [word["word"] for word in phrase]


def test_pack_lines_gives_a_long_word_its_own_line():
    phrase = [
        {"word": "a" * 25, "start": 0.0, "end": 0.5},
        {"word": "b", "start": 0.5, "end": 0.7},
    ]
    lines = sg.pack_lines(phrase)
    assert lines[0] == [phrase[0]]
    assert lines[1] == [phrase[1]]


def test_caption_lines_caps_at_two_lines_and_splits_long_phrases():
    phrase = [
        {"word": word, "start": index, "end": index + 0.5}
        for index, word in enumerate("unu doi trei patru cinci sase sapte opt noua zece unsprezece doisprezece".split())
    ]
    captions = sg.caption_lines(phrase)
    assert all(len(caption) <= sg.CAPTION_MAX_LINES for caption in captions)
    assert len(captions) > 1
    assert [word["word"] for caption in captions for line in caption for word in line] == [
        word["word"] for word in phrase
    ]


def test_generate_karaoke_ass_merges_hyphenated_before_breaking(tmp_path):
    words = [
        {"word": "task", "start": 0.0, "end": 0.4},
        {"word": "-ul", "start": 0.4, "end": 0.6},
        {"word": "meu.", "start": 0.6, "end": 1.0},
    ]
    out = tmp_path / "k.ass"
    sg.generate_karaoke_ass(words, str(out))
    text = out.read_text(encoding="utf-8")
    assert "task-ul" in text
    assert "-ul" not in text.replace("task-ul", "")


def test_generate_karaoke_ass_style_comes_from_the_brand(tmp_path):
    brand, _ = sg.load_brand_kit()
    words = [{"word": "salut", "start": 0.0, "end": 0.5}]
    out = tmp_path / "k.ass"
    sg.generate_karaoke_ass(words, str(out), brand, safe_zone_frac=0.33)
    style = read_style_line(out)
    fields = style.split(",")
    assert fields[1] == brand["fonts"]["caption"]["family"]
    assert int(fields[2]) == round(brand["fonts"]["caption"]["size_px"] * 1080 / brand["canvas"]["height"])
    assert fields[3] == "&H00E6EBED"
    assert fields[5] == "&H0018120F"
    assert int(fields[21]) == round(1080 * 0.33)
    text = out.read_text(encoding="utf-8")
    assert "&H5AA2E8&" in text


def test_generate_karaoke_ass_marginv_follows_safe_zone(tmp_path):
    brand, _ = sg.load_brand_kit()
    words = [{"word": "salut", "start": 0.0, "end": 0.5}]
    out = tmp_path / "k.ass"
    sg.generate_karaoke_ass(words, str(out), brand, safe_zone_frac=0.5)
    assert int(read_style_line(out).split(",")[21]) == round(1080 * 0.5)


def test_generate_karaoke_ass_scale_active_word_only(tmp_path):
    brand, _ = sg.load_brand_kit()
    words = [
        {"word": "unu", "start": 0.0, "end": 0.5},
        {"word": "doi", "start": 0.5, "end": 1.0},
    ]
    out = tmp_path / "k.ass"
    sg.generate_karaoke_ass(words, str(out), brand, scale_active_word=True)
    text = out.read_text(encoding="utf-8")
    assert text.count(r"\fscx106\fscy106") == 2
    assert r"\fscx100\fscy100" in text


def test_missing_brand_font_is_a_hard_error(tmp_path):
    brand, _ = sg.load_brand_kit()
    brand["fonts"]["caption"]["path"] = "assets/fonts/DoesNotExist.ttf"
    brand_path = tmp_path / "brand.json"
    brand_path.write_text(json.dumps(brand), encoding="utf-8")
    loaded, loaded_path = sg.load_brand_kit(str(brand_path))
    with pytest.raises(RuntimeError) as excinfo:
        sg.resolve_caption_font_dir(loaded, loaded_path.parent)
    message = str(excinfo.value)
    assert "DoesNotExist.ttf" in message
    assert "fonts.caption" in message


def test_karaoke_settings_defaults_and_non_mapping():
    assert sg.karaoke_settings({}) == (0.33, False)
    assert sg.karaoke_settings({"karaoke": {"safe_zone_frac": 0.4, "scale_active_word": True}}) == (0.4, True)
    with pytest.raises(RuntimeError):
        sg.karaoke_settings({"karaoke": "nope"})


def test_render_short_adds_fontsdir_when_given(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, check=True, **kwargs):
        calls.append(cmd)

    monkeypatch.setattr(sg.subprocess, "run", fake_run)
    sg.render_short(
        "v.mp4", sg.fixed_crop_filter(0), 0.0, 1.0, "a.ass", str(tmp_path / "o.mp4"),
        encoder="libx264", font_dir="/fonts",
    )
    vf = calls[0][calls[0].index("-vf") + 1]
    assert "ass=a.ass:fontsdir=/fonts" in vf


def test_render_short_without_font_dir_keeps_the_old_filter(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, check=True, **kwargs):
        calls.append(cmd)

    monkeypatch.setattr(sg.subprocess, "run", fake_run)
    sg.render_short("v.mp4", sg.fixed_crop_filter(0), 0.0, 1.0, "a.ass", str(tmp_path / "o.mp4"), encoder="libx264")
    vf = calls[0][calls[0].index("-vf") + 1]
    assert "ass=a.ass" in vf
    assert "fontsdir" not in vf


def test_main_with_words_applies_corrections_and_reports_source(monkeypatch, tmp_path, capsys):
    cfg = write_config(tmp_path, 'video: "Clip.mp4"\nsrt: "Clip_RO.srt"\n')
    monkeypatch.setattr(sg, "CONFIG_PATH", cfg)
    video = tmp_path / "Clip.mp4"
    video.write_bytes(b"")
    srt = write_srt(tmp_path)
    words = write_words(tmp_path)
    corrections = write_corrections(tmp_path)
    candidates = write_candidates(tmp_path)

    record = {
        "crop_w": 608,
        "crop_h": 1080,
        "points": [{"t_s": 0.0, "x": 656, "y": 0, "source": "face", "confidence": 0.9}],
    }
    monkeypatch.setattr(sg, "reframe_crop_path", lambda *args, **kwargs: record)
    monkeypatch.setattr(sg, "render_short", lambda *args, **kwargs: "libx264")

    code = sg.main([
        "--video", str(video), "--srt", str(srt), "--candidates", str(candidates),
        "--words", str(words), "--corrections", str(corrections),
        "--yes", "--json", "--encoder", "libx264",
    ])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["llm_cost_usd"] == 0.0
    assert "wall_time_s" in summary
    assert [short["words_source"] for short in summary["shorts"]] == ["words.json", "words.json"]

    ass = tmp_path / "auto" / "short1_karaoke.ass"
    text = ass.read_text(encoding="utf-8")
    assert "cezve" in text
    assert "chezve" not in text
    subs = pysubs2.load(str(ass))
    assert subs[0].start == 500


def test_main_without_words_reports_srt_source(monkeypatch, tmp_path, capsys):
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
    monkeypatch.setattr(sg, "render_short", lambda *args, **kwargs: "libx264")

    code = sg.main([
        "--video", str(video), "--srt", str(srt), "--candidates", str(candidates),
        "--yes", "--json", "--encoder", "libx264",
    ])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert [short["words_source"] for short in summary["shorts"]] == ["srt", "srt"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_end_to_end_with_and_without_words(monkeypatch, tmp_path):
    video = tmp_path / "Clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=25:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(video),
        ],
        check=True, capture_output=True,
    )
    srt = write_srt(tmp_path)
    words = write_words(tmp_path)
    cfg = write_config(
        tmp_path,
        'video: "Clip.mp4"\nsrt: "Clip_RO.srt"\n'
        "segments:\n  - name: Hook\n    start: \"00:00:00\"\n    end: \"00:00:03\"\n    x_offset: 656\n",
    )
    monkeypatch.setattr(sg, "CONFIG_PATH", cfg)

    code = sg.main([
        "--video", str(video), "--srt", str(srt), "--words", str(words),
        "--yes", "--json", "--encoder", "libx264",
    ])
    assert code == 0
    ass = tmp_path / "auto" / "Hook_karaoke.ass"
    short = tmp_path / "shorts" / "Short1-Hook.mp4"
    assert ass.exists()
    assert short.exists()
    text = ass.read_text(encoding="utf-8")
    assert "Montserrat Black" in text
    assert "&H5AA2E8&" in text

    code = sg.main([
        "--video", str(video), "--srt", str(srt),
        "--yes", "--json", "--encoder", "libx264",
    ])
    assert code == 0
    assert ass.exists()
    assert short.exists()


SPLIT_RE = re.compile(
    r"^split=2\[fa\]\[sc\];"
    r"\[fa\]crop=(\d+):(\d+):(.+?):(.+?),scale=608:(\d+)\[fc\];"
    r"\[sc\]crop=(\d+):(\d+):([\d.]+):([\d.]+),scale=608:(\d+)\[sc2\];"
    r"\[fc\]\[sc2\]vstack$"
)


def split_parts(filter_str):
    match = SPLIT_RE.match(filter_str)
    assert match, filter_str
    return match.groups()


def make_clip(tmp_path):
    video = tmp_path / "Clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=25:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(video),
        ],
        check=True, capture_output=True,
    )
    return video


def test_resolve_layout_priority_and_default():
    assert sg.resolve_layout({"layout": "split"}, {"layout": "face"}) == "split"
    assert sg.resolve_layout({}, {"layout": "split"}) == "split"
    assert sg.resolve_layout({}, {}) == "face"
    with pytest.raises(RuntimeError) as excinfo:
        sg.resolve_layout({"layout": "bogus"}, {})
    assert "bogus" in str(excinfo.value)


def test_split_settings_defaults_and_non_mapping():
    assert sg.split_settings({}) == sg.DEFAULT_SPLIT
    merged = sg.split_settings({"split": {"face_cell_frac": 0.5}})
    assert merged["face_cell_frac"] == 0.5
    assert merged["face_h_frac"] == sg.DEFAULT_SPLIT["face_h_frac"]
    with pytest.raises(RuntimeError):
        sg.split_settings({"split": "nope"})


def test_load_candidates_segments_passes_layout(tmp_path):
    document = json.loads(json.dumps(CANDIDATES))
    document["candidates"][0]["layout"] = "split"
    path = write_candidates(tmp_path, document)
    segments = sg.load_candidates_segments(str(path))
    assert segments[0]["layout"] == "split"
    assert "layout" not in segments[1]


def test_split_filter_from_path_geometry():
    record = {"crop_w": 608, "crop_h": 1080, "points": [{"t_s": 0.0, "x": 656, "y": 0}]}
    split = sg.split_settings({})
    filter_str = sg.split_filter_from_path(record, split)
    w1, h1, x1, y1, face_cell, w2, h2, x2, y2, screen_cell = split_parts(filter_str)
    assert int(face_cell) == round(1080 * split["face_cell_frac"])
    assert int(screen_cell) == 1080 - int(face_cell)
    assert int(h1) == round(1080 * split["face_h_frac"])
    assert int(h2) == round(1080 * split["screen_h_frac"])
    assert abs(int(w1) / int(h1) - 608 / int(face_cell)) < 0.01
    assert abs(int(w2) / int(h2) - 608 / int(screen_cell)) < 0.01
    assert float(x1) == 656 + 304 - int(w1) / 2
    assert float(y1) == 0 + 540 - int(h1) / 2
    assert float(x2) == 1920 * split["screen_x_frac"] - int(w2) / 2
    assert float(y2) == 1080 * split["screen_y_frac"] - int(h2) / 2
    assert filter_str.endswith("vstack")


def test_split_filter_clamps_face_window_inside_the_frame():
    split = sg.split_settings({})
    left = sg.split_filter_from_path(
        {"crop_w": 608, "crop_h": 1080, "points": [{"t_s": 0.0, "x": 0, "y": 0}]}, split
    )
    _, _, x1, y1, _, _, _, _, _, _ = split_parts(left)
    assert float(x1) >= 0
    assert float(y1) >= 0
    right = sg.split_filter_from_path(
        {"crop_w": 608, "crop_h": 1080, "points": [{"t_s": 0.0, "x": 1312, "y": 1080}]}, split
    )
    w1, h1, x1, y1, _, _, _, _, _, _ = split_parts(right)
    assert float(x1) + int(w1) <= 1920
    assert float(y1) + int(h1) <= 1080


def test_split_filter_screen_window_follows_fractions():
    split = sg.split_settings({"split": {"screen_x_frac": 0.25, "screen_y_frac": 0.6}})
    filter_str = sg.split_filter_from_path(
        {"crop_w": 608, "crop_h": 1080, "points": [{"t_s": 0.0, "x": 656, "y": 0}]}, split
    )
    _, _, _, _, _, w2, h2, x2, y2, _ = split_parts(filter_str)
    assert float(x2) == 1920 * 0.25 - int(w2) / 2
    assert float(y2) == 1080 * 0.6 - int(h2) / 2


def test_main_split_ignores_x_offset(monkeypatch, tmp_path, capsys):
    cfg = write_config(
        tmp_path,
        'video: "Clip.mp4"\nsrt: "Clip_RO.srt"\n'
        "segments:\n  - name: Hook\n    start: \"00:00:00\"\n    end: \"00:00:03\"\n"
        "    x_offset: 800\n    layout: split\n",
    )
    monkeypatch.setattr(sg, "CONFIG_PATH", cfg)
    video = tmp_path / "Clip.mp4"
    video.write_bytes(b"")
    srt = write_srt(tmp_path)
    record = {"crop_w": 608, "crop_h": 1080, "points": [{"t_s": 0.0, "x": 656, "y": 0}]}
    monkeypatch.setattr(sg, "reframe_crop_path", lambda *args, **kwargs: record)
    filters = []

    def fake_render(video_path, crop_filter, *args, **kwargs):
        filters.append(crop_filter)
        return "libx264"

    monkeypatch.setattr(sg, "render_short", fake_render)

    code = sg.main(["--video", str(video), "--srt", str(srt), "--yes", "--encoder", "libx264"])
    assert code == 0
    assert filters[0].startswith("split=2")
    assert "x_offset ignored in split layout" in capsys.readouterr().out


def test_main_bad_layout_exit_1(monkeypatch, tmp_path, capsys):
    cfg = write_config(
        tmp_path,
        'video: "Clip.mp4"\nsrt: "Clip_RO.srt"\nlayout: bogus\n'
        "segments:\n  - name: Hook\n    start: \"00:00:00\"\n    end: \"00:00:03\"\n    x_offset: 656\n",
    )
    monkeypatch.setattr(sg, "CONFIG_PATH", cfg)
    video = tmp_path / "Clip.mp4"
    video.write_bytes(b"")
    srt = write_srt(tmp_path)

    code = sg.main(["--video", str(video), "--srt", str(srt), "--yes", "--encoder", "libx264"])
    assert code == 1
    assert "bogus" in capsys.readouterr().err


def test_main_candidate_layout_is_used(monkeypatch, tmp_path, capsys):
    cfg = write_config(tmp_path, 'video: "Clip.mp4"\nsrt: "Clip_RO.srt"\n')
    monkeypatch.setattr(sg, "CONFIG_PATH", cfg)
    video = tmp_path / "Clip.mp4"
    video.write_bytes(b"")
    srt = write_srt(tmp_path)
    document = json.loads(json.dumps(CANDIDATES))
    document["candidates"][0]["layout"] = "split"
    candidates = write_candidates(tmp_path, document)
    record = {"crop_w": 608, "crop_h": 1080, "points": [{"t_s": 0.0, "x": 656, "y": 0}]}
    monkeypatch.setattr(sg, "reframe_crop_path", lambda *args, **kwargs: record)
    monkeypatch.setattr(sg, "render_short", lambda *args, **kwargs: "libx264")

    code = sg.main([
        "--video", str(video), "--srt", str(srt), "--candidates", str(candidates),
        "--yes", "--json", "--encoder", "libx264",
    ])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert [short["layout"] for short in summary["shorts"]] == ["split", "face"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_main_split_layout_renders_608x1080(monkeypatch, tmp_path, capsys):
    video = make_clip(tmp_path)
    srt = write_srt(tmp_path)
    cfg = write_config(
        tmp_path,
        'video: "Clip.mp4"\nsrt: "Clip_RO.srt"\nlayout: split\n'
        "segments:\n  - name: Hook\n    start: \"00:00:00\"\n    end: \"00:00:03\"\n",
    )
    monkeypatch.setattr(sg, "CONFIG_PATH", cfg)
    record = {"crop_w": 608, "crop_h": 1080, "points": [{"t_s": 0.0, "x": 656, "y": 0}]}
    monkeypatch.setattr(sg, "reframe_crop_path", lambda *args, **kwargs: record)

    code = sg.main(["--video", str(video), "--srt", str(srt), "--yes", "--json", "--encoder", "libx264"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["shorts"][0]["layout"] == "split"
    short = tmp_path / "shorts" / "Short1-Hook.mp4"
    assert short.exists()
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(short)],
        capture_output=True, text=True, check=True,
    )
    assert probe.stdout.strip() == "608,1080"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_main_face_layout_is_unchanged(monkeypatch, tmp_path, capsys):
    video = make_clip(tmp_path)
    srt = write_srt(tmp_path)
    cfg = write_config(
        tmp_path,
        'video: "Clip.mp4"\nsrt: "Clip_RO.srt"\n'
        "segments:\n  - name: Hook\n    start: \"00:00:00\"\n    end: \"00:00:03\"\n    x_offset: 656\n",
    )
    monkeypatch.setattr(sg, "CONFIG_PATH", cfg)

    code = sg.main(["--video", str(video), "--srt", str(srt), "--yes", "--json", "--encoder", "libx264"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["shorts"][0]["layout"] == "face"
    short = tmp_path / "shorts" / "Short1-Hook.mp4"
    assert short.exists()
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(short)],
        capture_output=True, text=True, check=True,
    )
    assert probe.stdout.strip() == "608,1080"
