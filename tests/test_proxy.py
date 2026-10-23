import hashlib
import json
import subprocess

import proxy
from common.exit_codes import EXIT_OK, EXIT_USAGE


def make_clip(path, duration=2.0, size="320x240", rate=10, audio=True):
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration}:size={size}:rate={rate}",
    ]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        command += ["-c:a", "aac", "-shortest"]
    command += [str(path)]
    subprocess.run(command, check=True, capture_output=True)


def probe(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def video_stream(path):
    return next(s for s in probe(path)["streams"] if s["codec_type"] == "video")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_writes_proxy_with_audio(tmp_path, capsys):
    source = tmp_path / "clip.mp4"
    make_clip(source)
    out = tmp_path / "proxy"
    assert proxy.main([str(source), "--out", str(out), "--json"]) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "written"
    target = out / "clip.mp4"
    assert target.exists()
    video = video_stream(target)
    assert video["height"] == 720
    assert video["codec_name"] == "h264"
    assert video["avg_frame_rate"] == video["r_frame_rate"]
    assert any(s["codec_type"] == "audio" for s in probe(target)["streams"])


def test_proxy_keeps_the_clip_creation_time(tmp_path, capsys):
    source = tmp_path / "C0001.MP4"
    raw = tmp_path / "raw.mp4"
    make_clip(raw)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(raw), "-c", "copy", "-metadata",
         "creation_time=2026-10-12T11:38:00.000000Z", str(source)],
        check=True,
        capture_output=True,
    )
    out = tmp_path / "proxy"
    assert proxy.main([str(source), "--out", str(out), "--json"]) == EXIT_OK
    tags = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time",
         "-of", "csv=p=0", str(out / "C0001.MP4")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert tags.startswith("2026-10-12T11:38:00")


def test_skips_existing_and_force_rewrites(tmp_path, capsys):
    source = tmp_path / "clip.mp4"
    make_clip(source)
    out = tmp_path / "proxy"
    assert proxy.main([str(source), "--out", str(out), "--json"]) == EXIT_OK
    capsys.readouterr()
    target = out / "clip.mp4"
    before = sha256(target)
    assert proxy.main([str(source), "--out", str(out), "--json"]) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "skipped"
    assert summary["written"] == []
    assert sha256(target) == before
    assert proxy.main([str(source), "--out", str(out), "--force", "--json"]) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "written"


def test_refuses_out_inside_source(tmp_path, capsys):
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    make_clip(source_dir / "clip.mp4")
    out = source_dir / "proxy"
    assert proxy.main([str(source_dir), "--out", str(out)]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "[error]" in err
    assert "inside the source" in err
    assert not out.exists()
    assert sorted(path.name for path in source_dir.iterdir()) == ["clip.mp4"]


def test_refuses_out_that_would_overwrite_original(tmp_path, capsys):
    source = tmp_path / "clip.mp4"
    make_clip(source)
    before = sha256(source)
    assert proxy.main([str(source), "--out", str(source.parent)]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "[error]" in err
    assert "must not be inside the source" in err
    assert sha256(source) == before
    assert proxy.main([str(source), "--out", str(source.parent), "--force"]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "[error]" in err
    assert "must not be inside the source" in err
    assert sha256(source) == before


def test_originals_untouched(tmp_path, capsys):
    source = tmp_path / "clip.mp4"
    make_clip(source)
    before = sha256(source)
    out = tmp_path / "proxy"
    assert proxy.main([str(source), "--out", str(out), "--json"]) == EXIT_OK
    capsys.readouterr()
    assert sha256(source) == before


def test_json_summary_fields(tmp_path, capsys):
    source = tmp_path / "clip.mp4"
    make_clip(source)
    out = tmp_path / "proxy"
    assert proxy.main([str(source), "--out", str(out), "--json"]) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["tool"] == "scripts/proxy.py"
    assert isinstance(summary["wall_time_s"], (int, float))
    assert summary["llm_cost_usd"] == 0.0


def test_folder_input(tmp_path, capsys):
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    make_clip(source_dir / "a.mp4")
    make_clip(source_dir / "b.mp4")
    (source_dir / "notes.txt").write_text("ignore me", encoding="utf-8")
    out = tmp_path / "proxy"
    assert proxy.main([str(source_dir), "--out", str(out), "--json"]) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["counts"]["written"] == 2
    assert (out / "a.mp4").exists()
    assert (out / "b.mp4").exists()
    assert not (out / "notes.txt").exists()


def test_video_without_audio(tmp_path, capsys):
    source = tmp_path / "silent.mp4"
    make_clip(source, audio=False)
    out = tmp_path / "proxy"
    assert proxy.main([str(source), "--out", str(out), "--json"]) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "written"
    assert (out / "silent.mp4").exists()


def make_photo(path, size=(32, 24), exif_datetime=None):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (120, 60, 30))
    kwargs = {}
    if path.suffix.lower() == ".heic":
        kwargs["format"] = "HEIF"
    if exif_datetime is not None:
        exif = Image.Exif()
        exif[36867] = exif_datetime
        kwargs["exif"] = exif
    image.save(path, **kwargs)
    return path


def test_photo_jpeg_copied_byte_identical(tmp_path):
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    photo = make_photo(source_dir / "still.jpg", exif_datetime="2026:10:12 11:40:00")
    out = tmp_path / "proxy"

    summary = proxy.build_proxies(source_dir, out, height=720, crf=23, force=False)

    target = out / "still.jpg"
    assert target.exists()
    assert sha256(target) == sha256(photo)
    assert summary["counts"]["photos"] == 1


def test_photo_only_folder_is_valid(tmp_path):
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    make_photo(source_dir / "a.jpg")
    out = tmp_path / "proxy"

    summary = proxy.build_proxies(source_dir, out, height=720, crf=23, force=False)

    assert summary["status"] in {"written", "skipped"}
    assert (out / "a.jpg").exists()


def test_heic_photo_becomes_jpeg_with_exif(tmp_path):
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        import pytest

        pytest.skip("pillow-heif not installed")
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    make_photo(source_dir / "still.heic", exif_datetime="2026:10:12 11:41:00")
    out = tmp_path / "proxy"

    summary = proxy.build_proxies(source_dir, out, height=720, crf=23, force=False)

    target = out / "still.jpg"
    assert target.exists()
    assert summary["counts"]["photos"] == 1
    from common.photos import capture_time

    iso, source = capture_time(target)
    assert source == "exif"
    assert iso == "2026-10-12T11:41:00+00:00"


def test_photo_rerun_skips_existing_copy(tmp_path):
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    make_photo(source_dir / "still.jpg", exif_datetime="2026:10:12 11:40:00")
    out = tmp_path / "proxy"

    proxy.build_proxies(source_dir, out, height=720, crf=23, force=False)
    summary = proxy.build_proxies(source_dir, out, height=720, crf=23, force=False)

    assert summary["status"] == "skipped"
    assert "still.jpg" in summary["skipped"]
    assert summary["counts"]["photos"] == 0


def test_photo_refuses_out_inside_source(tmp_path):
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    make_photo(source_dir / "still.jpg")
    try:
        proxy.build_proxies(source_dir, source_dir / "proxy", height=720, crf=23, force=False)
    except proxy.ProxyError as exc:
        assert "inside the source" in str(exc)
    else:
        raise AssertionError("expected ProxyError")
