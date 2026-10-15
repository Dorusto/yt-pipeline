import json
import os
import subprocess
import time

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


def test_captions_warn_and_ignore(tmp_path, capsys):
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
    data["captions"] = [{"template": "karaoke", "props": {}}]
    edit.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert any("captions" in warning for warning in summary["warnings"])
    assert summary["music"] is None


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


def make_music(path, duration=2.0, freq=1000):
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={duration}:sample_rate=48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def audio_rms(path, start, duration):
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-i",
            str(path),
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    samples = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples**2)))


def make_music_edit(tmp_path, music, name="music_edit.json", ambience=True):
    photo = make_photo(tmp_path / "land.jpg")
    edit = make_photo_edit(
        tmp_path,
        [
            photo_slot("p0", photo.name, duration_s=3.0, transition={"type": "cut", "duration_s": 0}),
            photo_slot("p1", photo.name, duration_s=3.0, transition={"type": "cut", "duration_s": 0}),
        ],
        [photo_source(photo)],
        name=name,
    )
    data = json.loads(edit.read_text(encoding="utf-8"))
    data["audio"]["ambience"]["enabled"] = ambience
    data["audio"]["music"] = {"path": str(music)}
    edit.write_text(json.dumps(data), encoding="utf-8")
    return edit


def test_music_loops_to_edit_length(tmp_path, capsys):
    music = make_music(tmp_path / "music.wav", duration=2.0)
    edit = make_music_edit(tmp_path, music)
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["music"]["looped"] is True
    data = probe(out)
    assert abs(float(data["format"]["duration"]) - 6.0) <= 0.15
    assert audio_rms(out, 3.0, 0.5) > 0.001
    assert audio_rms(out, 5.0, 0.5) > 0.001


def test_music_fade_out_is_measurable(tmp_path, capsys):
    music = make_music(tmp_path / "music.wav", duration=2.0)
    edit = make_music_edit(tmp_path, music)
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    capsys.readouterr()
    middle = audio_rms(out, 2.5, 1.0)
    tail = audio_rms(out, 5.8, 0.2)
    assert middle > 0.001
    assert tail < 0.3 * middle


