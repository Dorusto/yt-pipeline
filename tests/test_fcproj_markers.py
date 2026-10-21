import json
from pathlib import Path

import fcproj_markers as fm
import graphics_plan

TICKS = 254016000000


def marker(marker_id, seconds, name, comment="", color="Mango", kind="Comment"):
    return {
        "id": marker_id,
        "start": int(round(seconds * TICKS)),
        "duration": 0,
        "name": name,
        "comment": comment,
        "kind": kind,
        "color": color,
    }


def write_project(path, markers, clip_markers=None):
    clip_markers = clip_markers or []
    audio_items = []
    for index, count in enumerate(clip_markers):
        audio_items.append(
            {
                "id": 20 + index,
                "item": 1,
                "name": "camera A.mp4",
                "start": index * TICKS,
                "duration": TICKS,
                "source_in": 0,
                "speed": 1.0,
                "reverse": False,
                "enabled": True,
                "markers": [marker(100 + index * 10 + n, 0.5, f"clip {index}-{n}") for n in range(count)],
            }
        )
    project = {
        "format": "filmcraft-project",
        "schema_version": 1,
        "project": {
            "name": "Fixture",
            "settings": {},
            "items": {
                "1": {
                    "id": 1,
                    "name": "camera A.mp4",
                    "kind": {"Media": {"media": {"File": {"path": "/footage/camera A.mp4"}}}},
                },
                "2": {
                    "id": 2,
                    "name": "camera B.mp4",
                    "kind": {"Media": {"media": {"File": {"path": "/footage/camera B.mp4"}}}},
                },
                "10": {
                    "id": 10,
                    "name": "Main",
                    "kind": {
                        "Sequence": {
                            "settings": {"width": 1920, "height": 1080, "frame_rate": {"num": 25, "den": 1}},
                            "video_tracks": [{"id": 11, "kind": "Video", "name": "Video 1", "items": []}],
                            "audio_tracks": [{"id": 12, "kind": "Audio", "name": "Audio 1", "items": audio_items}],
                            "markers": markers,
                            "caption_tracks": [],
                        }
                    },
                },
            },
            "next_id": 30,
        },
        "view": {},
    }
    path.write_text(json.dumps(project), encoding="utf-8")
    return path


def default_markers():
    return [
        marker(3, 30.0, "Capitolul 2", color="Green", kind="Chapter"),
        marker(1, 12.16, "arat\u0103 num\u0103rul", comment="counter idea", color="Mango"),
        marker(2, 1.0, "de ref\u0103cut", comment="sunet prost", color="Rose"),
    ]


def run(tmp_path, capsys, project, extra=None):
    out = tmp_path / "markers.json"
    todo = tmp_path / "todo.md"
    argv = [str(project), "--out", str(out), "--todo", str(todo), "--json"]
    if extra:
        argv = [str(project), *extra, "--out", str(out), "--todo", str(todo), "--json"]
    rc = fm.main(argv)
    captured = capsys.readouterr()
    summary = json.loads(captured.out) if captured.out.strip() else None
    return rc, out, todo, summary, captured.err


def test_markers_times_timecodes_and_sort(tmp_path, capsys):
    project = write_project(tmp_path / "proj.fcproj", default_markers())
    rc, out, _, summary, _ = run(tmp_path, capsys, project)
    assert rc == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["schema_version"] == "1"
    assert document["sequence"] == "Main"
    assert document["source"] == str(project)
    markers = document["markers"]
    assert [m["time_s"] for m in markers] == [1.0, 12.16, 30.0]
    assert [m["timecode"] for m in markers] == ["00:00:01:00", "00:00:12:04", "00:00:30:00"]
    assert summary["markers"] == 3
    assert summary["rose"] == 1


def test_color_key_maps_to_colour(tmp_path, capsys):
    project = write_project(tmp_path / "proj.fcproj", default_markers())
    _, out, _, _, _ = run(tmp_path, capsys, project)
    markers = json.loads(out.read_text(encoding="utf-8"))["markers"]
    assert [m["colour"] for m in markers] == ["Rose", "Mango", "Green"]
    assert all("color" not in m for m in markers)
    assert [m["kind"] for m in markers] == ["Comment", "Comment", "Chapter"]
    assert markers[1]["name"] == "arat\u0103 num\u0103rul"
    assert markers[1]["comment"] == "counter idea"


