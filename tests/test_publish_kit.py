import json
import os
import subprocess
from pathlib import Path

import pytest

import publish_kit
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE

SRT_RO = (
    "1\n00:00:00,200 --> 00:00:03,800\ndoi trei patru cinci.\n\n"
    "2\n00:00:04,200 --> 00:00:06,600\nAlfa beta gama.\n\n"
    "3\n00:00:08,600 --> 00:00:09,800\nopt nouă.\n"
)
SRT_EN = (
    "1\n00:00:00,200 --> 00:00:03,800\nHello\n\n"
    "2\n00:00:04,200 --> 00:00:06,600\nWorld\n\n"
    "3\n00:00:08,600 --> 00:00:09,800\nEnd\n"
)

METADATA = {
    "name": "proj",
    "titles": ["Title one", "Title two", "Title three"],
    "description_ro": "Desc RO",
    "description_en": "Desc EN",
    "chapters": [{"timestamp": "00:00:00", "title": "Intro"}],
    "tags": ["a", "b"],
    "pinned_comment": "Q?",
}


def make_clip(path, duration=10):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=64x64:rate=10:duration={duration}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def make_thumbnail(path, size=(1280, 720)):
    from PIL import Image

    Image.new("RGB", size, (10, 20, 30)).save(path, "JPEG")


def build_project(tmp_path, name="proj"):
    project = tmp_path / name
    project.mkdir()
    clip = tmp_path / "final.mp4"
    make_clip(clip)
    (project / f"{name}_RO.srt").write_text(SRT_RO, encoding="utf-8")
    (project / f"{name}_EN.srt").write_text(SRT_EN, encoding="utf-8")
    (project / "metadata.json").write_text(json.dumps(METADATA), encoding="utf-8")
    make_thumbnail(project / "thumbnail.jpg")
    return project, clip


def run_kit(project, clip, extra=None):
    argv = [
        "--project",
        str(project),
        "--final-video",
        str(clip),
        "--json",
    ]
    if extra:
        argv += extra
    return publish_kit.main(argv)


def test_builds_the_kit_and_links_the_video(tmp_path, capsys):
    project, clip = build_project(tmp_path)
    assert run_kit(project, clip) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    kit = project / "publish-kit"
    assert (kit / f"{project.name}_RO.srt").is_file()
    assert (kit / f"{project.name}_EN.srt").is_file()
    assert (kit / "thumbnail.jpg").is_file()
    assert (kit / "studio.md").is_file()
    video = kit / "final.mp4"
    assert video.exists()
    assert os.path.islink(video) or os.stat(video).st_ino == os.stat(clip).st_ino
    assert summary["video_link"] in {"hardlink", "symlink"}
    studio = (kit / "studio.md").read_text(encoding="utf-8")
    assert "Title one" in studio
    assert "Title two" in studio and "Title three" in studio
    assert "00:00 Intro" in studio
    assert "a, b" in studio
    assert "Q?" in studio


def test_says_before_writing_the_video(tmp_path, capsys):
    project, clip = build_project(tmp_path)
    argv = ["--project", str(project), "--final-video", str(clip)]
    assert publish_kit.main(argv) == EXIT_OK
    out = capsys.readouterr().out
    assert "linking the final video" in out


def test_path_only_fallback(tmp_path, monkeypatch, capsys):
    project, clip = build_project(tmp_path)
    monkeypatch.setattr(publish_kit, "link_video", lambda source, dest: None)
    assert run_kit(project, clip) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    kit = project / "publish-kit"
    assert not (kit / "final.mp4").exists()
    assert summary["video_link"] == "path"
    studio = (kit / "studio.md").read_text(encoding="utf-8")
    assert str(clip) in studio


def test_missing_en_srt_exits_2(tmp_path, capsys):
    project, clip = build_project(tmp_path)
    (project / f"{project.name}_EN.srt").unlink()
    assert run_kit(project, clip) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "_EN.srt" in err
    assert not (project / "publish-kit").exists()


