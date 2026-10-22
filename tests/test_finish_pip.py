import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import finish_pip as fp
from common.filmcraft import cli_path

T = fp.TICKS_PER_SECOND
SMALL_OVER = re.compile(fp.DEFAULT_SMALL_OVER)


def screen_item(clip, item, name, start_s, end_s, source_in_s=0.0, enabled=True):
    return {
        "clip": clip,
        "item": item,
        "name": name,
        "start": int(round(start_s * T)),
        "end": int(round(end_s * T)),
        "sourceIn": int(round(source_in_s * T)),
        "sourceOut": int(round((source_in_s + end_s - start_s) * T)),
        "enabled": enabled,
    }


def camera_item(clip, item, name, start_s, end_s, source_in_s=0.0, enabled=True):
    return {
        "clip": clip,
        "item": item,
        "name": name,
        "start": int(round(start_s * T)),
        "end": int(round(end_s * T)),
        "sourceIn": int(round(source_in_s * T)),
        "sourceOut": int(round((source_in_s + end_s - start_s) * T)),
        "enabled": enabled,
    }


def make_sequence(screen_items, camera_items, width=1920, height=1080, extra_tracks=None):
    video = [
        {"id": 15, "name": "Graphics 1", "items": []},
        {"id": 16, "name": "Camera 1", "items": camera_items},
        {"id": 17, "name": "Screen 1", "items": screen_items},
    ]
    if extra_tracks:
        video.extend(extra_tracks)
    return {
        "video": video,
        "audio": [{"id": 18, "name": "Audio 1", "items": []}],
        "settings": {"width": width, "height": height, "frame_rate": {"num": 25, "den": 1}},
    }


def write_sequence(tmp_path, sequence, name="seq.json"):
    path = tmp_path / name
    path.write_text(json.dumps(sequence), encoding="utf-8")
    return path


def make_camera_dir(tmp_path, names):
    directory = tmp_path / "camera"
    directory.mkdir(exist_ok=True)
    for name in names:
        (directory / name).write_bytes(b"")
    return directory


def motion_effects():
    return [{
        "effect": "motion",
        "params": {
            "scale": {"keyframes": [{"time": 0, "value": {"Float": 45.0}}]},
            "position": {"keyframes": [{"time": 0, "value": {"Float": [900.0, 700.0]}}]},
        },
    }]


class FakeRun:
    def __init__(self, import_items=None, inspect=None):
        self.calls = []
        self.import_items = list(import_items or [])
        self.inspect = inspect

    def __call__(self, command, capture_output=True, text=True):
        self.calls.append(list(command))
        joined = " ".join(str(part) for part in command)
        if "matte.py" in joined:
            out = Path(command[command.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"")
            return subprocess.CompletedProcess(command, 0, "", "")
        if "import" in command:
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"errors": [], "items": self.import_items}), ""
            )
        if "inspect" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps(self.inspect), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def matte_calls(self):
        return [call for call in self.calls if "matte.py" in " ".join(str(part) for part in call)]


def run_main(tmp_path, capsys, sequence, camera_names, extra=None, monkeypatch=None, fake=None):
    seq_path = write_sequence(tmp_path, sequence)
    camera_dir = make_camera_dir(tmp_path, camera_names)
    argv = ["--sequence", str(seq_path), "--camera-dir", str(camera_dir), "--out-dir", str(tmp_path)]
    extra = list(extra or [])
    if "--matte-dir" not in extra:
        argv += ["--matte-dir", str(tmp_path / "matte")]
    argv += extra
    if fake is not None and monkeypatch is not None:
        monkeypatch.setattr(fp.subprocess, "run", fake)
        monkeypatch.setattr(fp, "cli_path", lambda: "filmcraft-cli")
    rc = fp.main(argv)
    captured = capsys.readouterr()
    summary = None
    if captured.out.strip():
        try:
            summary = json.loads(captured.out.strip().splitlines()[-1])
        except json.JSONDecodeError:
            summary = None
    return rc, summary, captured


def test_spans_merge_touching_and_keep_gaps():
    sequence = make_sequence(
        [
            screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5),
            screen_item(12, 4, "2026-09-01 10-00-00.mp4", 5, 8),
            screen_item(13, 5, "2026-09-01 10-00-00.mp4", 12, 15),
        ],
        [],
    )
    spans = fp.screen_spans(sequence, SMALL_OVER)
    assert [(span["start"], span["end"]) for span in spans] == [(2 * T, 8 * T), (12 * T, 15 * T)]
    assert all(span["explainer"] is False for span in spans)