def test_music_flag_overrides_edit_path(tmp_path, capsys):
    edit_music = make_music(tmp_path / "edit_music.wav", duration=2.0, freq=1000)
    flag_music = make_music(tmp_path / "flag_music.wav", duration=2.0, freq=800)
    edit = make_music_edit(tmp_path, edit_music)
    out = tmp_path / "out.mp4"
    assert (
        render.main(
            ["--edit", str(edit), "--out", str(out), "--music", str(flag_music), "--json"]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["music"]["path"] == str(flag_music)


def test_music_gain_flag_overrides_edit_gain(tmp_path, capsys):
    music = make_music(tmp_path / "music.wav", duration=2.0)
    edit = make_music_edit(tmp_path, music)
    data = json.loads(edit.read_text(encoding="utf-8"))
    data["audio"]["music"]["gain_db"] = -6.0
    edit.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "out.mp4"
    assert (
        render.main(
            ["--edit", str(edit), "--out", str(out), "--music-gain-db", "-20", "--json"]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["music"]["gain_db"] == -20.0


def test_music_summary_fields(tmp_path, capsys):
    music = make_music(tmp_path / "music.wav", duration=2.0)
    edit = make_music_edit(tmp_path, music)
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["music"] == {
        "path": str(music),
        "gain_db": 0.0,
        "ambient_under_db": 12.0,
        "ducking": True,
        "looped": True,
    }


def test_music_without_ambience_has_no_ducking(tmp_path, capsys):
    music = make_music(tmp_path / "music.wav", duration=2.0)
    edit = make_music_edit(tmp_path, music, ambience=False)
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["music"]["ducking"] is False
    assert audio_rms(out, 3.0, 0.5) > 0.001


def test_music_rerun_skipped_when_newer_than_both(tmp_path, capsys):
    music = make_music(tmp_path / "music.wav", duration=2.0)
    edit = make_music_edit(tmp_path, music)
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    capsys.readouterr()
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "skipped"
    assert summary["music"]["path"] == str(music)


def test_music_newer_than_output_rerenders(tmp_path, capsys):
    music = make_music(tmp_path / "music.wav", duration=2.0)
    edit = make_music_edit(tmp_path, music)
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    capsys.readouterr()
    future = time.time() + 10
    os.utime(music, (future, future))
    assert render.main(["--edit", str(edit), "--out", str(out), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "written"


def test_missing_music_file_exits_1_without_output(tmp_path, capsys):
    edit = make_music_edit(tmp_path, tmp_path / "missing.wav")
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out)]) == 1
    assert not out.exists()
    assert "[error]" in capsys.readouterr().err


def test_music_without_audio_stream_exits_1(tmp_path, capsys):
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:d=2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(silent),
        ],
        check=True,
        capture_output=True,
    )
    edit = make_music_edit(tmp_path, silent)
    out = tmp_path / "out.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out)]) == 1
    assert not out.exists()
    assert "[error]" in capsys.readouterr().err


def test_help_lists_music_flags(capsys):
    with pytest.raises(SystemExit) as excinfo:
        render.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--music" in out
    assert "--music-gain-db" in out





def band_rms(path, start, duration, freq, width=60.0):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(duration), "-i", str(path),
         "-f", "f32le", "-ac", "1", "-ar", "48000", "-"],
        check=True,
        capture_output=True,
    ).stdout
    samples = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(samples.size)))
    freqs = np.fft.rfftfreq(samples.size, 1.0 / 48000)
    band = (freqs > freq - width) & (freqs < freq + width)
    return float(np.sqrt(np.sum(spectrum[band] ** 2)))


def test_music_is_the_bed_and_the_ambient_stays_audible_under_it(tmp_path, capsys):
    clip = tmp_path / "amb.mp4"
    make_clip(clip, duration=4.0, freq=440)
    edit = make_edit(tmp_path, [clip], [{"type": "cut", "duration_s": 0}])
    data = json.loads(edit.read_text(encoding="utf-8"))
    data["video"][0]["out_s"] = 4.0
    edit.write_text(json.dumps(data), encoding="utf-8")
    music = make_music(tmp_path / "music.wav", duration=4.0, freq=1000)
    out = tmp_path / "out.mp4"
    assert render.main(
        ["--edit", str(edit), "--out", str(out), "--music", str(music),
         "--denoiser", "none", "--json"]
    ) == 0
    capsys.readouterr()
    ambient = band_rms(out, 1.0, 1.0, 440)
    bed = band_rms(out, 1.0, 1.0, 1000)
    assert ambient > 0.0
    gap_db = 20 * np.log10(bed / ambient)
    assert 8.0 <= gap_db <= 16.0, gap_db


def make_ramp_edit(tmp_path, clip, in_s, out_s, middle_speed):
    slot = {
        "id": "v1",
        "clip": clip.name,
        "in_s": in_s,
        "out_s": out_s,
        "score": 0.5,
        "reason": "ramp",
        "transition": {"type": "cut", "duration_s": 0},
    }
    if middle_speed is not None:
        slot["middle_speed"] = middle_speed
    edit = {
        "schema_version": "0.1",
        "generated_at": "2026-10-15T21:17:43+00:00",
        "brief": {"text": "ramp"},
        "sources": [{"file": clip.name, "path": str(clip)}],
        "video": [slot],
        "audio": {"ambience": {"enabled": True, "source": "slot"}},
        "captions": [],
        "graphics": [],
    }
    path = tmp_path / "ramp_edit.json"
    path.write_text(json.dumps(edit), encoding="utf-8")
    return path