def test_long_title_exits_2(tmp_path, capsys):
    project, clip = build_project(tmp_path)
    assert run_kit(project, clip, extra=["--kit-title", "x" * 101]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "101" in err
    assert not (project / "publish-kit").exists()


def test_bad_thumbnail_dimensions_exits_2(tmp_path, capsys):
    project, clip = build_project(tmp_path)
    make_thumbnail(project / "thumbnail.jpg", size=(1281, 720))
    assert run_kit(project, clip) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "1281x720" in err
    assert not (project / "publish-kit").exists()


def test_no_thumbnail_builds_the_kit(tmp_path, capsys):
    project, clip = build_project(tmp_path)
    (project / "thumbnail.jpg").unlink()
    assert run_kit(project, clip) == EXIT_OK
    kit = project / "publish-kit"
    assert not (kit / "thumbnail.jpg").exists()
    studio = (kit / "studio.md").read_text(encoding="utf-8")
    assert "Make and upload the thumbnail (none in the kit yet)" in studio


def test_missing_metadata_json_exits_2(tmp_path, capsys):
    project, clip = build_project(tmp_path)
    (project / "metadata.json").unlink()
    assert run_kit(project, clip) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "metadata.json" in err
    assert not (project / "publish-kit").exists()


def test_missing_final_video_exits_2(tmp_path, capsys):
    project, clip = build_project(tmp_path)
    clip.unlink()
    assert run_kit(project, clip) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "final video not found" in err
    assert not (project / "publish-kit").exists()


def test_rerun_overwrites_the_kit(tmp_path, capsys):
    project, clip = build_project(tmp_path)
    assert run_kit(project, clip) == EXIT_OK
    capsys.readouterr()
    sentinel = project / "publish-kit" / "sentinel.txt"
    sentinel.write_text("old", encoding="utf-8")
    assert run_kit(project, clip) == EXIT_OK
    assert not sentinel.exists()
    assert (project / "publish-kit" / "studio.md").is_file()


def test_loudness_uses_the_loudened_file(tmp_path, monkeypatch, capsys):
    project, clip = build_project(tmp_path)
    loud = tmp_path / "final_loud.mp4"
    loud.write_bytes(b"loud")
    monkeypatch.setattr(publish_kit, "run_loudness", lambda final: (loud, None))
    assert run_kit(project, clip, extra=["--kit-loudness"]) == EXIT_OK
    kit = project / "publish-kit"
    assert (kit / "final_loud.mp4").exists()
    assert not (kit / "final.mp4").exists()


def test_loudness_failure_exits_3(tmp_path, monkeypatch, capsys):
    project, clip = build_project(tmp_path)
    monkeypatch.setattr(publish_kit, "run_loudness", lambda final: (None, "boom"))
    assert run_kit(project, clip, extra=["--kit-loudness"]) == EXIT_RUNTIME
    err = capsys.readouterr().err
    assert "loudness failed" in err
    assert not (project / "publish-kit").exists()


def test_help_lists_an_example(capsys):
    with pytest.raises(SystemExit) as exc:
        publish_kit.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--project" in out
    assert "example" in out


def test_json_summary_fields(tmp_path, capsys):
    project, clip = build_project(tmp_path)
    assert run_kit(project, clip) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["output"] == "publish-kit/studio.md"
    assert summary["video"] == "final.mp4"
    assert summary["video_link"] in {"hardlink", "symlink", "path"}
    assert summary["title"] == "Title one"
    assert summary["checks"]
    assert isinstance(summary["wall_time_s"], (int, float))
    assert summary["llm_cost_usd"] == 0.0


def test_json_failure_is_one_object(tmp_path, capsys):
    project, clip = build_project(tmp_path)
    (project / f"{project.name}_EN.srt").unlink()
    assert run_kit(project, clip) == EXIT_USAGE
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["exit_code"] == EXIT_USAGE
    assert len(captured.out.strip().splitlines()) == 1
    error_lines = [line for line in captured.err.splitlines() if line.startswith("[error]")]
    assert len(error_lines) == 1


def test_chapter_lines_en_uses_title_en_when_present():
    chapters = [
        {"timestamp": "00:00:00", "title": "Intro", "title_en": "Intro"},
        {"timestamp": "00:01:00", "title": "Lume"},
    ]
    assert publish_kit.chapter_lines(chapters, "en") == "00:00 Intro\n01:00 Lume"
    assert publish_kit.chapter_lines(chapters) == "00:00 Intro\n01:00 Lume"