def test_spans_ignore_disabled_and_matte_items():
    sequence = make_sequence(
        [
            screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5),
            screen_item(12, 4, "2026-09-01 10-00-00.mp4", 5, 8, enabled=False),
            screen_item(13, 5, "2026-09-01 10-00-00_matte.mov", 8, 11),
            screen_item(14, 6, "2026-09-01 10-00-00-Alpha.mov", 11, 14),
        ],
        [],
    )
    spans = fp.screen_spans(sequence, SMALL_OVER)
    assert [(span["start"], span["end"]) for span in spans] == [(2 * T, 5 * T)]


def test_still_named_screen_does_not_classify_track():
    sequence = make_sequence(
        [screen_item(11, 3, "screen background.png", 0, 10)],
        [],
    )
    assert fp.classify_track(sequence["video"][2]) is None
    assert fp.screen_spans(sequence, SMALL_OVER) == []


def test_no_screen_track_is_empty_plan(tmp_path, capsys):
    sequence = make_sequence([], [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10)])
    rc, summary, captured = run_main(tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"], extra=["--json"])
    assert rc == 0
    assert summary["ok"] is True
    assert summary["spans"] == []
    assert summary["ranges"] == []
    assert summary["placements"] == 0


def test_explainer_flag_from_name():
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00 explainer.mp4", 2, 5)],
        [],
    )
    spans = fp.screen_spans(sequence, SMALL_OVER)
    assert spans[0]["explainer"] is True


def test_ranges_padding_clamped_and_merged():
    sequence = make_sequence(
        [
            screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5),
            screen_item(12, 4, "2026-09-01 10-00-00.mp4", 5, 8),
        ],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
    )
    spans = fp.screen_spans(sequence, SMALL_OVER)
    pieces = fp.camera_pieces(sequence, spans, 0.5)
    ranges = fp.merge_ranges(pieces, 2.0)
    assert len(ranges) == 1
    assert ranges[0]["start"] == int(round(1.5 * T))
    assert ranges[0]["end"] == int(round(8.5 * T))


def test_ranges_clamped_at_zero():
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 0, 2)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
    )
    spans = fp.screen_spans(sequence, SMALL_OVER)
    pieces = fp.camera_pieces(sequence, spans, 0.5)
    ranges = fp.merge_ranges(pieces, 2.0)
    assert ranges[0]["start"] == 0
    assert ranges[0]["end"] == int(round(2.5 * T))


def test_ranges_over_gap_do_not_merge():
    sequence = make_sequence(
        [
            screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5),
            screen_item(12, 4, "2026-09-01 10-00-00.mp4", 12, 15),
        ],
        [
            camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0),
            camera_item(10, 2, "camera 2026-09-01 10-00-00.mp4", 10, 20, source_in_s=10),
        ],
    )
    spans = fp.screen_spans(sequence, SMALL_OVER)
    pieces = fp.camera_pieces(sequence, spans, 0.5)
    ranges = fp.merge_ranges(pieces, 2.0)
    assert len(ranges) == 2


def test_matte_output_name():
    out = fp.matte_output("matte", "camera 2026-09-01 10-00-00.mp4", int(round(11.5 * T)), int(round(31.0 * T)))
    assert out == Path("matte") / "camera 2026-09-01 10-00-00_0011.50-0031.00.mov"


def test_matte_argv_has_no_ratio(tmp_path, capsys, monkeypatch):
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
    )
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json"], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    calls = fake.matte_calls()
    assert len(calls) == 1
    argv = calls[0]
    assert argv[1].endswith("matte.py")
    assert "--start" in argv and "--end" in argv and "--out" in argv
    assert "--ratio" not in argv
    assert argv[argv.index("--start") + 1] == "1.500"
    assert argv[argv.index("--end") + 1] == "5.500"


def test_existing_output_skipped_and_force_reruns(tmp_path, capsys, monkeypatch):
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
    )
    matte_dir = tmp_path / "matte"
    matte_dir.mkdir()
    out = fp.matte_output(matte_dir, "camera 2026-09-01 10-00-00.mp4", int(round(1.5 * T)), int(round(5.5 * T)))
    out.write_bytes(b"")

    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json", "--matte-dir", str(matte_dir)], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert fake.matte_calls() == []
    assert summary["ranges"][0]["matted"] is True

    fake_force = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json", "--matte-dir", str(matte_dir), "--force"],
        monkeypatch=monkeypatch, fake=fake_force,
    )
    assert rc == 0
    assert len(fake_force.matte_calls()) == 1


def test_placements_trimmed_to_span_and_tracks(tmp_path, capsys, monkeypatch):
    sequence = make_sequence(
        [
            screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5),
            screen_item(12, 4, "2026-09-01 10-00-00.mp4", 5, 8),
        ],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 6, source_in_s=0)],
    )
    fake = FakeRun(import_items=[48], inspect=make_sequence([], []))
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json", "--project", str(tmp_path / "proj.fcproj")], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    steps = [json.loads(line) for line in (tmp_path / fp.PLACE_FILE).read_text(encoding="utf-8").splitlines()]
    assert len(steps) == 1
    params = steps[0]["params"]
    assert params["track"] == "new"
    assert params["audio"] is False
    assert params["insert"] is False
    assert params["time"] == 2 * T
    assert params["duration"] == 4 * T
    assert params["sourceIn"] == int(round(round(0.5 * 25) / 25 * T))


