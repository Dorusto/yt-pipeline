import json
import subprocess

import numpy as np
import pytest
from PIL import Image

import render


def make_clip(path, duration=2.0, color="blue", freq=440, size="320x180"):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:size={size}:rate=25:duration={duration}",
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


def make_originals(tmp_path, clips, size="640x360"):
    originals = tmp_path / "originals"
    originals.mkdir()
    for index, clip in enumerate(clips):
        make_clip(originals / clip.name, color="green", freq=330 + index, size=size)
    return originals


def video_size(path):
    data = probe(path)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    return video["width"], video["height"]


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


def test_originals_win_over_sources(tmp_path, capsys):
    first, second = two_clips(tmp_path)
    edit = make_edit(
        tmp_path,
        [first, second],
        [
            {"type": "cut", "duration_s": 0},
            {"type": "cut", "duration_s": 0},
        ],
    )
    originals = make_originals(tmp_path, [first, second], size="640x360")
    out = tmp_path / "final.mp4"
    assert (
        render.main(
            ["--edit", str(edit), "--out", str(out), "--originals-dir", str(originals), "--json"]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "written"
    assert video_size(out) == (640, 360)
    assert summary["source"] == "originals"
    assert summary["originals_dir"] == str(originals)


def test_native_size_used_when_width_and_height_omitted(tmp_path, capsys):
    first, second = two_clips(tmp_path)
    edit = make_edit(
        tmp_path,
        [first, second],
        [
            {"type": "cut", "duration_s": 0},
            {"type": "cut", "duration_s": 0},
        ],
    )
    originals = make_originals(tmp_path, [first, second], size="854x480")
    out = tmp_path / "final.mp4"
    assert (
        render.main(["--edit", str(edit), "--out", str(out), "--originals-dir", str(originals)])
        == 0
    )
    capsys.readouterr()
    assert video_size(out) == (854, 480)


def test_default_frame_without_originals(tmp_path, capsys):
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
    assert video_size(out) == (1280, 720)
    assert summary["source"] == "sources"
    assert summary["originals_dir"] is None


def test_missing_originals_fall_back_to_sources_with_warning(tmp_path, capsys):
    first, second = two_clips(tmp_path)
    edit = make_edit(
        tmp_path,
        [first, second],
        [
            {"type": "cut", "duration_s": 0},
            {"type": "cut", "duration_s": 0},
        ],
    )
    originals = tmp_path / "originals"
    originals.mkdir()
    out = tmp_path / "final.mp4"
    assert (
        render.main(
            ["--edit", str(edit), "--out", str(out), "--originals-dir", str(originals), "--json"]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "written"
    assert summary["warnings"]
    assert any("fell back to sources" in warning for warning in summary["warnings"])
    assert video_size(out) == (320, 180)
    assert out.exists()


def test_out_inside_originals_is_refused(tmp_path, capsys):
    first, second = two_clips(tmp_path)
    edit = make_edit(
        tmp_path,
        [first, second],
        [
            {"type": "cut", "duration_s": 0},
            {"type": "cut", "duration_s": 0},
        ],
    )
    originals = make_originals(tmp_path, [first, second])
    out = originals / "final.mp4"
    assert (
        render.main(["--edit", str(edit), "--out", str(out), "--originals-dir", str(originals)])
        == 1
    )
    assert not out.exists()
    assert "[error]" in capsys.readouterr().err


def test_originals_are_not_modified(tmp_path, capsys):
    first, second = two_clips(tmp_path)
    edit = make_edit(
        tmp_path,
        [first, second],
        [
            {"type": "cut", "duration_s": 0},
            {"type": "cut", "duration_s": 0},
        ],
    )
    originals = make_originals(tmp_path, [first, second], size="640x360")
    targets = sorted(originals.iterdir())
    before = {path.name: (path.stat().st_mtime_ns, path.stat().st_size) for path in targets}
    out = tmp_path / "final.mp4"
    assert (
        render.main(["--edit", str(edit), "--out", str(out), "--originals-dir", str(originals)])
        == 0
    )
    capsys.readouterr()
    after = {path.name: (path.stat().st_mtime_ns, path.stat().st_size) for path in targets}
    assert before == after


def test_json_summary_keeps_keys_and_adds_originals_fields(tmp_path, capsys):
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
    expected = {
        "status",
        "out",
        "slots",
        "duration_s",
        "width",
        "height",
        "warnings",
        "originals_dir",
        "source",
        "wall_time_s",
        "llm_cost_usd",
    }
    assert expected <= set(summary)
    assert summary["originals_dir"] is None
    assert summary["source"] == "sources"
    assert summary["llm_cost_usd"] == 0.0


def test_help_lists_originals_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        render.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--originals-dir" in out
    assert "--width" in out
    assert "--media-dir" in out


def test_ambient_clean_chain_applied_by_default(tmp_path, capsys):
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
    assert render.main(["--edit", str(edit), "--out", str(out), "--denoiser", "afftdn", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    clean = summary["audio_clean"]
    assert clean["enabled"] is True
    assert clean["denoiser"] == "afftdn"
    assert clean["filters"] == ["highpass", "afftdn", "loudnorm"]
    assert probe(out)


def test_no_audio_clean_keeps_the_raw_mix(tmp_path, capsys):
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
    assert render.main(["--edit", str(edit), "--out", str(out), "--no-audio-clean", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["audio_clean"] == {"enabled": False, "denoiser": None, "filters": []}
    assert probe(out)


def test_arnndn_without_model_is_an_error(tmp_path, capsys):
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
    code = render.main(
        ["--edit", str(edit), "--out", str(out), "--denoiser", "arnndn", "--model", str(tmp_path / "missing.rnnn"), "--json"]
    )
    assert code == 1
    assert "arnndn" in capsys.readouterr().err
    assert not out.exists()


def test_filter_graph_appends_the_clean_chain():
    slots = [{"in_s": 0.0, "out_s": 2.0, "transition": {"type": "cut", "duration_s": 0}}]
    graph, _, final_a = render.build_filter_graph(
        slots, [2.0], [True], True, 1280, 720, 0.3, [], clean_chain=["highpass=f=150", "afftdn=nr=12"]
    )
    assert final_a == "aclean"
    assert "highpass=f=150,afftdn=nr=12[aclean]" in graph
    graph, _, final_a = render.build_filter_graph(
        slots, [2.0], [True], True, 1280, 720, 0.3, [], clean_chain=[]
    )
    assert final_a == "a0"
    assert "[aclean]" not in graph


def make_photo(path, size=(640, 360), color=(200, 60, 60)):
    Image.new("RGB", size, color).save(path)
    return path


def make_gradient_photo(path, size=(300, 600)):
    width, height = size
    x = np.arange(width)[None, :]
    y = np.arange(height)[:, None]
    red = np.broadcast_to(x % 256, (height, width))
    green = np.broadcast_to(y % 256, (height, width))
    blue = (x + y) % 256
    array = np.stack([red, green, blue], axis=-1).astype("uint8")
    Image.fromarray(array, "RGB").save(path)
    return path


def photo_source(path):
    return {"file": path.name, "path": str(path), "duration_s": None, "media_type": "photo"}


def photo_slot(slot_id, photo_name, duration_s=3.0, move="zoom_in", transition=None):
    slot = {
        "id": slot_id,
        "kind": "photo",
        "photo": photo_name,
        "duration_s": duration_s,
        "ken_burns": {"move": move, "amount": 0.08, "focus_x": 0.5, "focus_y": 0.5},
        "score": 0.5,
        "reason": "test",
    }
    if transition is not None:
        slot["transition"] = transition
    return slot


def make_photo_edit(tmp_path, slots, sources, name="photo_edit.json"):
    edit = {
        "schema_version": "0.1",
        "generated_at": "2026-10-14T19:43:17+00:00",
        "brief": {"text": "test"},
        "sources": sources,
        "video": slots,
        "audio": {"ambience": {"enabled": True, "source": "slot"}},
        "captions": [],
        "graphics": [],
    }
    path = tmp_path / name
    path.write_text(json.dumps(edit), encoding="utf-8")
    return path


def extract_frame(video, t, out):
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-ss", str(t), "-frames:v", "1", str(out)],
        check=True,
        capture_output=True,
    )
    return out


def frame_array(path):
    return np.asarray(Image.open(path).convert("RGB"), dtype=float)


def test_photo_slot_renders_duration_and_frame(tmp_path, capsys):
    photo = make_photo(tmp_path / "land.jpg")
    edit = make_photo_edit(
        tmp_path,
        [photo_slot("p0", photo.name, duration_s=3.0)],
        [photo_source(photo)],
    )
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "written"
    assert summary["slots"] == 1
    data = probe(out)
    assert abs(float(data["format"]["duration"]) - 3.0) <= 0.1
    assert video_size(out) == (1280, 720)
    assert any(s["codec_type"] == "audio" for s in data["streams"])


def test_portrait_photo_renders_on_blurred_background(tmp_path, capsys):
    photo = make_gradient_photo(tmp_path / "port.jpg", size=(300, 600))
    edit = make_photo_edit(
        tmp_path,
        [photo_slot("p0", photo.name, duration_s=3.0)],
        [photo_source(photo)],
    )
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    capsys.readouterr()
    array = frame_array(extract_frame(out, 1.5, tmp_path / "frame.png"))
    left = array[300:420, 0:4, :]
    right = array[300:420, -4:, :]
    assert left.std() > 0
    assert right.std() > 0
    assert left.mean() > 0
    assert right.mean() > 0


def test_zoom_in_produces_visible_motion(tmp_path, capsys):
    photo = make_gradient_photo(tmp_path / "land.jpg", size=(640, 360))
    edit = make_photo_edit(
        tmp_path,
        [photo_slot("p0", photo.name, duration_s=3.0, move="zoom_in")],
        [photo_source(photo)],
    )
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    capsys.readouterr()
    first = frame_array(extract_frame(out, 0.05, tmp_path / "first.png"))
    last = frame_array(extract_frame(out, 2.9, tmp_path / "last.png"))
    assert np.abs(first - last).mean() > 0


def test_mixed_video_and_photo_slots(tmp_path, capsys):
    clip = tmp_path / "clip.mp4"
    make_clip(clip, color="blue", freq=440)
    photo = make_photo(tmp_path / "land.jpg")
    edit = make_photo_edit(
        tmp_path,
        [
            {
                "id": "v0",
                "clip": clip.name,
                "in_s": 0.0,
                "out_s": 2.0,
                "score": 0.5,
                "reason": "test",
                "transition": {"type": "cut", "duration_s": 0},
            },
            photo_slot("p1", photo.name, duration_s=3.0, transition={"type": "cut", "duration_s": 0}),
        ],
        [
            {"file": clip.name, "path": str(clip), "duration_s": 2.0, "media_type": "video"},
            photo_source(photo),
        ],
    )
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["slots"] == 2
    data = probe(out)
    assert abs(float(data["format"]["duration"]) - 5.0) <= 0.1
    assert video_size(out) == (1280, 720)


def test_photo_first_slot_uses_photo_size_from_originals(tmp_path, capsys):
    photo = make_photo(tmp_path / "land.jpg", size=(640, 360))
    originals = tmp_path / "originals"
    originals.mkdir()
    make_photo(originals / "land.jpg", size=(640, 360))
    edit = make_photo_edit(
        tmp_path,
        [photo_slot("p0", photo.name, duration_s=3.0)],
        [photo_source(photo)],
    )
    out = tmp_path / "final.mp4"
    assert (
        render.main(
            ["--edit", str(edit), "--out", str(out), "--originals-dir", str(originals), "--json"]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["source"] == "originals"
    assert video_size(out) == (640, 360)


def test_unresolvable_photo_exits_1_without_output(tmp_path, capsys):
    photo = make_photo(tmp_path / "land.jpg")
    edit = make_photo_edit(
        tmp_path,
        [photo_slot("p0", "missing.jpg", duration_s=3.0)],
        [photo_source(photo)],
    )
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out)]) == 1
    assert not out.exists()
    assert "[error]" in capsys.readouterr().err


def test_heic_photo_is_converted_to_jpeg(tmp_path, capsys):
    import pillow_heif

    pillow_heif.register_heif_opener()
    photo = tmp_path / "still.heic"
    Image.new("RGB", (300, 600), (120, 180, 90)).save(photo, quality=90)
    edit = make_photo_edit(
        tmp_path,
        [photo_slot("p0", photo.name, duration_s=3.0)],
        [photo_source(photo)],
    )
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    capsys.readouterr()
    assert out.exists()
    assert list((tmp_path / "photos").glob("*.jpg"))


