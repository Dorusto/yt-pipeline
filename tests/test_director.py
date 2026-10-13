import json
import subprocess
import sys
from pathlib import Path

from common.schema import validate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "director.py"
FIXTURES = Path(__file__).parent / "fixtures"

TARGET = 24.0


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def segment(sid, start, end, role, confidence=0.8, defects=None, motion_label=None):
    record = {
        "id": sid,
        "start_s": start,
        "end_s": end,
        "role": role,
        "confidence": confidence,
        "defects": defects or [],
    }
    if motion_label is not None:
        record["motion_label"] = motion_label
    return record


def quality_doc(file, duration, segments):
    return {"file": file, "duration_s": duration, "segments": segments}


def move(mid, start, end, label, speed, steadiness, steady_start, steady_end, handle_in, handle_out, confidence=0.9):
    return {
        "id": mid,
        "start_s": start,
        "end_s": end,
        "label": label,
        "speed": speed,
        "steadiness": steadiness,
        "steady_start_s": steady_start,
        "steady_end_s": steady_end,
        "handle_in_s": handle_in,
        "handle_out_s": handle_out,
        "confidence": confidence,
    }


def motion_doc(file, duration, moves):
    return {
        "file": file,
        "duration_s": duration,
        "analysis": {"method": "test", "source": "optical_flow", "sample_fps": 10},
        "trajectory": [],
        "phases": [],
        "moves": moves,
        "scene_activity": [],
        "events": [],
    }


def build_workspace(root):
    clips = [
        {"file": "clipA.mp4", "path": "/media/clipA.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:00:00"},
        {"file": "clipB.mp4", "path": "/media/clipB.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:01:00"},
        {"file": "clipC.mp4", "path": "/media/clipC.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:02:00"},
        {"file": "clipD.mp4", "path": "/media/clipD.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:03:00"},
    ]
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    write_json(
        root / "quality" / "clipA.json",
        quality_doc(
            "clipA.mp4",
            60.0,
            [
                segment("s1", 0.0, 5.0, "setup"),
                segment("s2", 5.0, 55.0, "hold", motion_label="static"),
                segment("s3", 55.0, 60.0, "accidental"),
            ],
        ),
    )
    write_json(
        root / "quality" / "clipB.json",
        quality_doc("clipB.mp4", 60.0, [segment("s1", 0.0, 60.0, "move", motion_label="pan_right")]),
    )
    write_json(
        root / "quality" / "clipC.json",
        quality_doc(
            "clipC.mp4",
            60.0,
            [segment("s1", 0.0, 55.0, "hold", motion_label="static"), segment("s2", 55.0, 60.0, "teardown")],
        ),
    )
    write_json(
        root / "quality" / "clipD.json",
        quality_doc("clipD.mp4", 60.0, [segment("s1", 0.0, 60.0, "hold", motion_label="static")]),
    )
    write_json(
        root / "motion" / "clipB.json",
        motion_doc("clipB.mp4", 60.0, [move("m1", 0.0, 60.0, "pan_right", "regular", "no_shaking", 2.0, 58.0, 1.0, 59.0)]),
    )
    return root


def run_director(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
    )


def build_wallclock_gap_workspace(root):
    times = [
        "2026-01-01T10:00:00",
        "2026-01-01T10:01:00",
        "2026-01-01T10:02:00",
        "2026-01-01T10:03:00",
        "2026-01-01T14:00:00",
        "2026-01-01T14:01:00",
        "2026-01-01T14:02:00",
        "2026-01-01T14:03:00",
    ]
    clips = [
        {"file": f"clip{index}.mp4", "path": f"/media/clip{index}.mp4", "duration_s": 60.0, "creation_time": moment}
        for index, moment in enumerate(times)
    ]
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    for index in range(len(times)):
        write_json(
            root / "quality" / f"clip{index}.json",
            quality_doc(f"clip{index}.mp4", 60.0, [segment("s1", 5.0, 55.0, "hold", motion_label="static")]),
        )
    return root


def build_fill_workspace(root):
    clips = [
        {"file": "clipA.mp4", "path": "/media/clipA.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:00:00"},
        {"file": "clipB.mp4", "path": "/media/clipB.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:01:00"},
        {"file": "clipC.mp4", "path": "/media/clipC.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:02:00"},
    ]
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    holds = [segment(f"s{index}", index * 6.0, index * 6.0 + 5.0, "hold", motion_label="static") for index in range(10)]
    write_json(root / "quality" / "clipA.json", quality_doc("clipA.mp4", 60.0, holds))
    write_json(root / "quality" / "clipB.json", quality_doc("clipB.mp4", 60.0, [segment("s1", 0.0, 60.0, "setup")]))
    write_json(root / "quality" / "clipC.json", quality_doc("clipC.mp4", 60.0, holds))
    return root