def test_placements_second_uses_vn(tmp_path, capsys, monkeypatch):
    sequence = make_sequence(
        [
            screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5),
            screen_item(12, 4, "2026-09-01 10-00-00.mp4", 12, 15),
        ],
        [
            camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0),
            camera_item(10, 2, "camera 2026-09-01 10-00-00.mp4", 10, 20, source_in_s=10),
        ],
    )
    fake = FakeRun(import_items=[48, 49], inspect=make_sequence([], []))
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json", "--project", str(tmp_path / "proj.fcproj")], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    steps = [json.loads(line) for line in (tmp_path / fp.PLACE_FILE).read_text(encoding="utf-8").splitlines()]
    assert [step["params"]["track"] for step in steps] == ["new", "V4"]


def test_rerun_skips_already_placed(tmp_path, capsys, monkeypatch):
    matte_name = "camera 2026-09-01 10-00-00_0001.50-0005.50.mov"
    pip_track = {
        "id": 19,
        "name": "Video 4",
        "items": [
            {"clip": 50, "item": 48, "name": matte_name, "start": 2 * T, "end": 6 * T, "enabled": True},
        ],
    }
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
        extra_tracks=[pip_track],
    )
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json"], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert summary["placements"] == 0
    assert not (tmp_path / fp.PLACE_FILE).exists()
    assert (tmp_path / fp.ANIMATE_FILE).is_file()


def test_bridge_runs_place_then_animate(tmp_path, capsys, monkeypatch):
    matte_name = "camera 2026-09-01 10-00-00_0001.50-0005.50.mov"
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
    )
    fresh = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
        extra_tracks=[{
            "id": 19,
            "name": "Video 4",
            "items": [{"clip": 50, "item": 48, "name": matte_name, "start": 2 * T, "end": 6 * T, "enabled": True}],
        }],
    )
    fake = FakeRun(import_items=[48])
    monkeypatch.setattr(fp.subprocess, "run", fake)
    runs = []
    monkeypatch.setattr(fp, "run_steps", lambda path, bridge, project: runs.append((Path(path).name, bridge)))
    monkeypatch.setattr(fp, "inspect_sequence", lambda bridge, project: fresh)
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json", "--bridge", "127.0.0.1:7714"], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert runs == [(fp.PLACE_FILE, "127.0.0.1:7714"), (fp.ANIMATE_FILE, "127.0.0.1:7714")]
    assert summary["steps_files"] == [fp.PLACE_FILE, fp.ANIMATE_FILE]
    assert (tmp_path / fp.ANIMATE_FILE).read_text(encoding="utf-8") != ""


def test_no_target_no_import_and_no_place_file(tmp_path, capsys, monkeypatch):
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
    )
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json"], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert all("import" not in call for call in fake.calls)
    assert not (tmp_path / fp.PLACE_FILE).exists()
    assert summary["steps_files"] == []
    assert "place steps need --bridge or --project" in summary["warnings"]


def test_project_runs_place_then_animate(tmp_path, capsys, monkeypatch):
    matte_name = "camera 2026-09-01 10-00-00_0001.50-0005.50.mov"
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
    )
    fresh = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
        extra_tracks=[{
            "id": 19,
            "name": "Video 4",
            "items": [{"clip": 50, "item": 48, "name": matte_name, "start": 2 * T, "end": 6 * T, "enabled": True}],
        }],
    )
    project = tmp_path / "proj.fcproj"
    fake = FakeRun(import_items=[48], inspect=fresh)
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json", "--project", str(project)], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    import_calls = [call for call in fake.calls if "import" in call]
    assert len(import_calls) == 1
    assert import_calls[0][import_calls[0].index("--project") + 1] == str(project)
    assert "--save" in import_calls[0]
    run_calls = [call for call in fake.calls if "run" in call]
    assert len(run_calls) == 2
    for call in run_calls:
        assert call[call.index("--project") + 1] == str(project)
        assert "--save" in call
    inspect_calls = [call for call in fake.calls if "inspect" in call]
    assert len(inspect_calls) == 1
    assert inspect_calls[0][inspect_calls[0].index("--project") + 1] == str(project)
    assert summary["steps_files"] == [fp.PLACE_FILE, fp.ANIMATE_FILE]
    assert (tmp_path / fp.ANIMATE_FILE).read_text(encoding="utf-8") != ""


