import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import publish

FIXTURES = Path(__file__).parent / "fixtures" / "publish"
FCPROJ = FIXTURES / "fixture.fcproj"

SRT_RAW = (
    "1\n00:00:00,200 --> 00:00:03,800\ndoi trei patru cinci.\n\n"
    "2\n00:00:04,200 --> 00:00:06,600\nAlfa beta gama.\n\n"
    "3\n00:00:08,600 --> 00:00:09,800\nopt nouă.\n"
)
SRT_EN = (
    "1\n00:00:00,200 --> 00:00:03,800\nHello\n\n"
    "2\n00:00:04,200 --> 00:00:06,600\nWorld\n\n"
    "3\n00:00:08,600 --> 00:00:09,800\nEnd\n"
)


class Recorder:
    """Stand-in for ``publish.execute``: writes the child outputs and returns success."""

    def __init__(self, fail_at=None, costs=None):
        self.fail_at = fail_at
        self.costs = costs or {}
        self.calls = []
        self.argvs = []

    def __call__(self, step, argv, ctx):
        name = step["name"]
        self.calls.append(name)
        self.argvs.append((name, argv))
        if name == self.fail_at:
            return {
                "status": "FAIL",
                "returncode": 1,
                "outputs": [],
                "llm_cost_usd": 0.0,
                "stderr": "boom",
                "stdout": "",
                "wall_time_s": 0.1,
            }
        self.create_outputs(name, ctx)
        return {
            "status": "done",
            "returncode": 0,
            "outputs": [],
            "llm_cost_usd": self.costs.get(name, 0.0),
            "stderr": "",
            "stdout": "",
            "wall_time_s": 0.1,
        }

    def create_outputs(self, name, ctx):
        project = ctx.project
        if name == "subtitles":
            (project / f"{ctx.name}_RO.srt").write_text(SRT_RAW, encoding="utf-8")
            (project / f"{ctx.name}_words.json").write_text("[]", encoding="utf-8")
        elif name == "correct":
            (project / f"{ctx.name}_RO.srt").write_text(SRT_RAW, encoding="utf-8")
        elif name == "translate":
            (project / f"{ctx.name}_EN.srt").write_text(SRT_EN, encoding="utf-8")
        elif name == "thumbnail":
            (project / "thumbnail.jpg").write_bytes(b"jpg")
        elif name == "metadata":
            (project / "metadata.md").write_text("# Metadata", encoding="utf-8")


def write_context(tmp_path):
    context = tmp_path / "ctx.md"
    context.write_text("Angle: test", encoding="utf-8")
    return context


def run_publish(project, context, extra=None):
    argv = [
        "--project",
        str(project),
        "--context",
        str(context),
        "--fcproj",
        str(FCPROJ),
        "--sequence",
        "Main",
        "--words-dir",
        str(FIXTURES),
    ]
    if extra:
        argv += extra
    return publish.main(argv)