def decode_audio(path, rate=8000):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(rate), "-"],
        check=True,
        capture_output=True,
    )
    return np.frombuffer(result.stdout, dtype=np.float32), rate


def test_middle_speed_slot_has_the_ramped_on_screen_duration_and_continuous_audio(tmp_path, capsys):
    clip = tmp_path / "long.mp4"
    make_clip(clip, duration=7.0, freq=440)
    edit = make_ramp_edit(tmp_path, clip, 1.0, 6.0, 1.2)
    out = tmp_path / "ramp.mp4"
    code = render.main(["--edit", str(edit), "--out", str(out), "--no-audio-clean", "--json"])
    assert code == 0, capsys.readouterr().err
    summary = json.loads(capsys.readouterr().out)
    expected = 0.25 * 5.0 + 0.5 * 5.0 / 1.2 + 0.25 * 5.0
    assert abs(summary["duration_s"] - expected) <= 0.1
    video_duration = float(probe(out)["format"]["duration"])
    assert abs(video_duration - expected) <= 0.1
    samples, rate = decode_audio(out)
    assert abs(len(samples) / rate - expected) <= 0.1
    window = int(0.05 * rate)
    levels = [
        float(np.sqrt(np.mean(samples[start : start + window] ** 2)))
        for start in range(0, len(samples) - window, window)
    ]
    assert min(levels[1:-1]) > 0.5 * float(np.median(levels))


def test_slot_without_middle_speed_keeps_the_single_trim_graph(tmp_path):
    slot = {"id": "v1", "clip": "a.mp4", "in_s": 0.0, "out_s": 2.0, "transition": {"type": "cut", "duration_s": 0}}
    graph, _, _ = render.build_filter_graph([slot], [2.0], [True], True, 320, 180, 0.3, [])
    assert "split" not in graph and "asplit" not in graph and "atempo" not in graph
    assert "trim=start=0:end=2,setpts=PTS-STARTPTS,fps=25" in graph


def test_slot_with_middle_speed_builds_three_trims_and_atempo(tmp_path):
    slot = {
        "id": "v1", "clip": "a.mp4", "in_s": 0.0, "out_s": 4.0, "middle_speed": 1.2,
        "transition": {"type": "cut", "duration_s": 0},
    }
    graph, _, _ = render.build_filter_graph([slot], [render.slot_duration(slot)], [True], True, 320, 180, 0.3, [])
    assert "setpts=(PTS-STARTPTS)/1.2" in graph
    assert "atempo=1.2" in graph
    assert graph.count("concat=n=3") == 2
    assert render.slot_duration(slot) == pytest.approx(0.25 * 4 + 0.5 * 4 / 1.2 + 0.25 * 4)


def make_moving_square_clip(path, duration=3.0, size="640x360"):
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=0x303030:size={size}:rate=25:duration={duration}",
            "-f", "lavfi", "-i", f"color=c=red:size=60x60:rate=25:duration={duration}",
            "-f", "lavfi", "-i", f"sine=f=330:duration={duration}",
            "-filter_complex", "[0:v][1:v]overlay=x='30+150*t':y=150[v]",
            "-map", "[v]", "-map", "2:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def write_following_path(cache, clip, duration=3.0, size=(640, 360)):
    crop_w, crop_h = 202, 360
    points = []
    for index in range(int(duration * 4) + 1):
        t_s = index / 4
        centre = 60 + 150 * t_s
        x = int(min(size[0] - crop_w, max(0, round(centre - crop_w / 2))))
        points.append({"t_s": t_s, "x": x, "y": 0, "source": "track", "confidence": 0.9})
    record = {
        "file": clip.name, "path": str(clip), "width": size[0], "height": size[1],
        "crop_w": crop_w, "crop_h": crop_h, "points": points,
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(record), encoding="utf-8")