def test_second_run_with_everything_placed_does_not_import(tmp_path, capsys, monkeypatch):
    matte_name = "camera 2026-09-01 10-00-00_0001.50-0005.50.mov"
    pip_track = {
        "id": 19,
        "name": "Video 4",
        "items": [
            {
                "clip": 50, "item": 48, "name": matte_name, "start": 2 * T, "end": 6 * T,
                "enabled": True, "effects": motion_effects(),
            },
        ],
    }
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
        extra_tracks=[pip_track],
    )
    matte_dir = tmp_path / "matte"
    matte_dir.mkdir()
    (matte_dir / matte_name).write_bytes(b"")
    fake = FakeRun(import_items=[48], inspect=sequence)
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json", "--bridge", "127.0.0.1:7714", "--matte-dir", str(matte_dir)],
        monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert all("import" not in call for call in fake.calls)
    assert summary["placements"] == 0
    assert summary["nothing_to_do"] is True
    assert not (tmp_path / fp.PLACE_FILE).exists()


def test_image_on_screen_track_makes_no_span(tmp_path, capsys, monkeypatch):
    sequence = make_sequence(
        [
            screen_item(11, 3, "fundal_intro.png", 0, 10),
            screen_item(12, 4, "2026-09-01 10-00-00.mp4", 10, 20),
        ],
        [
            camera_item(9, 1, "camera 2026-09-01 09-00-00.mp4", 0, 10),
            camera_item(10, 2, "camera 2026-09-01 10-00-00.mp4", 10, 20),
        ],
    )
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence,
        ["camera 2026-09-01 09-00-00.mp4", "camera 2026-09-01 10-00-00.mp4"],
        extra=["--json"], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert summary["spans"] == [{"start_s": 10.0, "end_s": 20.0, "explainer": False}]
    assert len(summary["ranges"]) == 1
    assert summary["ranges"][0]["source"] == "camera 2026-09-01 10-00-00.mp4"
    matte_calls = fake.matte_calls()
    assert len(matte_calls) == 1
    assert "09-00-00" not in " ".join(str(part) for part in matte_calls[0])


def test_edge_piece_leftover_is_dropped(tmp_path, capsys, monkeypatch):
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 10, 20)],
        [camera_item(9, 1, "camera 2026-09-01 09-00-00.mp4", 6, 10.04)],
    )
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 09-00-00.mp4"],
        extra=["--json"], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert summary["ranges"] == []
    assert summary["placements"] == 0
    assert len(summary["edge_checks"]) == 1
    assert summary["edge_checks"][0]["verdict"] == "leftover"
    assert summary["edge_checks"][0]["overlap_s"] == 0.04
    assert fake.matte_calls() == []


def test_edge_piece_seam_is_kept(tmp_path, capsys, monkeypatch):
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 10, 20)],
        [camera_item(9, 1, "camera 2026-09-01 09-00-00.mp4", 10, 10.8)],
    )
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 09-00-00.mp4"],
        extra=["--json"], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert len(summary["edge_checks"]) == 1
    assert summary["edge_checks"][0]["verdict"] == "seam"
    assert len(summary["ranges"]) == 1
    assert summary["placements"] == 1
    assert len(fake.matte_calls()) == 1


def test_rerun_nothing_to_do(tmp_path, capsys, monkeypatch):
    matte_name = "camera 2026-09-01 10-00-00_0001.50-0005.50.mov"
    pip_track = {
        "id": 19,
        "name": "Video 4",
        "items": [
            {
                "clip": 50, "item": 48, "name": matte_name, "start": 2 * T, "end": 6 * T,
                "enabled": True, "effects": motion_effects(),
            },
        ],
    }
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
        extra_tracks=[pip_track],
    )
    matte_dir = tmp_path / "matte"
    matte_dir.mkdir()
    (matte_dir / matte_name).write_bytes(b"")
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--dry-run", "--matte-dir", str(matte_dir)], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert "nothing to do" in captured.out
    assert "placed=yes" in captured.out
    assert "keyframed=yes" in captured.out
    assert fake.calls == []

    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--dry-run", "--json", "--matte-dir", str(matte_dir)], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert summary["nothing_to_do"] is True
    assert summary["placements"] == 0
    assert summary["ranges"][0]["placed"] is True
    assert summary["ranges"][0]["keyframed"] is True