def test_step_order_stops_at_review(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    recorder = Recorder()
    monkeypatch.setattr(publish, "execute", recorder)
    assert run_publish(project, write_context(tmp_path), extra=["--json"]) == 0
    assert recorder.calls == ["subtitles", "correct"]
    summary = json.loads(capsys.readouterr().out)
    assert summary["stopped_at"] == "review"
    statuses = {entry["name"]: entry["status"] for entry in summary["steps"]}
    assert statuses["review"] == "stopped"
    assert "translate" not in statuses
    assert "thumbnail" not in statuses
    assert "metadata" not in statuses


def test_resume_runs_the_rest(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    recorder = Recorder()
    monkeypatch.setattr(publish, "execute", recorder)
    assert run_publish(project, write_context(tmp_path), extra=["--resume", "--json"]) == 0
    assert recorder.calls == ["subtitles", "correct", "translate", "metadata"]
    summary = json.loads(capsys.readouterr().out)
    assert summary["stopped_at"] is None
    statuses = {entry["name"]: entry["status"] for entry in summary["steps"]}
    assert statuses["review"] == "passed"
    assert statuses["thumbnail"] == "skipped"


def test_command_lines(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    recorder = Recorder()
    monkeypatch.setattr(publish, "execute", recorder)
    assert run_publish(project, write_context(tmp_path), extra=["--resume", "--json"]) == 0
    commands = dict(recorder.argvs)
    name = project.name
    subs = commands["subtitles"]
    assert subs[1] == "scripts/subs_from_edit.py"
    assert "--fcproj" in subs and str(FCPROJ) in subs
    assert "--sequence" in subs and "Main" in subs
    assert "--words-dir" in subs and str(FIXTURES) in subs
    assert "--out-dir" in subs and str(project) in subs
    assert "--name" in subs and name in subs
    assert subs[-1] == "--json"
    correct = commands["correct"]
    assert correct[1] == "scripts/correct_srt.py"
    assert correct[2] == str(project / f"{name}.srt")
    assert correct[3] == str(project / f"{name}_RO.srt")
    translate = commands["translate"]
    assert translate[1] == "scripts/translate_srt.py"
    assert translate[2] == str(project / f"{name}_RO.srt")
    assert translate[3] == str(project / f"{name}_EN.srt")
    metadata = commands["metadata"]
    assert metadata[1] == "scripts/metadata.py"
    assert metadata[2] == str(project / f"{name}_RO.srt")
    assert "--context" in metadata
    assert "--out-dir" in metadata and str(project) in metadata
    assert "--name" in metadata and name in metadata
    assert metadata[-1] == "--json"


def test_chapters_flag_on_metadata_step(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    recorder = Recorder()
    monkeypatch.setattr(publish, "execute", recorder)
    chapters = tmp_path / "chapters.json"
    chapters.write_text("{}", encoding="utf-8")
    assert run_publish(
        project,
        write_context(tmp_path),
        extra=["--resume", "--chapters", str(chapters), "--json"],
    ) == 0
    metadata = dict(recorder.argvs)["metadata"]
    assert "--chapters" in metadata
    assert str(chapters) in metadata
    summary = json.loads(capsys.readouterr().out)
    assert isinstance(summary["wall_time_s"], (int, float))
    assert "llm_cost_usd" in summary


def test_media_source_uses_transcribe(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    recorder = Recorder()
    monkeypatch.setattr(publish, "execute", recorder)
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"")
    argv = [
        "--project",
        str(project),
        "--context",
        str(write_context(tmp_path)),
        "--media",
        str(media),
        "--transcribe-backend",
        "mock",
        "--resume",
        "--json",
    ]
    assert publish.main(argv) == 0
    subs = dict(recorder.argvs)["subtitles"]
    assert subs[1] == "scripts/transcribe.py"
    assert str(media) in subs
    assert "--backend" in subs and "mock" in subs
    assert "--words-dir" not in subs


def test_from_edit_source_uses_subs_from_edit(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    recorder = Recorder()
    monkeypatch.setattr(publish, "execute", recorder)
    edit = tmp_path / "edit.json"
    edit.write_text("{}", encoding="utf-8")
    argv = [
        "--project",
        str(project),
        "--context",
        str(write_context(tmp_path)),
        "--from-edit",
        str(edit),
        "--words-dir",
        str(FIXTURES),
        "--resume",
        "--json",
    ]
    assert publish.main(argv) == 0
    subs = dict(recorder.argvs)["subtitles"]
    assert subs[1] == "scripts/subs_from_edit.py"
    assert "--from-edit" in subs and str(edit) in subs
    assert "--fcproj" not in subs


def test_second_run_skips_everything(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    context = write_context(tmp_path)
    recorder = Recorder()
    monkeypatch.setattr(publish, "execute", recorder)
    assert run_publish(project, context, extra=["--resume", "--json"]) == 0
    capsys.readouterr()
    recorder.calls.clear()
    assert run_publish(project, context, extra=["--resume", "--json"]) == 0
    assert recorder.calls == []
    summary = json.loads(capsys.readouterr().out)
    statuses = {entry["name"]: entry["status"] for entry in summary["steps"]}
    assert statuses["subtitles"] == "skipped"
    assert statuses["correct"] == "skipped"
    assert statuses["translate"] == "skipped"
    assert statuses["metadata"] == "skipped"


def test_force_reruns_every_step(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    context = write_context(tmp_path)
    recorder = Recorder()
    monkeypatch.setattr(publish, "execute", recorder)
    assert run_publish(project, context, extra=["--resume", "--json"]) == 0
    capsys.readouterr()
    recorder.calls.clear()
    assert run_publish(project, context, extra=["--resume", "--force", "--json"]) == 0
    assert recorder.calls == ["subtitles", "correct", "translate", "metadata"]


def test_force_still_stops_at_review(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    context = write_context(tmp_path)
    recorder = Recorder()
    monkeypatch.setattr(publish, "execute", recorder)
    assert run_publish(project, context, extra=["--resume", "--json"]) == 0
    capsys.readouterr()
    recorder.calls.clear()
    assert run_publish(project, context, extra=["--force", "--json"]) == 0
    assert recorder.calls == ["subtitles", "correct"]
    summary = json.loads(capsys.readouterr().out)
    assert summary["stopped_at"] == "review"


def test_thumbnail_skipped_without_photo(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    recorder = Recorder()
    monkeypatch.setattr(publish, "execute", recorder)
    assert run_publish(project, write_context(tmp_path), extra=["--resume", "--json"]) == 0
    assert "thumbnail" not in recorder.calls
    summary = json.loads(capsys.readouterr().out)
    thumbnail = next(entry for entry in summary["steps"] if entry["name"] == "thumbnail")
    assert thumbnail["status"] == "skipped"
    state = json.loads((project / "publish_state.json").read_text(encoding="utf-8"))
    assert state["steps"]["thumbnail"]["reason"] == "no photo"


def test_thumbnail_runs_with_photo(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    recorder = Recorder()
    monkeypatch.setattr(publish, "execute", recorder)
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"")
    extra = [
        "--photo",
        str(photo),
        "--title",
        "T",
        "--keyword",
        "K",
        "--theme",
        "tech",
        "--resume",
        "--json",
    ]
    assert run_publish(project, write_context(tmp_path), extra=extra) == 0
    assert "thumbnail" in recorder.calls
    command = dict(recorder.argvs)["thumbnail"]
    assert command[1] == "scripts/thumbnail.py"
    assert "--photo" in command and str(photo) in command
    assert "--out" in command and str(project) in command
    assert command[-1] == "--json"


def test_failing_child_aborts(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    recorder = Recorder(fail_at="correct")
    monkeypatch.setattr(publish, "execute", recorder)
    assert run_publish(project, write_context(tmp_path), extra=["--resume", "--json"]) == 2
    assert recorder.calls == ["subtitles", "correct"]
    summary = json.loads(capsys.readouterr().out)
    statuses = {entry["name"]: entry["status"] for entry in summary["steps"]}
    assert statuses["correct"] == "failed"
    state = json.loads((project / "publish_state.json").read_text(encoding="utf-8"))
    assert state["steps"]["correct"]["status"] == "failed"


def test_naming_normalization(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    recorder = Recorder(fail_at="correct")
    monkeypatch.setattr(publish, "execute", recorder)
    assert run_publish(project, write_context(tmp_path), extra=["--json"]) == 2
    raw = project / f"{project.name}.srt"
    assert raw.is_file()
    assert raw.read_text(encoding="utf-8") == SRT_RAW
    assert not (project / f"{project.name}_RO.srt").exists()


def test_json_summary_fields(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    recorder = Recorder(costs={"translate": 0.01, "metadata": 0.02})
    monkeypatch.setattr(publish, "execute", recorder)
    assert run_publish(project, write_context(tmp_path), extra=["--resume", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["project"] == str(project)
    assert summary["name"] == project.name
    assert summary["stopped_at"] is None
    assert summary["outputs"]["srt_raw"] == str(project / f"{project.name}.srt")
    assert summary["outputs"]["words_json"] == str(project / f"{project.name}_words.json")
    assert summary["outputs"]["srt_ro"] == str(project / f"{project.name}_RO.srt")
    assert summary["outputs"]["srt_en"] == str(project / f"{project.name}_EN.srt")
    assert summary["outputs"]["thumbnail"] is None
    assert summary["outputs"]["metadata"] == str(project / "metadata.md")
    assert isinstance(summary["wall_time_s"], (int, float))
    assert summary["llm_cost_usd"] == pytest.approx(0.03)


def test_state_file_shape(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    recorder = Recorder()
    monkeypatch.setattr(publish, "execute", recorder)
    assert run_publish(project, write_context(tmp_path), extra=["--resume", "--json"]) == 0
    state = json.loads((project / "publish_state.json").read_text(encoding="utf-8"))
    assert state["tool"] == "scripts/publish.py"
    assert state["name"] == project.name
    assert state["options"]["source"] == "fcproj"
    assert state["options"]["sequence"] == "Main"
    assert state["options"]["photo"] is None
    assert state["steps"]["subtitles"]["status"] == "done"
    assert state["steps"]["subtitles"]["command"][1] == "scripts/subs_from_edit.py"
    assert state["steps"]["review"]["status"] == "passed"
    assert state["steps"]["translate"]["status"] == "done"
    assert state["steps"]["metadata"]["status"] == "done"


def test_conflicting_sources_exit_2(tmp_path, capsys):
    project = tmp_path / "proj"
    context = write_context(tmp_path)
    rc = publish.main(
        [
            "--project",
            str(project),
            "--context",
            str(context),
            "--fcproj",
            str(FCPROJ),
            "--sequence",
            "Main",
            "--media",
            "clip.mp4",
            "--words-dir",
            str(FIXTURES),
        ]
    )
    assert rc == 2
    assert "exactly one" in capsys.readouterr().err


def test_words_dir_required_for_fcproj(tmp_path, capsys):
    project = tmp_path / "proj"
    context = write_context(tmp_path)
    rc = publish.main(
        [
            "--project",
            str(project),
            "--context",
            str(context),
            "--fcproj",
            str(FCPROJ),
            "--sequence",
            "Main",
        ]
    )
    assert rc == 2
    assert "--words-dir" in capsys.readouterr().err


def test_words_dir_not_allowed_with_media(tmp_path, capsys):
    project = tmp_path / "proj"
    context = write_context(tmp_path)
    rc = publish.main(
        [
            "--project",
            str(project),
            "--context",
            str(context),
            "--media",
            "clip.mp4",
            "--words-dir",
            str(FIXTURES),
        ]
    )
    assert rc == 2
    assert "--words-dir" in capsys.readouterr().err


def test_title_without_photo_exit_2(tmp_path, capsys):
    project = tmp_path / "proj"
    context = write_context(tmp_path)
    rc = publish.main(
        [
            "--project",
            str(project),
            "--context",
            str(context),
            "--fcproj",
            str(FCPROJ),
            "--sequence",
            "Main",
            "--words-dir",
            str(FIXTURES),
            "--title",
            "T",
        ]
    )
    assert rc == 2
    assert "--photo" in capsys.readouterr().err


def test_photo_without_title_exit_2(tmp_path, capsys):
    project = tmp_path / "proj"
    context = write_context(tmp_path)
    rc = publish.main(
        [
            "--project",
            str(project),
            "--context",
            str(context),
            "--fcproj",
            str(FCPROJ),
            "--sequence",
            "Main",
            "--words-dir",
            str(FIXTURES),
            "--photo",
            "photo.jpg",
        ]
    )
    assert rc == 2
    assert "--title" in capsys.readouterr().err


def test_help_lists_an_example(capsys):
    with pytest.raises(SystemExit) as exc:
        publish.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--project" in out
    assert "example:" in out


def write_mock_config(tmp_path):
    config = {
        "models": {"translate": "mock/model", "metadata": "mock/model"},
        "llm_budget_usd": 5.0,
        "llm_backend": "mock",
        "llm_mock_text": {
            "translate": "[1] Hello\n[2] World\n[3] End",
            "metadata": json.dumps(
                {
                    "titles": ["T1", "T2", "T3"],
                    "intro": {"ro": "Intro RO", "en": "Intro EN"},
                    "github_line": None,
                    "previous_line": None,
                    "subscribe_line": {"ro": "Sub RO", "en": "Sub EN"},
                    "promo_lines": [],
                    "chapters": [{"timestamp": "00:00:00", "title": "Intro"}],
                    "tags": ["a", "b"],
                    "pinned_comment": "Q?",
                    "thumbnail_concepts": [
                        {
                            "name": "Concept one",
                            "pose": "facing camera",
                            "emotion": "joy",
                            "text": {"title": "T1", "keyword": "K1", "kicker": ""},
                            "theme": "tech",
                            "source": {"kind": "new_photo"},
                        },
                        {
                            "name": "Concept two",
                            "pose": "hands up",
                            "emotion": "surprise",
                            "text": {"title": "T2", "keyword": "K2", "kicker": "kick"},
                            "theme": "creator",
                            "source": {"kind": "new_photo"},
                        },
                    ],
                }
            ),
        },
        "channel": {
            "members": "Andrei, Mihai și Maria",
            "github_url": "https://example.invalid/channel",
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path


def run_publish_subprocess(project, config_path, context_path, extra=None):
    argv = [
        sys.executable,
        "scripts/publish.py",
        "--project",
        str(project),
        "--context",
        str(context_path),
        "--fcproj",
        str(FCPROJ),
        "--sequence",
        "Main",
        "--words-dir",
        str(FIXTURES),
    ]
    if extra:
        argv += extra
    env = dict(os.environ)
    env["YT_CONFIG"] = str(config_path)
    return subprocess.run(
        argv, capture_output=True, text=True, cwd=str(publish.REPO_ROOT), env=env
    )


def test_end_to_end_with_mock_llm(tmp_path):
    project = tmp_path / "project"
    config_path = write_mock_config(tmp_path)
    context_path = write_context(tmp_path)
    name = project.name

    run1 = run_publish_subprocess(project, config_path, context_path, extra=["--json"])
    assert run1.returncode == 0, run1.stderr
    summary1 = json.loads(run1.stdout)
    assert summary1["stopped_at"] == "review"
    assert (project / f"{name}.srt").is_file()
    assert (project / f"{name}_words.json").is_file()
    assert (project / f"{name}_RO.srt").is_file()
    assert (project / "publish_state.json").is_file()
    assert (project / f"{name}_RO.srt").read_text(encoding="utf-8").count("-->") == 3

    run2 = run_publish_subprocess(
        project, config_path, context_path, extra=["--resume", "--json"]
    )
    assert run2.returncode == 0, run2.stderr
    summary2 = json.loads(run2.stdout)
    assert summary2["stopped_at"] is None
    english = (project / f"{name}_EN.srt").read_text(encoding="utf-8")
    assert english.count("-->") == 3
    assert "Hello" in english and "World" in english and "End" in english
    metadata_text = (project / "metadata.md").read_text(encoding="utf-8")
    assert "Andrei, Mihai și Maria" in metadata_text
    assert "nu uita să fii creativ" in metadata_text
    state = json.loads((project / "publish_state.json").read_text(encoding="utf-8"))
    assert state["steps"]["translate"]["status"] == "done"
    assert state["steps"]["metadata"]["status"] == "done"

    run3 = run_publish_subprocess(
        project, config_path, context_path, extra=["--resume", "--json"]
    )
    assert run3.returncode == 0, run3.stderr
    summary3 = json.loads(run3.stdout)
    statuses = {entry["name"]: entry["status"] for entry in summary3["steps"]}
    assert statuses["review"] == "passed"
    for step in ("subtitles", "correct", "translate", "thumbnail", "metadata"):
        assert statuses[step] == "skipped"


KIT_METADATA = {
    "name": "proj",
    "titles": ["Title one", "Title two", "Title three"],
    "description_ro": "Desc RO",
    "description_en": "Desc EN",
    "chapters": [{"timestamp": "00:00:00", "title": "Intro"}],
    "tags": ["a", "b"],
    "pinned_comment": "Q?",
    "thumbnail_concepts": [],
}


def make_thumbnail(path, size=(1280, 720)):
    from PIL import Image

    Image.new("RGB", size, (10, 20, 30)).save(path, "JPEG")


def make_clip(path):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=10:duration=1",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def build_kit_project(tmp_path, name="proj"):
    project = tmp_path / name
    project.mkdir()
    (project / f"{name}.srt").write_text(SRT_RAW, encoding="utf-8")
    (project / f"{name}_words.json").write_text("[]", encoding="utf-8")
    (project / f"{name}_RO.srt").write_text(SRT_RAW, encoding="utf-8")
    (project / f"{name}_EN.srt").write_text(SRT_EN, encoding="utf-8")
    (project / "metadata.md").write_text("# Metadata", encoding="utf-8")
    (project / "metadata.json").write_text(json.dumps(KIT_METADATA), encoding="utf-8")
    make_thumbnail(project / "thumbnail.jpg")
    clip = tmp_path / "final.mp4"
    make_clip(clip)
    return project, clip


def run_kit(project, clip, extra=None):
    argv = [
        "--project",
        str(project),
        "--context",
        str(project / "ctx.md"),
        "--fcproj",
        str(FCPROJ),
        "--sequence",
        "Main",
        "--words-dir",
        str(FIXTURES),
        "--resume",
        "--kit",
        "--final-video",
        str(clip),
        "--json",
    ]
    if extra:
        argv += extra
    return publish.main(argv)


def test_kit_builds_the_folder(tmp_path, capsys):
    project, clip = build_kit_project(tmp_path)
    assert run_kit(project, clip) == 0
    summary = json.loads(capsys.readouterr().out)
    kit = project / "publish-kit"
    assert (kit / "final.mp4").is_file()
    assert (kit / f"{project.name}_RO.srt").is_file()
    assert (kit / f"{project.name}_EN.srt").is_file()
    assert (kit / "thumbnail.jpg").is_file()
    studio = (kit / "studio.md").read_text(encoding="utf-8")
    assert "# Studio kit — proj" in studio
    assert "Title one" in studio
    assert "Title two" in studio and "Title three" in studio
    assert "a, b" in studio
    assert "Q?" in studio
    ro_block = studio.split("## Description (RO)\n\n```\n", 1)[1].split("\n```", 1)[0]
    en_block = studio.split("## Description (EN)\n\n```\n", 1)[1].split("\n```", 1)[0]
    assert ro_block.endswith("00:00 Intro")
    assert en_block.endswith("00:00 Intro")
    assert summary["kit"]["output"] == "publish-kit"
    assert summary["kit"]["video"] == "final.mp4"
    assert summary["kit"]["title"] == "Title one"
    assert summary["kit"]["checks"]


def test_kit_title_override(tmp_path, capsys):
    project, clip = build_kit_project(tmp_path)
    assert run_kit(project, clip, extra=["--kit-title", "Custom title"]) == 0
    studio = (project / "publish-kit" / "studio.md").read_text(encoding="utf-8")
    assert "Custom title" in studio
    assert "Title one" in studio


def test_kit_long_title_fails(tmp_path, capsys):
    project, clip = build_kit_project(tmp_path)
    assert run_kit(project, clip, extra=["--kit-title", "x" * 101]) == 2
    err = capsys.readouterr().err
    assert "101" in err
    assert not (project / "publish-kit").exists()


def test_kit_missing_en_srt_fails(tmp_path, monkeypatch, capsys):
    project, clip = build_kit_project(tmp_path)
    (project / f"{project.name}_EN.srt").unlink()
    monkeypatch.setattr(
        publish,
        "execute",
        lambda step, argv, ctx: {
            "status": "done",
            "returncode": 0,
            "outputs": [],
            "llm_cost_usd": 0.0,
            "stderr": "",
            "stdout": "",
            "wall_time_s": 0.0,
        },
    )
    assert run_kit(project, clip) == 2
    err = capsys.readouterr().err
    assert "_EN.srt" in err
    assert not (project / "publish-kit").exists()


def test_kit_bad_thumbnail_dimensions_fails(tmp_path, capsys):
    project, clip = build_kit_project(tmp_path)
    make_thumbnail(project / "thumbnail.jpg", size=(1281, 720))
    assert run_kit(project, clip) == 2
    err = capsys.readouterr().err
    assert "1281x720" in err
    assert not (project / "publish-kit").exists()


def test_kit_oversized_thumbnail_fails(tmp_path, capsys):
    project, clip = build_kit_project(tmp_path)
    with open(project / "thumbnail.jpg", "ab") as handle:
        handle.write(b"\0" * 3_000_000)
    assert run_kit(project, clip) == 2
    err = capsys.readouterr().err
    assert "2 MB" in err
    assert not (project / "publish-kit").exists()


def test_kit_long_tags_fails(tmp_path, capsys):
    project, clip = build_kit_project(tmp_path)
    data = json.loads((project / "metadata.json").read_text(encoding="utf-8"))
    data["tags"] = ["x" * 501]
    (project / "metadata.json").write_text(json.dumps(data), encoding="utf-8")
    assert run_kit(project, clip) == 2
    err = capsys.readouterr().err
    assert "501" in err
    assert not (project / "publish-kit").exists()


def test_kit_missing_metadata_json_fails(tmp_path, capsys):
    project, clip = build_kit_project(tmp_path)
    (project / "metadata.json").unlink()
    assert run_kit(project, clip) == 2
    err = capsys.readouterr().err
    assert "metadata.json" in err
    assert not (project / "publish-kit").exists()


def test_kit_missing_final_video_fails(tmp_path, capsys):
    project, clip = build_kit_project(tmp_path)
    clip.unlink()
    assert run_kit(project, clip) == 2
    err = capsys.readouterr().err
    assert "final video not found" in err
    assert not (project / "publish-kit").exists()


def test_kit_requires_final_video_for_fcproj(tmp_path, capsys):
    project, _ = build_kit_project(tmp_path)
    argv = [
        "--project",
        str(project),
        "--context",
        str(project / "ctx.md"),
        "--fcproj",
        str(FCPROJ),
        "--sequence",
        "Main",
        "--words-dir",
        str(FIXTURES),
        "--resume",
        "--kit",
        "--json",
    ]
    assert publish.main(argv) == 2
    err = capsys.readouterr().err
    assert "--final-video" in err
    assert not (project / "publish-kit").exists()


def test_kit_loudness_uses_the_loudened_file(tmp_path, monkeypatch, capsys):
    project, clip = build_kit_project(tmp_path)
    loud = tmp_path / "final_loud.mp4"
    loud.write_bytes(b"loud")
    monkeypatch.setattr(publish, "run_loudness", lambda ctx, final: (loud, None))
    assert run_kit(project, clip, extra=["--kit-loudness"]) == 0
    kit = project / "publish-kit"
    assert (kit / "final_loud.mp4").is_file()
    assert not (kit / "final.mp4").exists()


def test_kit_loudness_failure_fails(tmp_path, monkeypatch, capsys):
    project, clip = build_kit_project(tmp_path)
    monkeypatch.setattr(publish, "run_loudness", lambda ctx, final: (None, "boom"))
    assert run_kit(project, clip, extra=["--kit-loudness"]) == 2
    err = capsys.readouterr().err
    assert "loudness failed" in err
    assert not (project / "publish-kit").exists()


def test_kit_end_to_end_with_mock_llm(tmp_path):
    project = tmp_path / "project"
    config_path = write_mock_config(tmp_path)
    context_path = write_context(tmp_path)
    project.mkdir()
    make_thumbnail(project / "thumbnail.jpg")
    clip = tmp_path / "final.mp4"
    make_clip(clip)
    run = run_publish_subprocess(
        project,
        config_path,
        context_path,
        extra=["--resume", "--kit", "--final-video", str(clip), "--json"],
    )
    assert run.returncode == 0, run.stderr
    summary = json.loads(run.stdout)
    kit = project / "publish-kit"
    assert (kit / "final.mp4").is_file()
    assert (kit / f"{project.name}_RO.srt").is_file()
    assert (kit / f"{project.name}_EN.srt").is_file()
    assert (kit / "thumbnail.jpg").is_file()
    studio = (kit / "studio.md").read_text(encoding="utf-8")
    assert "T1" in studio
    assert "00:00 Intro" in studio
    assert "a, b" in studio
    assert summary["kit"]["title"] == "T1"


def write_chapters_file(tmp_path, chapters, name="chapters.json"):
    document = {
        "schema_version": "0.1",
        "generated_at": "2026-10-21T00:00:00+00:00",
        "tool": "scripts/chapters.py",
        "chapters": chapters,
    }
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_kit_studio_md_uses_chapters_file(tmp_path):
    project = tmp_path / "project"
    config_path = write_mock_config(tmp_path)
    context_path = write_context(tmp_path)
    project.mkdir()
    make_thumbnail(project / "thumbnail.jpg")
    clip = tmp_path / "final.mp4"
    make_clip(clip)
    chapters = write_chapters_file(
        tmp_path, [{"start_s": 0.0, "title": "FileChapter", "summary": "file"}]
    )
    run = run_publish_subprocess(
        project,
        config_path,
        context_path,
        extra=[
            "--resume",
            "--kit",
            "--final-video",
            str(clip),
            "--chapters",
            str(chapters),
            "--json",
        ],
    )
    assert run.returncode == 0, run.stderr
    studio = (project / "publish-kit" / "studio.md").read_text(encoding="utf-8")
    assert "00:00 FileChapter" in studio
    assert "00:00 Intro" not in studio


def test_kit_skipped_then_forced(tmp_path):
    project = tmp_path / "project"
    config_path = write_mock_config(tmp_path)
    context_path = write_context(tmp_path)
    project.mkdir()
    make_thumbnail(project / "thumbnail.jpg")
    clip = tmp_path / "final.mp4"
    make_clip(clip)
    extra = ["--resume", "--kit", "--final-video", str(clip), "--json"]
    run1 = run_publish_subprocess(project, config_path, context_path, extra=extra)
    assert run1.returncode == 0, run1.stderr
    run2 = run_publish_subprocess(project, config_path, context_path, extra=extra)
    assert run2.returncode == 0, run2.stderr
    statuses = {entry["name"]: entry["status"] for entry in json.loads(run2.stdout)["steps"]}
    assert statuses["kit"] == "skipped"
    run3 = run_publish_subprocess(
        project,
        config_path,
        context_path,
        extra=["--resume", "--kit", "--final-video", str(clip), "--force", "--json"],
    )
    assert run3.returncode == 0, run3.stderr
    statuses = {entry["name"]: entry["status"] for entry in json.loads(run3.stdout)["steps"]}
    assert statuses["kit"] == "done"