def write_edit(tmp_path, *extra):
    workspace = build_workspace(tmp_path / "ws")
    out = tmp_path / "edit.json"
    result = run_director(
        "--workspace", workspace, "--out", out, "--target-duration-s", TARGET, "--json", *extra
    )
    assert result.returncode == 0, result.stderr
    return workspace, out, json.loads(result.stdout), json.loads(out.read_text(encoding="utf-8"))


def shotlog_shot(key, clip, segment_id, start, end, eligible, score, description, reason, similar_to=None, hero=None):
    return {
        "key": key,
        "clip": clip,
        "segment_id": segment_id,
        "start_s": start,
        "end_s": end,
        "eligible": eligible,
        "role": "hold",
        "role_source": "geometry",
        "motion_label": "static",
        "label_source": "geometry",
        "score": score,
        "scores": {"labels": score},
        "description": description,
        "hook": description or f"{segment_id}",
        "reason": reason,
        "similar_to": similar_to or [],
        "hero": hero,
    }


def write_shotlog(root, shots):
    write_json(
        root / "shotlog.json",
        {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "weights": {"vision": 0.55, "labels": 0.45},
            "shots": shots,
        },
    )


def build_shotlog_workspace(root):
    clips = [
        {"file": "clip_0001_x.mp4", "path": "/media/clip_0001_x.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:00:00"},
        {"file": "clip_0002_x.mp4", "path": "/media/clip_0002_x.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:01:00"},
    ]
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    for clip in clips:
        stem = Path(clip["file"]).stem
        write_json(
            root / "quality" / f"{stem}.json",
            quality_doc(
                clip["file"],
                60.0,
                [
                    segment("s1", 0.0, 30.0, "hold", motion_label="static"),
                    segment("s2", 30.0, 60.0, "hold", motion_label="static"),
                ],
            ),
        )
        write_json(root / "motion" / f"{stem}.json", motion_doc(clip["file"], 60.0, []))
    write_shotlog(
        root,
        [
            shotlog_shot("0001/s1", "clip_0001_x.mp4", "s1", 0.0, 30.0, True, 0.42, "a cave", "interest 42 · static by geometry · hold · clean"),
            shotlog_shot("0001/s2", "clip_0001_x.mp4", "s2", 30.0, 60.0, False, 0.0, None, "no description · static by geometry · hold · clean · not eligible"),
            shotlog_shot("0002/s1", "clip_0002_x.mp4", "s1", 0.0, 30.0, True, 0.77, "a tunnel", "interest 77 · hero · travel_forward by geometry · move · clean", hero=True),
            shotlog_shot("0002/s2", "clip_0002_x.mp4", "s2", 30.0, 60.0, True, 0.33, None, "interest 33 · static by geometry · hold · clean"),
        ],
    )
    return root


def build_similarity_workspace(root):
    clips = [
        {"file": "clip_0001_x.mp4", "path": "/media/clip_0001_x.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:00:00"},
        {"file": "clip_0002_x.mp4", "path": "/media/clip_0002_x.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:01:00"},
        {"file": "clip_0003_x.mp4", "path": "/media/clip_0003_x.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:02:00"},
    ]
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    for clip in clips:
        stem = Path(clip["file"]).stem
        write_json(
            root / "quality" / f"{stem}.json",
            quality_doc(
                clip["file"],
                60.0,
                [
                    segment("s1", 0.0, 20.0, "hold", motion_label="static"),
                    segment("s2", 20.0, 40.0, "hold", motion_label="static"),
                ],
            ),
        )
        write_json(root / "motion" / f"{stem}.json", motion_doc(clip["file"], 60.0, []))
    write_shotlog(
        root,
        [
            shotlog_shot("0001/s1", "clip_0001_x.mp4", "s1", 0.0, 20.0, True, 0.90, "cave", "interest 90 · static by geometry · hold · clean", similar_to=["0003/s1"]),
            shotlog_shot("0001/s2", "clip_0001_x.mp4", "s2", 20.0, 40.0, True, 0.40, "deck", "interest 40 · static by geometry · hold · clean"),
            shotlog_shot("0002/s1", "clip_0002_x.mp4", "s1", 0.0, 20.0, True, 0.80, "harbour", "interest 80 · static by geometry · hold · clean"),
            shotlog_shot("0002/s2", "clip_0002_x.mp4", "s2", 20.0, 40.0, True, 0.70, "boat", "interest 70 · static by geometry · hold · clean"),
            shotlog_shot("0003/s1", "clip_0003_x.mp4", "s1", 0.0, 20.0, True, 0.85, "tunnel", "interest 85 · static by geometry · hold · clean", similar_to=["0001/s1"]),
            shotlog_shot("0003/s2", "clip_0003_x.mp4", "s2", 20.0, 40.0, True, 0.30, "road", "interest 30 · static by geometry · hold · clean"),
        ],
    )
    return root