def test_rerun_rewrites_animate_when_keyframes_missing(tmp_path, capsys, monkeypatch):
    matte_name = "camera 2026-09-01 10-00-00_0001.50-0005.50.mov"
    pip_track = {
        "id": 19,
        "name": "Video 4",
        "items": [
            {"clip": 50, "item": 48, "name": matte_name, "start": 2 * T, "end": 6 * T, "enabled": True},
        ],
    }
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
        extra_tracks=[pip_track],
    )
    matte_dir = tmp_path / "matte"
    matte_dir.mkdir()
    (matte_dir / matte_name).write_bytes(b"")
    fake = FakeRun(import_items=[48], inspect=sequence)
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json", "--bridge", "127.0.0.1:7714", "--matte-dir", str(matte_dir)],
        monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert summary["nothing_to_do"] is False
    assert summary["ranges"][0]["keyframed"] is False
    assert (tmp_path / fp.ANIMATE_FILE).is_file()
    assert (tmp_path / fp.ANIMATE_FILE).read_text(encoding="utf-8") != ""
    assert not (tmp_path / fp.PLACE_FILE).exists()
    assert all("import" not in call for call in fake.calls)


def test_rerun_rewrites_animate_when_effects_lack_keyframes(tmp_path, capsys, monkeypatch):
    matte_name = "camera 2026-09-01 10-00-00_0001.50-0005.50.mov"
    failed_effects = [{
        "effect": "motion",
        "params": {"scale": {"keyframes": []}, "position": {"keyframes": []}},
    }]
    pip_track = {
        "id": 19,
        "name": "Video 4",
        "items": [
            {
                "clip": 50, "item": 48, "name": matte_name, "start": 2 * T, "end": 6 * T,
                "enabled": True, "effects": failed_effects,
            },
        ],
    }
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
        extra_tracks=[pip_track],
    )
    matte_dir = tmp_path / "matte"
    matte_dir.mkdir()
    (matte_dir / matte_name).write_bytes(b"")
    fake = FakeRun(import_items=[48], inspect=sequence)
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json", "--bridge", "127.0.0.1:7714", "--matte-dir", str(matte_dir)],
        monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert summary["nothing_to_do"] is False
    assert summary["ranges"][0]["keyframed"] is False
    assert (tmp_path / fp.ANIMATE_FILE).read_text(encoding="utf-8") != ""


def test_animate_failure_deletes_steps_file(tmp_path, capsys, monkeypatch):
    matte_name = "camera 2026-09-01 10-00-00_0001.50-0005.50.mov"
    pip_track = {
        "id": 19,
        "name": "Video 4",
        "items": [
            {"clip": 50, "item": 48, "name": matte_name, "start": 2 * T, "end": 6 * T, "enabled": True},
        ],
    }
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
        extra_tracks=[pip_track],
    )
    matte_dir = tmp_path / "matte"
    matte_dir.mkdir()
    (matte_dir / matte_name).write_bytes(b"")
    fake = FakeRun(import_items=[48], inspect=sequence)

    def failing_run_steps(path, bridge, project):
        if Path(path).name == fp.ANIMATE_FILE:
            raise fp.FinishPipRuntimeError("animate failed")

    monkeypatch.setattr(fp, "run_steps", failing_run_steps)
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json", "--bridge", "127.0.0.1:7714", "--matte-dir", str(matte_dir)],
        monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 1
    assert not (tmp_path / fp.ANIMATE_FILE).exists()


def test_rerun_nothing_to_do_from_animate_steps_file(tmp_path, capsys, monkeypatch):
    matte_name = "camera 2026-09-01 10-00-00_0001.50-0005.50.mov"
    pip_track = {
        "id": 19,
        "name": "Video 4",
        "items": [
            {"clip": 50, "item": 48, "name": matte_name, "start": 2 * T, "end": 6 * T, "enabled": True},
        ],
    }
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
        extra_tracks=[pip_track],
    )
    matte_dir = tmp_path / "matte"
    matte_dir.mkdir()
    (matte_dir / matte_name).write_bytes(b"")
    animate = tmp_path / fp.ANIMATE_FILE
    sentinel = json.dumps({
        "id": "effects.setParam",
        "params": {"clip": 50, "effect": "motion", "param": "scale", "value": 45, "time": 0, "keyframe": True},
    }) + "\n"
    animate.write_text(sentinel, encoding="utf-8")
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json", "--matte-dir", str(matte_dir)], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert summary["nothing_to_do"] is True
    assert summary["ranges"][0]["keyframed"] is True
    assert summary["steps_files"] == []
    assert fake.calls == []
    assert animate.read_text(encoding="utf-8") == sentinel


def test_still_on_camera_track_is_not_a_piece(tmp_path, capsys, monkeypatch):
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 0, 10)],
        [
            camera_item(9, 1, "fundal_intro.png", 0, 10),
            camera_item(10, 2, "camera 2026-09-01 10-00-00.mp4", 0, 10),
        ],
    )
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json"], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert len(summary["ranges"]) == 1
    assert summary["ranges"][0]["source"] == "camera 2026-09-01 10-00-00.mp4"
    assert summary["placements"] == 1