def red_centre_x(frame_png):
    array = frame_array(frame_png)
    mask = (array[:, :, 0] > 180) & (array[:, :, 1] < 80) & (array[:, :, 2] < 80)
    columns = np.where(mask.any(axis=0))[0]
    return None if columns.size == 0 else float(columns.mean())


def test_vertical_render_is_720x1280_and_follows_the_subject(tmp_path, capsys):
    clip = tmp_path / "mover.mp4"
    make_moving_square_clip(clip)
    edit = make_ramp_edit(tmp_path, clip, 0.0, 3.0, None)
    write_following_path(tmp_path / "reframe" / "mover_reframe.json", clip)
    out = tmp_path / "recap_720p_9x16.mp4"
    code = render.main(["--edit", str(edit), "--out", str(out), "--format", "9x16", "--no-audio-clean", "--json"])
    assert code == 0, capsys.readouterr().err
    summary = json.loads(capsys.readouterr().out)
    assert (summary["width"], summary["height"]) == (720, 1280)
    assert summary["format"] == "9x16"
    assert video_size(out) == (720, 1280)
    assert abs(float(probe(out)["format"]["duration"]) - 3.0) <= 0.1
    for moment in (0.3, 1.5, 2.7):
        x = red_centre_x(extract_frame(out, moment, tmp_path / f"f{moment}.png"))
        assert x is not None, f"subject lost at {moment}s"
        assert abs(x - 360) < 130, f"subject off centre at {moment}s: {x}"


def test_vertical_render_with_a_middle_speed_ramp_keeps_the_crop(tmp_path, capsys):
    clip = tmp_path / "mover.mp4"
    make_moving_square_clip(clip, duration=4.0)
    edit = make_ramp_edit(tmp_path, clip, 0.0, 4.0, 1.2)
    write_following_path(tmp_path / "reframe" / "mover_reframe.json", clip, duration=4.0)
    out = tmp_path / "ramp_9x16.mp4"
    code = render.main(["--edit", str(edit), "--out", str(out), "--format", "9x16", "--no-audio-clean", "--json"])
    assert code == 0, capsys.readouterr().err
    expected = 0.25 * 4 + 0.5 * 4 / 1.2 + 0.25 * 4
    assert abs(float(probe(out)["format"]["duration"]) - expected) <= 0.1
    assert video_size(out) == (720, 1280)


def test_reframe_is_called_once_per_clip_and_cached(tmp_path):
    clip = tmp_path / "mover.mp4"
    make_moving_square_clip(clip)
    calls = []

    def runner(command):
        calls.append(command)
        write_following_path(tmp_path / "reframe" / "mover_reframe.json", clip)
        return None

    edit = make_ramp_edit(tmp_path, clip, 0.0, 3.0, None)
    for _ in range(2):
        summary = render.render(
            edit, tmp_path / "v.mp4", None, None, None, None, 0.3, True,
            audio_clean=False, frame_format="9x16", reframe_runner=runner,
        )
    assert summary["status"] == "written"
    assert len(calls) == 1
    command = calls[0]
    assert command[1].endswith("reframe.py")
    assert command[command.index("--video") + 1] == str(clip)
    assert command[command.index("--out") + 1] == str(tmp_path / "reframe" / "mover_reframe.json")
    assert "--json" in command


def test_reframe_failure_falls_back_to_a_centred_crop_with_a_warning(tmp_path):
    clip = tmp_path / "mover.mp4"
    make_moving_square_clip(clip)
    edit = make_ramp_edit(tmp_path, clip, 0.0, 3.0, None)
    summary = render.render(
        edit, tmp_path / "v.mp4", None, None, None, None, 0.3, True,
        audio_clean=False, frame_format="9x16", reframe_runner=lambda command: "model download failed",
    )
    assert video_size(tmp_path / "v.mp4") == (720, 1280)
    assert any("reframe failed for mover.mp4" in warning for warning in summary["warnings"])