def shot_keys(record, workspace):
    shots = json.loads((workspace / "shotlog.json").read_text(encoding="utf-8"))["shots"]
    keys = []
    for slot in record["video"]:
        match = None
        for shot in shots:
            if shot["clip"] == slot["clip"] and shot["start_s"] - 0.01 <= slot["in_s"] and slot["out_s"] <= shot["end_s"] + 0.01:
                match = shot["key"]
                break
        keys.append(match)
    return keys


def test_skip_roles_never_become_slots(tmp_path):
    _, _, summary, record = write_edit(tmp_path)
    roles = {slot["role"] for slot in record["video"]}
    assert roles.isdisjoint({"accidental", "setup", "teardown"})
    assert any("skipped 3 accidental/setup/teardown segments" in warning for warning in summary["warnings"])


def test_move_slot_uses_handle_window(tmp_path):
    _, _, _, record = write_edit(tmp_path)
    move_slots = [slot for slot in record["video"] if slot["role"] == "move"]
    assert move_slots
    assert move_slots[0]["clip"] == "clipB.mp4"
    assert move_slots[0]["in_s"] == 1.0
    assert move_slots[0]["out_s"] <= 59.0


def test_chronological_order(tmp_path):
    _, _, _, record = write_edit(tmp_path)
    assert [slot["clip"] for slot in record["video"]] == ["clipA.mp4", "clipB.mp4", "clipC.mp4", "clipD.mp4"]


def test_calm_to_energy_last_slot_shorter_than_first(tmp_path):
    _, _, _, record = write_edit(tmp_path)
    lengths = [round(slot["out_s"] - slot["in_s"], 2) for slot in record["video"]]
    assert lengths[-1] < lengths[0]


def test_edit_validates_and_fixture_roundtrips(tmp_path):
    _, _, _, record = write_edit(tmp_path)
    assert validate(record, "edit") == []
    fixture = json.loads((FIXTURES / "edit_valid.json").read_text(encoding="utf-8"))
    assert validate(fixture, "edit") == []


def test_json_summary_fields(tmp_path):
    _, _, summary, _ = write_edit(tmp_path)
    assert summary["status"] == "written"
    assert summary["wall_time_s"] >= 0
    assert summary["llm_cost_usd"] == 0.0


def test_rerun_without_force_skips_and_keeps_mtime(tmp_path):
    workspace, out, _, _ = write_edit(tmp_path)
    mtime = out.stat().st_mtime
    result = run_director("--workspace", workspace, "--out", out, "--target-duration-s", TARGET, "--json")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "skipped"
    assert out.stat().st_mtime == mtime


def test_total_duration_within_tolerance(tmp_path):
    workspace = build_fill_workspace(tmp_path / "ws")
    out = tmp_path / "edit.json"
    result = run_director("--workspace", workspace, "--out", out, "--target-duration-s", TARGET, "--json")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert abs(summary["total_duration_s"] - TARGET) <= 0.2 * TARGET