def test_camera_track_with_only_a_still_makes_no_ranges(tmp_path, capsys, monkeypatch):
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 0, 10)],
        [camera_item(9, 1, "fundal_intro.png", 0, 10)],
    )
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, [], extra=["--json"], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert summary["ranges"] == []
    assert summary["placements"] == 0


def test_attach_clips_skips_missing_clip_id(tmp_path, capsys, monkeypatch):
    matte_name = "camera 2026-09-01 10-00-00_0001.50-0005.50.mov"
    pip_track = {
        "id": 19,
        "name": "Video 4",
        "items": [
            {"item": 48, "name": matte_name, "start": 2 * T, "end": 6 * T, "enabled": True},
        ],
    }
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
        extra_tracks=[pip_track],
    )
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json"], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert any("no clip id" in warning for warning in summary["warnings"])


def test_bridge_and_project_are_mutually_exclusive(tmp_path, capsys):
    sequence = make_sequence([], [])
    seq_path = write_sequence(tmp_path, sequence)
    camera_dir = make_camera_dir(tmp_path, [])
    rc = fp.main([
        "--sequence", str(seq_path), "--camera-dir", str(camera_dir),
        "--bridge", "127.0.0.1:7714", "--project", str(tmp_path / "proj.fcproj"), "--json",
    ])
    captured = capsys.readouterr()
    assert rc == 2
    summary = json.loads(captured.out.strip())
    assert summary["ok"] is False
    assert "mutually exclusive" in summary["error"]


def test_pose_scale_and_position():
    settings = {"width": 1920, "height": 1080}
    pose = fp.final_pose(settings, 45)
    assert pose["scale"] == 45
    assert pose["position"] == [1434.0, 783.0]
    small = fp.final_pose(settings, 36)
    assert small["position"] == [1920 - 54 - 1920 * 0.36 / 2, 1080 - 54 - 1080 * 0.36 / 2]


def test_animate_keyframes_times_and_values(tmp_path, capsys, monkeypatch):
    matte_name = "camera 2026-09-01 10-00-00_0001.50-0005.50.mov"
    pip_track = {
        "id": 19,
        "name": "Video 4",
        "items": [
            {"clip": 50, "item": 48, "name": matte_name, "start": 2 * T, "end": 6 * T, "enabled": True},
        ],
    }
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
        extra_tracks=[pip_track],
    )
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json"], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    steps = [json.loads(line) for line in (tmp_path / fp.ANIMATE_FILE).read_text(encoding="utf-8").splitlines()]
    scale_steps = [step for step in steps if step["params"]["param"] == "scale" and step["id"] == "effects.setParam"]
    times = [step["params"]["time"] for step in scale_steps]
    assert times == [2 * T, 2 * T + int(round(0.18 * T)), 2 * T + int(round(0.30 * T)), 5 * T - int(round(0.25 * T)), 5 * T - int(round(0.10 * T)), 5 * T]
    values = [step["params"]["value"] for step in scale_steps]
    assert values[0] == pytest.approx(45 * 1.15)
    assert values[1] == pytest.approx(45 * 0.96)
    assert values[2] == 45
    assert values[3] == 45
    assert values[4] == pytest.approx(45 * 1.03)
    assert values[5] == pytest.approx(45 * 0.5)
    interp = [step for step in steps if step["id"] == "effects.setInterpolation"]
    assert [step["params"]["interpolation"] for step in interp] == ["easeOut", "easeOut", "easeIn", "easeIn"]
    snapped = int(round(round(0.5 * 25) / 25 * T))
    assert interp[0]["params"]["mediaTime"] == snapped
    assert interp[2]["params"]["mediaTime"] == 3 * T + snapped


def test_animate_media_time_is_source_relative(tmp_path, capsys, monkeypatch):
    matte_name = "camera 2026-09-01 10-00-00_0001.50-0005.50.mov"
    pip_track = {
        "id": 19,
        "name": "Video 4",
        "items": [
            {"clip": 50, "item": 48, "name": matte_name, "start": 2 * T, "end": 6 * T, "enabled": True},
        ],
    }
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
        extra_tracks=[pip_track],
    )
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json"], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    steps = [json.loads(line) for line in (tmp_path / fp.ANIMATE_FILE).read_text(encoding="utf-8").splitlines()]
    interp = [step for step in steps if step["id"] == "effects.setInterpolation"]
    assert len(interp) == 4
    placement = {
        "clip_start": 2 * T,
        "source_in": int(round(round(0.5 * 25) / 25 * T)),
    }
    assert placement["source_in"] != 0
    assert interp[0]["params"]["mediaTime"] == fp.media_time(placement, 2 * T)
    assert interp[1]["params"]["mediaTime"] == fp.media_time(placement, 2 * T)
    assert interp[2]["params"]["mediaTime"] == fp.media_time(placement, 5 * T)
    assert interp[3]["params"]["mediaTime"] == fp.media_time(placement, 5 * T)
    assert interp[0]["params"]["mediaTime"] == 2 * T - 2 * T + placement["source_in"]
    assert interp[2]["params"]["mediaTime"] == 5 * T - 2 * T + placement["source_in"]


