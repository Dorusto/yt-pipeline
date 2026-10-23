import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

import ingest
from common.schema import validate

try:
    import pillow_heif

    HAS_PILLOW_HEIF = True
except ImportError:
    pillow_heif = None
    HAS_PILLOW_HEIF = False

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ingest.py"
EXIF_DATETIME_ORIGINAL = 36867


def make_clip(path, duration=1, size="64x48", rate=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate={rate}:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def make_photo(path, size=(32, 24), exif_datetime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (120, 60, 30))
    save_kwargs = {}
    if path.suffix.lower() == ".heic":
        save_kwargs["format"] = "HEIF"
    if exif_datetime is not None:
        exif = Image.Exif()
        exif[EXIF_DATETIME_ORIGINAL] = exif_datetime
        save_kwargs["exif"] = exif
    image.save(path, **save_kwargs)
    return path


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
    )


def read_ingest(workspace):
    return json.loads((workspace / "ingest.json").read_text(encoding="utf-8"))


def test_help_mentions_workspace_and_json():
    result = run_cli("--help")
    assert result.returncode == 0
    assert "--workspace" in result.stdout
    assert "--json" in result.stdout


def test_scan_two_clips_validates_and_sorts(tmp_path):
    source = tmp_path / "clips"
    source.mkdir()
    make_clip(source / "b_clip.mp4")
    make_clip(source / "a_clip.mp4")
    workspace = tmp_path / "workspace"

    result = run_cli(source, "--workspace", workspace, "--json")

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["clips"] == 2
    data = read_ingest(workspace)
    assert validate(data, "ingest") == []
    assert data["clip_count"] == 2
    assert [clip["file"] for clip in data["clips"]] == ["a_clip.mp4", "b_clip.mp4"]
    assert Path(summary["output"]).resolve() == (workspace / "ingest.json").resolve()


def test_clip_fields(tmp_path):
    source = tmp_path / "clips"
    source.mkdir()
    make_clip(source / "one.mp4")
    workspace = tmp_path / "ws"

    assert run_cli(source, "--workspace", workspace).returncode == 0
    clip = read_ingest(workspace)["clips"][0]

    assert clip["duration_s"] == pytest.approx(1.0, abs=0.1)
    assert clip["width"] == 64
    assert clip["height"] == 48
    assert clip["fps"] == pytest.approx(10.0)
    assert clip["video_codec"] == "h264"
    assert clip["streams"][0]["index"] == 0
    assert clip["streams"][0]["codec_type"] == "video"
    assert clip["gyro"]["present"] is False
    assert clip["gyro"]["evidence"] is None
    assert clip["warnings"] == []


def test_creation_time_from_filename(tmp_path):
    source = tmp_path / "clips"
    source.mkdir()
    make_clip(source / "dji_mimo_20261012_113618_0051_x.mp4")
    workspace = tmp_path / "ws"

    assert run_cli(source, "--workspace", workspace).returncode == 0
    clip = read_ingest(workspace)["clips"][0]

    if clip["creation_time_source"] == "metadata":
        assert clip["creation_time"] is not None
    else:
        assert clip["creation_time_source"] == "filename"
        assert clip["creation_time"] == "2026-10-12T11:36:18"


def test_creation_time_from_name_variants():
    assert ingest.creation_time_from_name("dji_mimo_20261012_113618_0051_x.mp4") == "2026-10-12T11:36:18"
    assert ingest.creation_time_from_name("DJI_20250810123456_0001_D.MP4") == "2025-08-10T12:34:56"
    assert ingest.creation_time_from_name("no_timestamp.mp4") is None


def test_gyro_detects_djmd_stream():
    streams = [
        {"index": 2, "codec_type": "data", "codec_tag": "djmd", "handler_name": "DJI meta", "nb_frames": 548}
    ]
    present, evidence = ingest.gyro_from_streams(streams)
    assert present is True
    assert evidence == "stream 2: codec_tag=djmd handler='DJI meta' samples=548"


def test_gyro_ignores_single_sample_tmcd():
    streams = [{"index": 2, "codec_type": "data", "codec_tag": "tmcd", "handler_name": "VideoHandler", "nb_frames": 1}]
    assert ingest.gyro_from_streams(streams) == (False, None)


def test_gyro_ignores_mjpeg_video_handler():
    streams = [{"index": 3, "codec_type": "video", "codec_name": "mjpeg", "codec_tag": "mjpg", "handler_name": "VideoHandler"}]
    assert ingest.gyro_from_streams(streams) == (False, None)


def test_gyro_requires_more_than_one_sample():
    streams = [{"index": 2, "codec_type": "data", "codec_tag": "djmd", "handler_name": "DJI meta", "nb_frames": 1}]
    assert ingest.gyro_from_streams(streams) == (False, None)


def test_gyro_matches_handler_name():
    streams = [{"index": 4, "codec_type": "data", "codec_tag": None, "handler_name": "Gyroscope", "nb_frames": 200}]
    present, evidence = ingest.gyro_from_streams(streams)
    assert present is True
    assert "Gyroscope" in evidence