def test_force_rewrites(tmp_path):
    workspace, out, _, _ = write_edit(tmp_path)
    mtime = out.stat().st_mtime
    result = run_director(
        "--workspace", workspace, "--out", out, "--target-duration-s", TARGET, "--force", "--json"
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "written"
    assert out.stat().st_mtime >= mtime


def test_missing_workspace_is_an_error(tmp_path):
    result = run_director("--workspace", tmp_path / "nope", "--json")
    assert result.returncode == 1
    assert "[error]" in result.stderr


def test_content_buckets_ignore_wallclock_gaps(tmp_path):
    workspace = build_wallclock_gap_workspace(tmp_path / "ws")
    out = tmp_path / "edit.json"
    result = run_director("--workspace", workspace, "--out", out, "--target-duration-s", TARGET, "--json")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    record = json.loads(out.read_text(encoding="utf-8"))
    assert len(record["video"]) >= 4
    assert not any("no candidate" in warning for warning in summary["warnings"])


def test_fill_pass_tops_up_short_pick_set(tmp_path):
    workspace = build_fill_workspace(tmp_path / "ws")
    out = tmp_path / "edit.json"
    result = run_director("--workspace", workspace, "--out", out, "--target-duration-s", 90, "--json")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["total_duration_s"] >= 0.6 * 90
    assert summary["total_duration_s"] <= 90 + 12.0
    assert summary["slots"] > 10


def test_late_hold_reason(tmp_path):
    _, _, _, record = write_edit(tmp_path)
    late_holds = [slot for slot in record["video"] if slot["role"] == "hold" and slot["reason"].endswith("late hold")]
    assert late_holds
    assert all(slot["reason"] for slot in record["video"])


def test_without_shotlog_reason_is_label_based(tmp_path):
    _, _, _, record = write_edit(tmp_path)
    assert all("interest" not in slot["reason"] for slot in record["video"])
    assert all(slot["description"] is None for slot in record["video"])


def test_shotlog_score_description_and_ineligible(tmp_path):
    workspace = build_shotlog_workspace(tmp_path / "ws")
    out = tmp_path / "edit.json"
    result = run_director("--workspace", workspace, "--out", out, "--target-duration-s", TARGET, "--json")
    assert result.returncode == 0, result.stderr
    record = json.loads(out.read_text(encoding="utf-8"))
    cave = next(slot for slot in record["video"] if slot["clip"] == "clip_0001_x.mp4")
    tunnel = next(slot for slot in record["video"] if slot["clip"] == "clip_0002_x.mp4" and slot["in_s"] < 30.0)
    assert cave["score"] == 0.42
    assert cave["description"] == "a cave"
    assert cave["reason"].startswith("interest 42")
    assert tunnel["score"] == 0.77
    assert tunnel["description"] == "a tunnel"
    assert tunnel["reason"].startswith("interest 77")
    assert len([slot for slot in record["video"] if slot["clip"] == "clip_0001_x.mp4"]) == 1


def test_shotlog_null_description_stays_null(tmp_path):
    workspace = build_shotlog_workspace(tmp_path / "ws")
    out = tmp_path / "edit.json"
    result = run_director("--workspace", workspace, "--out", out, "--target-duration-s", TARGET, "--json")
    assert result.returncode == 0, result.stderr
    record = json.loads(out.read_text(encoding="utf-8"))
    null_slots = [slot for slot in record["video"] if slot["clip"] == "clip_0002_x.mp4" and slot["in_s"] >= 30.0]
    assert null_slots
    assert all(slot["description"] is None for slot in null_slots)


def test_similar_shots_excluded_by_default(tmp_path):
    workspace = build_similarity_workspace(tmp_path / "ws")
    out = tmp_path / "edit.json"
    result = run_director("--workspace", workspace, "--out", out, "--target-duration-s", TARGET, "--json")
    assert result.returncode == 0, result.stderr
    record = json.loads(out.read_text(encoding="utf-8"))
    keys = shot_keys(record, workspace)
    assert ("0001/s1" in keys) != ("0003/s1" in keys)


def test_allow_similar_never_adjacent(tmp_path):
    workspace = build_similarity_workspace(tmp_path / "ws")
    out = tmp_path / "edit.json"
    result = run_director(
        "--workspace", workspace, "--out", out, "--target-duration-s", TARGET, "--allow-similar", "--json"
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(out.read_text(encoding="utf-8"))
    keys = shot_keys(record, workspace)
    assert "0001/s1" in keys and "0003/s1" in keys
    assert abs(keys.index("0001/s1") - keys.index("0003/s1")) > 1


def test_calm_to_energy_targets_non_increasing_and_bounded(tmp_path):
    workspace, _, _, record = write_edit(tmp_path)
    lengths = [round(slot["out_s"] - slot["in_s"], 2) for slot in record["video"]]
    assert lengths[0] >= lengths[-1]
    assert all(2.0 <= length <= 12.0 for length in lengths)
    for slot in record["video"]:
        stem = Path(slot["clip"]).stem
        quality = json.loads((workspace / "quality" / f"{stem}.json").read_text(encoding="utf-8"))
        assert any(
            segment["start_s"] - 0.01 <= slot["in_s"] and slot["out_s"] <= segment["end_s"] + 0.01
            for segment in quality["segments"]
        )


def test_uniform_pacing_slots_share_target(tmp_path):
    _, _, _, record = write_edit(tmp_path, "--pacing", "uniform")
    lengths = [round(slot["out_s"] - slot["in_s"], 2) for slot in record["video"]]
    assert all(length == lengths[0] for length in lengths)