def test_explainer_uses_small_scale(tmp_path, capsys, monkeypatch):
    matte_name = "camera 2026-09-01 10-00-00_0001.50-0005.50.mov"
    pip_track = {
        "id": 19,
        "name": "Video 4",
        "items": [
            {"clip": 50, "item": 48, "name": matte_name, "start": 2 * T, "end": 6 * T, "enabled": True},
        ],
    }
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00 explainer.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
        extra_tracks=[pip_track],
    )
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json"], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    steps = [json.loads(line) for line in (tmp_path / fp.ANIMATE_FILE).read_text(encoding="utf-8").splitlines()]
    final = [
        step for step in steps
        if step["id"] == "effects.setParam"
        and step["params"]["param"] == "scale"
        and step["params"]["time"] == 2 * T + int(round(0.30 * T))
    ]
    assert final[0]["params"]["value"] == 36


def test_dry_run_writes_nothing(tmp_path, capsys, monkeypatch):
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
    )
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--dry-run"], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert fake.calls == []
    assert not (tmp_path / fp.PLACE_FILE).exists()
    assert not (tmp_path / fp.ANIMATE_FILE).exists()
    assert not (tmp_path / "matte").exists()
    assert "spans:" in captured.out


def test_json_summary_fields(tmp_path, capsys, monkeypatch):
    sequence = make_sequence(
        [screen_item(11, 3, "2026-09-01 10-00-00.mp4", 2, 5)],
        [camera_item(9, 1, "camera 2026-09-01 10-00-00.mp4", 0, 10, source_in_s=0)],
    )
    fake = FakeRun(import_items=[48])
    rc, summary, captured = run_main(
        tmp_path, capsys, sequence, ["camera 2026-09-01 10-00-00.mp4"],
        extra=["--json"], monkeypatch=monkeypatch, fake=fake,
    )
    assert rc == 0
    assert summary["ok"] is True
    assert summary["spans"] == [{"start_s": 2.0, "end_s": 5.0, "explainer": False}]
    assert summary["ranges"][0]["source"] == "camera 2026-09-01 10-00-00.mp4"
    assert summary["ranges"][0]["start_s"] == 1.5
    assert summary["ranges"][0]["end_s"] == 5.5
    assert summary["ranges"][0]["matted"] is True
    assert summary["placements"] == 1
    assert summary["steps_files"] == []
    assert summary["warnings"] == ["place steps need --bridge or --project"]
    assert summary["llm_cost_usd"] == 0.0
    assert summary["wall_time_s"] >= 0.0


def test_missing_sequence_is_exit_2(tmp_path, capsys):
    camera_dir = make_camera_dir(tmp_path, [])
    rc = fp.main(["--sequence", str(tmp_path / "nope.json"), "--camera-dir", str(camera_dir), "--json"])
    captured = capsys.readouterr()
    assert rc == 2
    summary = json.loads(captured.out.strip())
    assert summary["ok"] is False
    assert "not found" in summary["error"]


def test_missing_sequence_argument_is_exit_2(tmp_path, capsys):
    camera_dir = make_camera_dir(tmp_path, [])
    rc = fp.main(["--camera-dir", str(camera_dir), "--json"])
    captured = capsys.readouterr()
    assert rc == 2
    summary = json.loads(captured.out.strip())
    assert summary["ok"] is False
    assert "--sequence" in summary["error"]


def test_unparseable_sequence_is_exit_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    camera_dir = make_camera_dir(tmp_path, [])
    rc = fp.main(["--sequence", str(bad), "--camera-dir", str(camera_dir), "--json"])
    captured = capsys.readouterr()
    assert rc == 2
    assert json.loads(captured.out.strip())["ok"] is False


def test_missing_camera_dir_is_exit_2(tmp_path, capsys):
    sequence = make_sequence([], [])
    seq_path = write_sequence(tmp_path, sequence)
    rc = fp.main(["--sequence", str(seq_path), "--camera-dir", str(tmp_path / "nope"), "--json"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "not a directory" in json.loads(captured.out.strip())["error"]


def test_refuses_output_inside_camera_dir(tmp_path, capsys):
    sequence = make_sequence([], [])
    seq_path = write_sequence(tmp_path, sequence)
    camera_dir = make_camera_dir(tmp_path, [])
    rc = fp.main([
        "--sequence", str(seq_path), "--camera-dir", str(camera_dir),
        "--matte-dir", str(camera_dir / "matte"), "--json",
    ])
    captured = capsys.readouterr()
    assert rc == 2
    assert "inside the camera folder" in json.loads(captured.out.strip())["error"]


needs_filmcraft = pytest.mark.skipif(cli_path() is None, reason="filmcraft-cli is not installed")


def make_tiny_matte(path):
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "color=c=red:s=64x64:d=2.0:r=25",
            "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
            str(path),
        ],
        check=True,
    )


