import json
import subprocess

import render


def make_clip(path, duration=2.0, color="blue", freq=440):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:size=320x180:rate=25:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=f={freq}:duration={duration}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def make_edit(tmp_path, clips, transitions, name="edit.json"):
    sources = [{"file": path.name, "path": str(path)} for path in clips]
    video = []
    for index, (clip, transition) in enumerate(zip(clips, transitions)):
        video.append(
            {
                "id": f"v{index}",
                "clip": clip.name,
                "in_s": 0.0,
                "out_s": 2.0,
                "score": 0.5,
                "reason": "test",
                "transition": transition,
            }
        )
    edit = {
        "schema_version": "0.1",
        "generated_at": "2026-10-13T19:43:17+00:00",
        "brief": {"text": "test"},
        "sources": sources,
        "video": video,
        "audio": {"ambience": {"enabled": True, "source": "slot"}},
        "captions": [],
        "graphics": [],
    }
    path = tmp_path / name
    path.write_text(json.dumps(edit), encoding="utf-8")
    return path


def probe(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-show_entries",
            "stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def two_clips(tmp_path):
    first = tmp_path / "a.mp4"
    second = tmp_path / "b.mp4"
    make_clip(first, color="blue", freq=440)
    make_clip(second, color="red", freq=660)
    return first, second


def test_crossfade_join_duration_and_frame(tmp_path, capsys):
    first, second = two_clips(tmp_path)
    edit = make_edit(
        tmp_path,
        [first, second],
        [
            {"type": "cut", "duration_s": 0},
            {"type": "crossfade", "duration_s": 0.3},
        ],
    )
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "written"
    data = probe(out)
    assert abs(float(data["format"]["duration"]) - 3.7) <= 0.15
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1280, 720)
    assert any(s["codec_type"] == "audio" for s in data["streams"])


def test_cut_join_duration(tmp_path, capsys):
    first, second = two_clips(tmp_path)
    edit = make_edit(
        tmp_path,
        [first, second],
        [
            {"type": "cut", "duration_s": 0},
            {"type": "cut", "duration_s": 0},
        ],
    )
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    capsys.readouterr()
    data = probe(out)
    assert abs(float(data["format"]["duration"]) - 4.0) <= 0.15


def test_invalid_edit_exits_1_without_output(tmp_path, capsys):
    edit = tmp_path / "edit.json"
    edit.write_text(json.dumps({"schema_version": "0.1"}), encoding="utf-8")
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out)]) == 1
    assert not out.exists()
    assert "[error]" in capsys.readouterr().err


def test_unresolvable_clip_exits_1_without_output(tmp_path, capsys):
    first, _ = two_clips(tmp_path)
    edit = make_edit(tmp_path, [first], [{"type": "cut", "duration_s": 0}])
    data = json.loads(edit.read_text(encoding="utf-8"))
    data["video"][0]["clip"] = "missing.mp4"
    edit.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out)]) == 1
    assert not out.exists()
    assert "[error]" in capsys.readouterr().err


def test_rerun_without_force_is_skipped(tmp_path, capsys):
    first, second = two_clips(tmp_path)
    edit = make_edit(
        tmp_path,
        [first, second],
        [
            {"type": "cut", "duration_s": 0},
            {"type": "crossfade", "duration_s": 0.3},
        ],
    )
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    capsys.readouterr()
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "skipped"


def test_json_summary_has_wall_time_and_zero_cost(tmp_path, capsys):
    first, second = two_clips(tmp_path)
    edit = make_edit(
        tmp_path,
        [first, second],
        [
            {"type": "cut", "duration_s": 0},
            {"type": "cut", "duration_s": 0},
        ],
    )
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["wall_time_s"] >= 0
    assert summary["llm_cost_usd"] == 0.0


def test_media_dir_resolves_clip_not_in_sources(tmp_path, capsys):
    first, second = two_clips(tmp_path)
    edit = make_edit(
        tmp_path,
        [first, second],
        [
            {"type": "cut", "duration_s": 0},
            {"type": "cut", "duration_s": 0},
        ],
    )
    data = json.loads(edit.read_text(encoding="utf-8"))
    data["sources"] = [data["sources"][0]]
    edit.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "out.mp4"
    assert (
        render.main(
            ["--edit", str(edit), "--out", str(out), "--media-dir", str(tmp_path), "--json"]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "written"


def test_music_and_captions_warn_and_ignore(tmp_path, capsys):
    first, second = two_clips(tmp_path)
    edit = make_edit(
        tmp_path,
        [first, second],
        [
            {"type": "cut", "duration_s": 0},
            {"type": "cut", "duration_s": 0},
        ],
    )
    data = json.loads(edit.read_text(encoding="utf-8"))
    data["audio"]["music"] = {"path": "/does/not/exist.mp3"}
    data["captions"] = [{"template": "karaoke", "props": {}}]
    edit.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert any("music/sfx" in warning for warning in summary["warnings"])
    assert any("captions" in warning for warning in summary["warnings"])