def test_format_both_writes_the_16x9_and_the_vertical_file(tmp_path, capsys):
    clip = tmp_path / "mover.mp4"
    make_moving_square_clip(clip)
    edit = make_ramp_edit(tmp_path, clip, 0.0, 3.0, None)
    write_following_path(tmp_path / "reframe" / "mover_reframe.json", clip)
    out = tmp_path / "recap_720p.mp4"
    code = render.main(["--edit", str(edit), "--out", str(out), "--format", "both", "--no-audio-clean", "--json"])
    assert code == 0, capsys.readouterr().err
    summary = json.loads(capsys.readouterr().out)
    vertical_out = tmp_path / "recap_720p_9x16.mp4"
    assert video_size(out) == (1280, 720)
    assert video_size(vertical_out) == (720, 1280)
    assert summary["vertical"]["out"] == str(vertical_out)
    assert abs(float(probe(out)["format"]["duration"]) - float(probe(vertical_out)["format"]["duration"])) <= 0.05


def test_default_format_is_16x9_and_unchanged(tmp_path, capsys):
    clip = tmp_path / "mover.mp4"
    make_moving_square_clip(clip)
    edit = make_ramp_edit(tmp_path, clip, 0.0, 3.0, None)
    out = tmp_path / "wide.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--no-audio-clean", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["format"] == "16x9"
    assert video_size(out) == (1280, 720)
    assert not (tmp_path / "reframe").exists()


def test_landscape_photo_in_a_vertical_frame_is_cropped_toward_the_subject(tmp_path, capsys):
    photo = tmp_path / "wide.jpg"
    array = np.zeros((360, 640, 3), dtype="uint8")
    array[:, :] = (40, 40, 40)
    array[150:210, 520:580] = (255, 0, 0)
    Image.fromarray(array, "RGB").save(photo)
    slot = photo_slot("p0", photo.name, duration_s=3.0)
    slot["ken_burns"] = {"move": "zoom_in", "amount": 0.05, "focus_x": 0.86, "focus_y": 0.5}
    edit = make_photo_edit(tmp_path, [slot], [photo_source(photo)])
    out = tmp_path / "photo_9x16.mp4"
    assert render.main(["--edit", str(edit), "--out", str(out), "--format", "9x16", "--json"]) == 0
    capsys.readouterr()
    assert video_size(out) == (720, 1280)
    x = red_centre_x(extract_frame(out, 0.5, tmp_path / "p.png"))
    assert x is not None and abs(x - 360) < 160


def test_help_lists_the_format_flag(capsys):
    with pytest.raises(SystemExit):
        render.main(["--help"])
    output = capsys.readouterr().out
    assert "--format" in output and "9x16" in output


def test_photo_with_a_stale_sources_path_falls_back_to_the_media_dir(tmp_path):
    media = tmp_path / "proxy"
    media.mkdir()
    (media / "still.jpg").write_bytes(b"jpeg")
    sources = [{"file": "still.jpg", "path": "/gone/old/proxy/still.jpg", "media_type": "photo"}]
    path, origin = render.resolve_photo("still.jpg", sources, media, None)
    assert path == media / "still.jpg"
    assert origin == "sources"


def test_pan_stays_inside_the_zoomed_frame():
    for move in ("pan_left", "pan_right"):
        for focus in (0.0, 0.3, 0.5, 0.9, 1.0):
            for amount in (0.04, 0.08, 0.3):
                start, end = render.pan_span(move, amount, focus)
                low = 1 / (2 * (1 + amount))
                assert low - 1e-9 <= min(start, end)
                assert max(start, end) <= 1 - low + 1e-9
                assert abs(end - start) > 0


def test_ken_burns_works_on_an_enlarged_frame_with_eased_motion():
    chain = render.zoompan_for("zoom_in", 0.08, 0.5, 0.5, 100, 1280, 720)
    assert chain.startswith("scale=iw*4:ih*4")
    assert "(3-2*(on/99))" in chain
    assert render.zoompan_upscale(3840) == 2