def test_todo_holds_only_rose(tmp_path, capsys):
    project = write_project(tmp_path / "proj.fcproj", default_markers())
    _, _, todo, _, _ = run(tmp_path, capsys, project)
    assert todo.read_text(encoding="utf-8") == "- [ ] 00:00:01:00 (1.0 s) \u2014 de ref\u0103cut: sunet prost\n"


def test_todo_without_comment_omits_colon(tmp_path, capsys):
    markers = [marker(1, 2.0, "doar nume", color="Rose")]
    project = write_project(tmp_path / "proj.fcproj", markers)
    _, _, todo, _, _ = run(tmp_path, capsys, project)
    assert todo.read_text(encoding="utf-8") == "- [ ] 00:00:02:00 (2.0 s) \u2014 doar nume\n"


def test_todo_without_rose_says_so(tmp_path, capsys):
    markers = [marker(1, 2.0, "verde", color="Green")]
    project = write_project(tmp_path / "proj.fcproj", markers)
    _, _, todo, _, _ = run(tmp_path, capsys, project)
    assert todo.read_text(encoding="utf-8") == "No Rose markers in this project.\n"


def test_clip_markers_ignored_counted(tmp_path, capsys):
    project = write_project(tmp_path / "proj.fcproj", default_markers(), clip_markers=[2, 1])
    _, _, _, summary, _ = run(tmp_path, capsys, project)
    assert summary["clip_markers_ignored"] == 3


def test_rerun_overwrites_cleanly(tmp_path, capsys):
    project = write_project(tmp_path / "proj.fcproj", default_markers())
    _, out, todo, _, _ = run(tmp_path, capsys, project)
    first = out.read_text(encoding="utf-8")
    write_project(project, [marker(1, 5.0, "verde", color="Green")])
    rc, out, todo, summary, _ = run(tmp_path, capsys, project)
    assert rc == 0
    assert summary["rose"] == 0
    assert todo.read_text(encoding="utf-8") == "No Rose markers in this project.\n"
    assert json.loads(out.read_text(encoding="utf-8"))["markers"][0]["time_s"] == 5.0
    assert first != out.read_text(encoding="utf-8")


def test_markers_json_loads_in_graphics_plan(tmp_path, capsys):
    project = write_project(tmp_path / "proj.fcproj", default_markers())
    _, out, _, _, _ = run(tmp_path, capsys, project)
    candidates = graphics_plan.load_markers(out)
    assert len(candidates) == 1
    assert candidates[0]["colour"] == "Mango"
    assert candidates[0]["time_s"] == 12.16


def test_unknown_sequence_lists_names(tmp_path, capsys):
    project = write_project(tmp_path / "proj.fcproj", default_markers())
    rc, _, _, _, err = run(tmp_path, capsys, project, extra=["--sequence", "Nope"])
    assert rc == 2
    assert "Nope" in err and "Main" in err


def test_missing_file_is_user_error(tmp_path, capsys):
    rc = fm.main([str(tmp_path / "nope.fcproj"), "--json"])
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_not_a_filmcraft_project(tmp_path, capsys):
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    rc = fm.main([str(path), "--json"])
    assert rc == 2
    assert "not a FilmCraft project" in capsys.readouterr().err


def test_bad_marker_shape_names_problem(tmp_path, capsys):
    markers = [{"id": 1, "name": "no start", "color": "Rose"}]
    project = write_project(tmp_path / "proj.fcproj", markers)
    rc = fm.main([str(project), "--json"])
    assert rc == 2
    assert "numeric start" in capsys.readouterr().err


def test_no_sequence_is_user_error(tmp_path, capsys):
    project = write_project(tmp_path / "proj.fcproj", [])
    data = json.loads(project.read_text(encoding="utf-8"))
    del data["project"]["items"]["10"]
    project.write_text(json.dumps(data), encoding="utf-8")
    rc = fm.main([str(project), "--json"])
    assert rc == 2
    assert "no sequence" in capsys.readouterr().err


def test_default_outputs_next_to_project(tmp_path, capsys):
    project = write_project(tmp_path / "proj.fcproj", default_markers())
    rc = fm.main([str(project), "--json"])
    assert rc == 0
    assert (tmp_path / "markers.json").is_file()
    assert (tmp_path / "todo.md").is_file()
    capsys.readouterr()