@needs_filmcraft
def test_smoke_keyframes_land_on_the_clip(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed")
    matte = tmp_path / "tiny_matte.mov"
    make_tiny_matte(matte)
    project = tmp_path / "proj.fcproj"
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    subprocess.run(
        [cli_path(), "--demo", "--save-as", str(project), "run", str(empty)],
        check=True, capture_output=True,
    )
    imported = tmp_path / "imported.fcproj"
    result = subprocess.run(
        [cli_path(), "--project", str(project), "--save-as", str(imported), "--compact", "import", str(matte)],
        capture_output=True, text=True, check=True,
    )
    item = json.loads(result.stdout.strip().splitlines()[-1])["items"][0]

    source_in = int(round(0.5 * T))
    place = tmp_path / "place.jsonl"
    place.write_text(
        json.dumps({"id": "timeline.place", "params": {
            "item": item, "track": "new", "time": 3 * T, "insert": False,
            "audio": False, "sourceIn": source_in, "duration": 1 * T,
        }}) + "\n",
        encoding="utf-8",
    )
    placed = tmp_path / "placed.fcproj"
    subprocess.run(
        [cli_path(), "--project", str(imported), "--save-as", str(placed), "run", str(place)],
        check=True, capture_output=True,
    )
    inspected = subprocess.run(
        [cli_path(), "--project", str(placed), "inspect", "sequence"],
        capture_output=True, text=True, check=True,
    )
    sequence = json.loads(inspected.stdout)

    span = {"start": 3 * T, "end": 4 * T, "explainer": False, "names": [matte.name]}
    placement = {
        "span": span,
        "source": matte.name,
        "matte_out": matte,
        "timeline_start": 3 * T,
        "timeline_end": 4 * T,
        "duration": 1 * T,
        "source_in": source_in,
    }
    fp.attach_clips(sequence, [placement])
    assert "clip" in placement
    assert placement["source_in"] != 0
    options = {"settings": {"width": 1920, "height": 1080}, "scale": 45.0, "scale_small": 36.0}
    steps = fp.build_animate_steps([span], [placement], options)
    animate = tmp_path / "animate.jsonl"
    animate.write_text("\n".join(json.dumps(step) for step in steps) + "\n", encoding="utf-8")
    animated = tmp_path / "animated.fcproj"
    subprocess.run(
        [cli_path(), "--project", str(placed), "--save-as", str(animated), "run", str(animate)],
        check=True, capture_output=True,
    )

    project_data = json.loads(animated.read_text(encoding="utf-8"))
    clip = None
    for value in project_data["project"]["items"].values():
        if "Sequence" in value.get("kind", {}):
            for track in value["kind"]["Sequence"]["video_tracks"]:
                for candidate in track["items"]:
                    if candidate["id"] == placement["clip"]:
                        clip = candidate
    assert clip is not None
    motion = next(effect for effect in clip["effects"] if effect["effect"] == "motion")
    scale = motion["params"]["scale"]["keyframes"]
    frame = T / 25
    assert len(scale) == 6
    assert scale[0]["value"]["Float"] == pytest.approx(45 * 1.15)
    assert scale[1]["value"]["Float"] == pytest.approx(45 * 0.96)
    assert scale[2]["value"]["Float"] == pytest.approx(45)
    assert scale[3]["value"]["Float"] == pytest.approx(45)
    assert scale[4]["value"]["Float"] == pytest.approx(45 * 1.03)
    assert scale[5]["value"]["Float"] == pytest.approx(45 * 0.5)
    assert scale[0]["time"] == pytest.approx(fp.media_time(placement, 3 * T), abs=frame)
    assert scale[1]["time"] == pytest.approx(fp.media_time(placement, 3 * T + int(round(0.18 * T))), abs=frame)
    assert scale[2]["time"] == pytest.approx(fp.media_time(placement, 3 * T + int(round(0.30 * T))), abs=frame)
    assert scale[3]["time"] == pytest.approx(fp.media_time(placement, 4 * T - int(round(0.25 * T))), abs=frame)
    assert scale[4]["time"] == pytest.approx(fp.media_time(placement, 4 * T - int(round(0.10 * T))), abs=frame)
    assert scale[5]["time"] == pytest.approx(fp.media_time(placement, 4 * T), abs=frame)
    assert scale[0]["interp"] == "EaseOut"
    assert scale[5]["interp"] == "EaseIn"