def test_missing_source_dir_errors(tmp_path):
    result = run_cli(tmp_path / "nope", "--workspace", tmp_path / "ws")
    assert result.returncode == 1
    assert "[error]" in result.stderr


def test_empty_folder_errors(tmp_path):
    source = tmp_path / "clips"
    source.mkdir()
    (source / "note.txt").write_text("hi", encoding="utf-8")
    result = run_cli(source, "--workspace", tmp_path / "ws")
    assert result.returncode == 1
    assert "[error]" in result.stderr
    assert not (tmp_path / "ws" / "ingest.json").exists()


def test_json_success_is_parseable(tmp_path):
    source = tmp_path / "clips"
    source.mkdir()
    make_clip(source / "one.mp4")
    result = run_cli(source, "--workspace", tmp_path / "ws", "--json")
    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert set(summary) == {
        "clips",
        "photos",
        "output",
        "gyro_clips",
        "total_duration_s",
        "wall_time_s",
        "llm_cost_usd",
    }
    assert summary["wall_time_s"] >= 0.0
    assert summary["llm_cost_usd"] == 0.0


def test_help_mentions_photo_extensions():
    result = run_cli("--help")
    assert result.returncode == 0
    for extension in (".jpg", ".jpeg", ".heic"):
        assert extension in result.stdout


def test_photo_with_exif_capture_time(tmp_path):
    source = tmp_path / "media"
    source.mkdir()
    make_photo(source / "shot.jpg", exif_datetime="2026:10:12 11:40:00")
    workspace = tmp_path / "ws"

    result = run_cli(source, "--workspace", workspace, "--json")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["photos"] == 1
    data = read_ingest(workspace)
    assert validate(data, "ingest") == []
    assert data["photo_count"] == 1
    photo = data["photos"][0]
    assert photo["file"] == "shot.jpg"
    assert photo["width"] == 32
    assert photo["height"] == 24
    assert photo["creation_time"] == "2026-10-12T11:40:00+00:00"
    assert photo["creation_time_source"] == "exif"
    assert photo["warnings"] == []


def test_photo_without_exif_has_no_creation_time(tmp_path):
    source = tmp_path / "media"
    source.mkdir()
    make_photo(source / "plain.jpg")
    workspace = tmp_path / "ws"

    assert run_cli(source, "--workspace", workspace).returncode == 0
    photo = read_ingest(workspace)["photos"][0]
    assert photo["creation_time"] is None
    assert photo["creation_time_source"] is None


@pytest.mark.skipif(not HAS_PILLOW_HEIF, reason="pillow-heif not installed")
def test_heic_photo_decodes_width_and_height(tmp_path):
    pillow_heif.register_heif_opener()
    source = tmp_path / "media"
    source.mkdir()
    make_photo(source / "still.heic", size=(48, 36))
    workspace = tmp_path / "ws"

    assert run_cli(source, "--workspace", workspace).returncode == 0
    photo = read_ingest(workspace)["photos"][0]
    assert photo["width"] == 48
    assert photo["height"] == 36


def test_only_photos_scans_without_error(tmp_path):
    source = tmp_path / "media"
    source.mkdir()
    make_photo(source / "a.jpg")
    make_photo(source / "b.jpg", exif_datetime="2026:10:12 11:41:00")
    workspace = tmp_path / "ws"

    result = run_cli(source, "--workspace", workspace, "--json")

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["clips"] == 0
    assert summary["photos"] == 2
    data = read_ingest(workspace)
    assert validate(data, "ingest") == []
    assert data["clip_count"] == 0
    assert data["clips"] == []
    assert data["photo_count"] == 2


def test_extension_matching_is_case_insensitive(tmp_path):
    source = tmp_path / "clips"
    source.mkdir()
    make_clip(source / "ONE.MP4")
    result = run_cli(source, "--workspace", tmp_path / "ws", "--json")
    assert result.returncode == 0
    assert json.loads(result.stdout)["clips"] == 1


def test_scan_is_non_recursive(tmp_path):
    source = tmp_path / "clips"
    (source / "nested").mkdir(parents=True)
    make_clip(source / "top.mp4")
    make_clip(source / "nested" / "deep.mp4")
    result = run_cli(source, "--workspace", tmp_path / "ws", "--json")
    assert result.returncode == 0
    assert json.loads(result.stdout)["clips"] == 1


def test_rerun_rewrites_identical_clips(tmp_path):
    source = tmp_path / "clips"
    source.mkdir()
    make_clip(source / "one.mp4")
    workspace = tmp_path / "ws"

    assert run_cli(source, "--workspace", workspace, "--json").returncode == 0
    first = read_ingest(workspace)
    assert run_cli(source, "--workspace", workspace, "--json").returncode == 0
    second = read_ingest(workspace)

    assert validate(second, "ingest") == []
    assert second["clips"] == first["clips"]
    assert second["clip_count"] == first["clip_count"]
