import json
import math
import subprocess
import sys
from pathlib import Path

from PIL import Image

import director
from common.moves import slot_screen_length
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
    clips.extend(
        {
            "file": f"clipX{index:02d}.mp4",
            "path": f"/media/clipX{index:02d}.mp4",
            "duration_s": 60.0,
            "creation_time": f"2026-01-01T11:{index:02d}:00",
        }
        for index in range(14)
    )
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    holds = [segment(f"s{index}", index * 6.0, index * 6.0 + 5.0, "hold", motion_label="static") for index in range(10)]
    write_json(root / "quality" / "clipA.json", quality_doc("clipA.mp4", 60.0, holds))
    write_json(root / "quality" / "clipB.json", quality_doc("clipB.mp4", 60.0, [segment("s1", 0.0, 60.0, "setup")]))
    write_json(root / "quality" / "clipC.json", quality_doc("clipC.mp4", 60.0, holds))
    for index in range(14):
        write_json(
            root / "quality" / f"clipX{index:02d}.json",
            quality_doc(f"clipX{index:02d}.mp4", 60.0, [segment("s1", 0.0, 5.0, "hold", motion_label="static")]),
        )
    return root


def write_edit(tmp_path, *extra):
    workspace = build_workspace(tmp_path / "ws")
    out = tmp_path / "edit.json"
    result = run_director(
        "--workspace", workspace, "--out", out, "--target-duration-s", TARGET, "--json", *extra
    )
    assert result.returncode == 0, result.stderr
    return workspace, out, json.loads(result.stdout), json.loads(out.read_text(encoding="utf-8"))


def shotlog_shot(
    key, clip, segment_id, start, end, eligible, score, description, reason,
    similar_to=None, hero=None, role="hold", motion_label="static", semantic_motion=None,
):
    return {
        "key": key,
        "clip": clip,
        "segment_id": segment_id,
        "start_s": start,
        "end_s": end,
        "eligible": eligible,
        "role": role,
        "role_source": "geometry",
        "motion_label": motion_label,
        "label_source": "geometry",
        "score": score,
        "scores": {"labels": score},
        "description": description,
        "hook": description or f"{segment_id}",
        "reason": reason,
        "similar_to": similar_to or [],
        "hero": hero,
        "semantic_motion": semantic_motion,
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


def build_clip_a_workspace(root, segments, moves):
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
    write_json(root / "quality" / "clipA.json", quality_doc("clipA.mp4", 60.0, segments))
    write_json(root / "motion" / "clipA.json", motion_doc("clipA.mp4", 60.0, moves))
    for name in ("clipB", "clipC", "clipD"):
        write_json(
            root / "quality" / f"{name}.json",
            quality_doc(f"{name}.mp4", 60.0, [segment("s1", 0.0, 60.0, "hold", motion_label="static")]),
        )
    return root


def phrase_workspace(root, move_score=0.9, hold_score=0.5):
    clips = [
        {"file": "clipA.mp4", "path": "/media/clipA.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:00:00"},
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
                segment("s1", 0.0, 10.0, "move", motion_label="pan_right"),
                segment("s2", 10.0, 20.0, "hold", motion_label="static"),
            ],
        ),
    )
    write_json(
        root / "motion" / "clipA.json",
        motion_doc("clipA.mp4", 60.0, [move("m1", 0.0, 10.0, "pan_right", "regular", "no_shaking", 0.0, 10.0, 0.0, 10.0)]),
    )
    write_shotlog(
        root,
        [
            shotlog_shot("clipA/s1", "clipA.mp4", "s1", 0.0, 10.0, True, move_score, "the move", "interest move · pan_right"),
            shotlog_shot("clipA/s2", "clipA.mp4", "s2", 10.0, 20.0, True, hold_score, "the hold", "interest hold · static"),
        ],
    )
    return root


def build_upgrade_workspace(root):
    clips = [
        {"file": "clip_0001_x.mp4", "path": "/media/clip_0001_x.mp4", "duration_s": 120.0, "creation_time": "2026-01-01T10:00:00"},
        {"file": "clip_0002_x.mp4", "path": "/media/clip_0002_x.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:01:00"},
        {"file": "clip_0003_x.mp4", "path": "/media/clip_0003_x.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:02:00"},
        {"file": "clip_0004_x.mp4", "path": "/media/clip_0004_x.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:03:00"},
    ]
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    write_json(
        root / "quality" / "clip_0001_x.json",
        quality_doc(
            "clip_0001_x.mp4",
            120.0,
            [
                segment("s1", 0.0, 5.0, "hold", motion_label="static"),
                segment("s2", 80.0, 85.0, "hold", motion_label="static"),
            ],
        ),
    )
    for name in ("clip_0002_x", "clip_0003_x", "clip_0004_x"):
        write_json(
            root / "quality" / f"{name}.json",
            quality_doc(f"{name}.mp4", 60.0, [segment("s1", 0.0, 60.0, "hold", motion_label="static")]),
        )
    write_shotlog(
        root,
        [
            shotlog_shot("0001/s1", "clip_0001_x.mp4", "s1", 0.0, 5.0, True, 0.5, "weak", "interest 50 · static by geometry · hold · clean"),
            shotlog_shot("0001/s2", "clip_0001_x.mp4", "s2", 80.0, 85.0, True, 0.9, "strong", "interest 90 · static by geometry · hold · clean"),
            shotlog_shot("0002/s1", "clip_0002_x.mp4", "s1", 0.0, 60.0, True, 0.3, "b", "interest 30 · static by geometry · hold · clean"),
            shotlog_shot("0003/s1", "clip_0003_x.mp4", "s1", 0.0, 60.0, True, 0.3, "c", "interest 30 · static by geometry · hold · clean"),
            shotlog_shot("0004/s1", "clip_0004_x.mp4", "s1", 0.0, 60.0, True, 0.3, "d", "interest 30 · static by geometry · hold · clean"),
        ],
    )
    return root


def build_skip_role_workspace(root):
    clips = [
        {"file": "clip_0001_x.mp4", "path": "/media/clip_0001_x.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:00:00"},
        {"file": "clip_0002_x.mp4", "path": "/media/clip_0002_x.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:01:00"},
        {"file": "clip_0003_x.mp4", "path": "/media/clip_0003_x.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:02:00"},
        {"file": "clip_0004_x.mp4", "path": "/media/clip_0004_x.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:03:00"},
    ]
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    write_json(root / "quality" / "clip_0001_x.json", quality_doc("clip_0001_x.mp4", 60.0, [segment("s1", 0.0, 60.0, "accidental")]))
    write_json(root / "quality" / "clip_0002_x.json", quality_doc("clip_0002_x.mp4", 60.0, [segment("s1", 0.0, 60.0, "setup")]))
    write_json(root / "quality" / "clip_0003_x.json", quality_doc("clip_0003_x.mp4", 60.0, [segment("s1", 0.0, 60.0, "teardown")]))
    write_json(
        root / "quality" / "clip_0004_x.json",
        quality_doc("clip_0004_x.mp4", 60.0, [segment("s1", 0.0, 60.0, "hold", motion_label="static")]),
    )
    write_shotlog(
        root,
        [
            shotlog_shot("0001/s1", "clip_0001_x.mp4", "s1", 0.0, 60.0, True, 0.99, "junk", "interest 99 · move by verdict · move · clean", role="move", motion_label="complex_motion"),
            shotlog_shot("0002/s1", "clip_0002_x.mp4", "s1", 0.0, 60.0, True, 0.99, "junk", "interest 99 · move by verdict · move · clean", role="move", motion_label="complex_motion"),
            shotlog_shot("0003/s1", "clip_0003_x.mp4", "s1", 0.0, 60.0, True, 0.99, "junk", "interest 99 · move by verdict · move · clean", role="move", motion_label="complex_motion"),
            shotlog_shot("0004/s1", "clip_0004_x.mp4", "s1", 0.0, 60.0, True, 0.5, "keep", "interest 50 · static by geometry · hold · clean"),
        ],
    )
    return root


def build_label_workspace(root):
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
            quality_doc(clip["file"], 60.0, [segment("s1", 0.0, 60.0, "hold", motion_label="static")]),
        )
    write_shotlog(
        root,
        [
            shotlog_shot("0001/s1", "clip_0001_x.mp4", "s1", 0.0, 60.0, True, 0.9, "hero", "interest 90 · travel_backward by verdict · move · clean", role="move", motion_label="travel_backward"),
            shotlog_shot("0002/s1", "clip_0002_x.mp4", "s1", 0.0, 60.0, True, 0.4, "b", "interest 40 · static by geometry · hold · clean"),
        ],
    )
    return root


PHRASE_TARGET = 10.0
PHRASE_SLOT = 2.0


def run_phrase_edit(workspace, out):
    return run_edit(workspace, out, "--pacing", "uniform", "--target-duration-s", PHRASE_TARGET)


def crossing_slots(record):
    return [
        slot
        for slot in record["video"]
        if slot["clip"] == "clipA.mp4" and slot["in_s"] < 10.0 and slot["out_s"] > 10.0
    ]


def run_edit(workspace, out, *extra):
    result = run_director(
        "--workspace", workspace, "--out", out, "--target-duration-s", TARGET, "--json", *extra
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), json.loads(out.read_text(encoding="utf-8"))


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
    record = json.loads(out.read_text(encoding="utf-8"))
    used = {slot["clip"] for slot in record["video"]}
    assert used == {f"clip{index}.mp4" for index in range(8)}


def test_fill_pass_tops_up_short_pick_set(tmp_path):
    workspace = build_fill_workspace(tmp_path / "ws")
    out = tmp_path / "edit.json"
    result = run_director("--workspace", workspace, "--out", out, "--target-duration-s", 90, "--json")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["total_duration_s"] >= 0.5 * 90
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
    result = run_director(
        "--workspace", workspace, "--out", out, "--target-duration-s", TARGET, "--order", "score", "--json"
    )
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
    assert all(1.5 <= length <= 6.0 for length in lengths)
    for slot in record["video"]:
        stem = Path(slot["clip"]).stem
        quality = json.loads((workspace / "quality" / f"{stem}.json").read_text(encoding="utf-8"))
        assert any(
            segment["start_s"] - 0.01 <= slot["in_s"] and slot["out_s"] <= segment["end_s"] + 0.01
            for segment in quality["segments"]
        )


def test_uniform_pacing_slots_share_target(tmp_path):
    _, _, _, record = write_edit(tmp_path, "--pacing", "uniform")
    ordinary = [slot for slot in record["video"] if not slot.get("middle_speed")]
    lengths = [round(slot_screen_length(slot), 2) for slot in ordinary]
    assert len(lengths) >= 3
    assert all(length == lengths[0] for length in lengths)


def test_no_move_then_own_landing(tmp_path):
    workspace = phrase_workspace(tmp_path / "ws")
    _, record = run_phrase_edit(workspace, tmp_path / "edit.json")
    slots = record["video"]
    for first, second in zip(slots, slots[1:]):
        move_then_landing = (
            first["clip"] == "clipA.mp4"
            and first["in_s"] <= 0.01
            and first["out_s"] <= 10.01
            and second["clip"] == "clipA.mp4"
            and second["in_s"] >= 9.99
            and second["out_s"] <= 20.01
        )
        assert not move_then_landing
    for slot in slots:
        if slot["clip"] == "clipA.mp4" and slot["in_s"] <= 0.01:
            assert slot["out_s"] > 10.0


def test_phrase_cut_inside_hold(tmp_path):
    workspace = phrase_workspace(tmp_path / "ws")
    _, record = run_phrase_edit(workspace, tmp_path / "edit.json")
    windows = crossing_slots(record)
    assert windows
    assert all(slot["out_s"] >= 10.75 for slot in windows)


def test_phrase_trimmed_from_head(tmp_path):
    workspace = phrase_workspace(tmp_path / "ws")
    summary, record = run_phrase_edit(workspace, tmp_path / "edit.json")
    windows = crossing_slots(record)
    assert windows
    slot = windows[0]
    assert round(slot["out_s"] - slot["in_s"], 2) == PHRASE_SLOT
    assert slot["out_s"] >= 10.75
    assert slot["in_s"] > 0.0
    assert slot["in_s"] >= 0.0
    assert any("phrase trimmed from the head" in warning for warning in summary["warnings"])


def test_phrase_never_exceeds_target(tmp_path):
    workspace = phrase_workspace(tmp_path / "ws")
    _, record = run_phrase_edit(workspace, tmp_path / "edit.json")
    windows = crossing_slots(record)
    assert windows
    assert all(slot["out_s"] - slot["in_s"] <= PHRASE_SLOT + 0.01 for slot in windows)


def test_hold_alone_wins_when_move_weak(tmp_path):
    workspace = phrase_workspace(tmp_path / "ws", move_score=0.3, hold_score=0.9)
    _, record = run_phrase_edit(workspace, tmp_path / "edit.json")
    slots = [slot for slot in record["video"] if slot["clip"] == "clipA.mp4"]
    assert slots
    assert all(slot["in_s"] >= 10.0 for slot in slots)
    assert all(slot["out_s"] <= 20.0 for slot in slots)
    assert not crossing_slots(record)


def test_phrase_wins_when_move_strong(tmp_path):
    workspace = phrase_workspace(tmp_path / "ws", move_score=0.9, hold_score=0.5)
    _, record = run_phrase_edit(workspace, tmp_path / "edit.json")
    slots = [slot for slot in record["video"] if slot["clip"] == "clipA.mp4"]
    assert len(slots) == 1
    assert slots[0]["in_s"] < 10.0
    assert slots[0]["out_s"] > 10.0
    assert slots[0]["out_s"] <= 20.0


def test_tie_prefers_phrase(tmp_path):
    workspace = phrase_workspace(tmp_path / "ws", move_score=0.7, hold_score=0.7)
    _, record = run_phrase_edit(workspace, tmp_path / "edit.json")
    slots = [slot for slot in record["video"] if slot["clip"] == "clipA.mp4"]
    assert len(slots) == 1
    assert slots[0]["in_s"] < 10.0
    assert slots[0]["out_s"] > 10.0


def test_move_without_landing_keeps_handle_window(tmp_path):
    workspace = build_clip_a_workspace(
        tmp_path / "ws",
        [segment("s1", 0.0, 10.0, "move", motion_label="pan_right")],
        [move("m1", 0.0, 10.0, "pan_right", "regular", "no_shaking", 0.0, 10.0, 0.0, 10.0)],
    )
    _, record = run_edit(workspace, tmp_path / "edit.json")
    move_slots = [slot for slot in record["video"] if slot["clip"] == "clipA.mp4"]
    assert len(move_slots) == 1
    assert move_slots[0]["in_s"] == 0.0
    assert move_slots[0]["out_s"] > 2.64
    assert move_slots[0]["middle_speed"] <= 1.2


def test_same_take_windows_never_adjacent(tmp_path):
    workspace = build_clip_a_workspace(
        tmp_path / "ws",
        [
            segment("s1", 0.0, 5.0, "hold", motion_label="static"),
            segment("s2", 20.0, 25.0, "hold", motion_label="static"),
        ],
        [],
    )
    _, record = run_edit(workspace, tmp_path / "edit.json")
    for first, second in zip(record["video"], record["video"][1:]):
        if first["clip"] == second["clip"]:
            assert abs(first["out_s"] - second["in_s"]) <= 0.5


def test_contiguous_same_take_windows_merge(tmp_path):
    workspace = build_clip_a_workspace(
        tmp_path / "ws",
        [
            segment("s1", 0.0, 2.5, "hold", motion_label="static"),
            segment("s2", 2.5, 10.0, "hold", motion_label="static"),
        ],
        [],
    )
    _, record = run_edit(workspace, tmp_path / "edit.json")
    clip_a = [slot for slot in record["video"] if slot["clip"] == "clipA.mp4"]
    assert len(clip_a) == 1
    assert clip_a[0]["in_s"] == 0.0
    assert clip_a[0]["out_s"] == 2.64


def test_json_summary_has_required_keys(tmp_path):
    workspace = phrase_workspace(tmp_path / "ws")
    summary, _ = run_phrase_edit(workspace, tmp_path / "edit.json")
    for key in (
        "status",
        "out",
        "slots",
        "total_duration_s",
        "target_duration_s",
        "warnings",
        "wall_time_s",
        "llm_cost_usd",
    ):
        assert key in summary
    assert summary["llm_cost_usd"] == 0.0


def test_phrase_edit_validates_against_schema(tmp_path):
    workspace = phrase_workspace(tmp_path / "ws")
    _, record = run_phrase_edit(workspace, tmp_path / "edit.json")
    assert validate(record, "edit") == []


def test_phrase_score_is_move_score(tmp_path):
    workspace = phrase_workspace(tmp_path / "ws", move_score=0.9, hold_score=0.5)
    _, record = run_phrase_edit(workspace, tmp_path / "edit.json")
    slots = [slot for slot in record["video"] if slot["clip"] == "clipA.mp4"]
    assert len(slots) == 1
    assert slots[0]["score"] == 0.9
    assert slots[0]["in_s"] < 10.0
    assert slots[0]["out_s"] > 10.0


def test_hold_alone_survives_when_move_weak(tmp_path):
    workspace = phrase_workspace(tmp_path / "ws", move_score=0.5, hold_score=0.9)
    _, record = run_phrase_edit(workspace, tmp_path / "edit.json")
    slots = [slot for slot in record["video"] if slot["clip"] == "clipA.mp4"]
    assert slots
    assert all(slot["in_s"] >= 10.0 for slot in slots)
    assert not crossing_slots(record)


def test_best_of_take_upgrade_replaces_weaker_window(tmp_path):
    workspace = build_upgrade_workspace(tmp_path / "ws")
    summary, record = run_edit(workspace, tmp_path / "edit.json")
    clip1 = [slot for slot in record["video"] if slot["clip"] == "clip_0001_x.mp4"]
    assert clip1
    assert all(slot["in_s"] >= 79.9 for slot in clip1)
    assert not any(slot["in_s"] < 1.0 for slot in clip1)
    assert any("same-take upgrade" in warning for warning in summary["warnings"])


def test_geometry_skip_roles_never_slots_even_with_shotlog(tmp_path):
    workspace = build_skip_role_workspace(tmp_path / "ws")
    _, record = run_edit(workspace, tmp_path / "edit.json")
    clips = {slot["clip"] for slot in record["video"]}
    assert "clip_0001_x.mp4" not in clips
    assert "clip_0002_x.mp4" not in clips
    assert "clip_0003_x.mp4" not in clips
    assert "clip_0004_x.mp4" in clips


def test_slot_labels_come_from_shotlog(tmp_path):
    workspace = build_label_workspace(tmp_path / "ws")
    _, record = run_edit(workspace, tmp_path / "edit.json")
    slot = next(slot for slot in record["video"] if slot["clip"] == "clip_0001_x.mp4")
    assert slot["role"] == "move"
    assert slot["motion_label"] == "travel_backward"


IMPORTANT_TARGET = 10.0


def build_important_workspace(root, segments, moves, shots=None):
    clips = [
        {
            "file": "clip_0001_x.mp4",
            "path": "/media/clip_0001_x.mp4",
            "duration_s": 60.0,
            "creation_time": "2026-01-01T10:00:00",
        },
    ]
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    write_json(root / "quality" / "clip_0001_x.json", quality_doc("clip_0001_x.mp4", 60.0, segments))
    write_json(root / "motion" / "clip_0001_x.json", motion_doc("clip_0001_x.mp4", 60.0, moves))
    if shots is not None:
        write_shotlog(root, shots)
    return root


def run_important_edit(workspace, out, *extra):
    result = run_director(
        "--workspace", workspace, "--out", out, "--target-duration-s", IMPORTANT_TARGET,
        "--pacing", "uniform", "--json", *extra,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), json.loads(out.read_text(encoding="utf-8"))


def test_hero_keeps_whole_window_up_to_cap(tmp_path):
    workspace = build_important_workspace(
        tmp_path / "ws",
        [segment("s1", 0.0, 6.0, "move", motion_label="pan_right")],
        [move("m1", 0.0, 6.0, "pan_right", "regular", "no_shaking", 0.0, 6.0, 0.0, 6.0)],
        [
            shotlog_shot(
                "0001/s1", "clip_0001_x.mp4", "s1", 0.0, 6.0, True, 0.9, "hero move",
                "interest 90 · hero", hero=True, role="move", motion_label="pan_right",
            )
        ],
    )
    _, record = run_important_edit(workspace, tmp_path / "edit.json")
    slot = record["video"][0]
    assert round(slot_screen_length(slot), 2) == 3.5
    assert slot["middle_speed"] <= 1.2


def test_hero_window_longer_than_cap_keeps_tail(tmp_path):
    workspace = build_important_workspace(
        tmp_path / "ws",
        [segment("s1", 0.0, 20.0, "move", motion_label="pan_right")],
        [move("m1", 0.0, 20.0, "pan_right", "regular", "no_shaking", 0.0, 20.0, 0.0, 20.0)],
        [
            shotlog_shot(
                "0001/s1", "clip_0001_x.mp4", "s1", 0.0, 20.0, True, 0.9, "hero move",
                "interest 90 · hero", hero=True, role="move", motion_label="pan_right",
            )
        ],
    )
    _, record = run_important_edit(workspace, tmp_path / "edit.json")
    slot = record["video"][0]
    assert round(slot_screen_length(slot), 2) == 3.5
    assert slot["middle_speed"] == 1.2
    assert slot["out_s"] == 20.0


def test_semantic_move_without_hero_keeps_window(tmp_path):
    workspace = build_important_workspace(
        tmp_path / "ws",
        [segment("s1", 0.0, 8.4, "move", motion_label="orbit_candidate")],
        [move("m1", 0.0, 8.4, "orbit_candidate", "regular", "no_shaking", 0.0, 8.4, 0.0, 8.4)],
        [
            shotlog_shot(
                "0001/s1", "clip_0001_x.mp4", "s1", 0.0, 8.4, True, 0.9, "orbit",
                "interest 90 · orbit", hero=False, role="move", motion_label="orbit_candidate",
                semantic_motion="orbit",
            )
        ],
    )
    _, record = run_important_edit(workspace, tmp_path / "edit.json")
    slot = record["video"][0]
    assert round(slot_screen_length(slot), 2) == 3.5
    assert slot["in_s"] > 0.0


def test_static_semantic_travel_is_not_important(tmp_path):
    workspace = build_important_workspace(
        tmp_path / "ws",
        [segment("s1", 0.0, 11.0, "hold", motion_label="static")],
        [],
        [
            shotlog_shot(
                "0001/s1", "clip_0001_x.mp4", "s1", 0.0, 11.0, True, 0.9, "wake",
                "interest 90 · travel", hero=False, role="hold", motion_label="static",
                semantic_motion="travel",
            )
        ],
    )
    _, record = run_important_edit(workspace, tmp_path / "edit.json")
    slot = record["video"][0]
    assert round(slot["out_s"] - slot["in_s"], 2) == 2.0


def test_hero_static_hold_respects_slot_max(tmp_path):
    workspace = build_important_workspace(
        tmp_path / "ws",
        [segment("s1", 0.0, 11.0, "hold", motion_label="static")],
        [],
        [
            shotlog_shot(
                "0001/s1", "clip_0001_x.mp4", "s1", 0.0, 11.0, True, 0.9, "hero hold",
                "interest 90 · hero", hero=True, role="hold", motion_label="static",
            )
        ],
    )
    _, record = run_important_edit(workspace, tmp_path / "edit.json")
    slot = record["video"][0]
    assert round(slot["out_s"] - slot["in_s"], 2) == 2.5
    assert "middle_speed" not in slot


def test_orbit_candidate_without_shotlog_is_important(tmp_path):
    workspace = build_important_workspace(
        tmp_path / "ws",
        [segment("s1", 0.0, 10.0, "move", motion_label="orbit_candidate")],
        [move("m1", 0.0, 10.0, "orbit_candidate", "regular", "no_shaking", 0.0, 10.0, 0.0, 10.0)],
    )
    _, record = run_important_edit(workspace, tmp_path / "edit.json")
    slot = record["video"][0]
    assert round(slot_screen_length(slot), 2) == 3.5


def test_important_max_s_flag_is_honoured(tmp_path):
    workspace = build_important_workspace(
        tmp_path / "ws",
        [segment("s1", 0.0, 10.0, "move", motion_label="orbit_candidate")],
        [move("m1", 0.0, 10.0, "orbit_candidate", "regular", "no_shaking", 0.0, 10.0, 0.0, 10.0)],
    )
    _, record = run_important_edit(workspace, tmp_path / "edit.json", "--important-max-s", 5)
    slot = record["video"][0]
    assert round(slot_screen_length(slot), 2) == 5.0


def test_help_lists_important_max_s():
    result = run_director("--help")
    assert result.returncode == 0
    assert "--important-max-s" in result.stdout


def build_beats_workspace(root):
    clips = [
        {"file": "clipA.mp4", "path": "/media/clipA.mp4", "duration_s": 160.0, "creation_time": "2026-01-01T10:00:00"},
        {"file": "clipB.mp4", "path": "/media/clipB.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:03:00"},
    ]
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    write_json(
        root / "quality" / "clipA.json",
        quality_doc("clipA.mp4", 160.0, [segment("s1", 0.0, 160.0, "move", motion_label="travel_forward")]),
    )
    write_json(
        root / "motion" / "clipA.json",
        motion_doc("clipA.mp4", 160.0, [move("m1", 10.0, 150.0, "travel_forward", "regular", "no_shaking", 10.0, 150.0, 10.0, 150.0)]),
    )
    write_json(
        root / "quality" / "clipB.json",
        quality_doc("clipB.mp4", 60.0, [segment("s1", 0.0, 20.0, "hold", motion_label="static")]),
    )
    write_json(root / "motion" / "clipB.json", motion_doc("clipB.mp4", 60.0, []))
    write_shotlog(
        root,
        [
            shotlog_shot("clipA/s1b1", "clipA.mp4", "s1b1", 0.0, 60.0, True, 0.95, "approach", "approach · interest 95", hero=True, role="move", motion_label="travel_forward"),
            shotlog_shot("clipA/s1b2", "clipA.mp4", "s1b2", 60.0, 160.0, True, 0.55, "exit", "exit · interest 55", role="move", motion_label="travel_forward"),
            shotlog_shot("clipB/s1", "clipB.mp4", "s1", 0.0, 20.0, True, 0.70, "the other take", "interest 70"),
        ],
    )
    shots = json.loads((root / "shotlog.json").read_text(encoding="utf-8"))["shots"]
    for shot in shots:
        if shot["key"] == "clipA/s1b1":
            shot["beat_of"] = "s1"
            shot["beat_id"] = "b1"
            shot["beat_label"] = "approach"
        if shot["key"] == "clipA/s1b2":
            shot["beat_of"] = "s1"
            shot["beat_id"] = "b2"
            shot["beat_label"] = "exit"
    write_json(root / "shotlog.json", {"generated_at": "2026-01-01T00:00:00+00:00", "workspace": str(root), "weights": {"vision": 0.55, "labels": 0.45}, "shots": shots})
    return root


def test_beats_by_segment_from_shotlog_groups_and_sorts():
    shotlog = {
        "a/s1b2": {"key": "a/s1b2", "clip": "clipA.mp4", "beat_of": "s1", "start_s": 60.0, "end_s": 160.0},
        "a/s1b1": {"key": "a/s1b1", "clip": "clipA.mp4", "beat_of": "s1", "start_s": 0.0, "end_s": 60.0},
        "b/s1": {"key": "b/s1", "clip": "clipB.mp4", "beat_of": None, "start_s": 0.0, "end_s": 5.0},
    }
    grouped = director.beats_by_segment_from_shotlog(shotlog)
    assert list(grouped) == [("clipA.mp4", "s1")]
    assert [b["key"] for b in grouped[("clipA.mp4", "s1")]] == ["a/s1b1", "a/s1b2"]
    assert director.beats_by_segment_from_shotlog(None) == {}


def test_segment_subranges_prefers_beats():
    segment = {"id": "s1", "start_s": 0.0, "end_s": 160.0}
    beats = {
        "s1": [
            {"key": "a/s1b1", "start_s": 0.0, "end_s": 60.0},
            {"key": "a/s1b2", "start_s": 60.0, "end_s": 160.0},
        ]
    }
    assert director.segment_subranges(segment, beats) == [
        (0.0, 60.0, "a/s1b1"),
        (60.0, 160.0, "a/s1b2"),
    ]
    assert director.segment_subranges(segment, {}) == [(0.0, 160.0, None)]


def test_beat_window_is_clamped_to_the_beat():
    clip = {"file": "clipA.mp4"}
    quality = quality_doc("clipA.mp4", 160.0, [segment("s1", 0.0, 160.0, "move", motion_label="travel_forward")])
    motion = motion_doc(
        "clipA.mp4", 160.0,
        [move("m1", 10.0, 150.0, "travel_forward", "regular", "no_shaking", 10.0, 150.0, 10.0, 150.0)],
    )
    beats = {
        "s1": [
            {"key": "clipA/s1b1", "start_s": 0.0, "end_s": 60.0},
            {"key": "clipA/s1b2", "start_s": 60.0, "end_s": 160.0},
        ]
    }
    candidates, _ = director.build_candidates(
        clip, None, 0.0, 0.0, quality, motion, 2.0, beats_by_segment=beats,
    )
    assert [(c["shot_key"], c["in_s"], c["out_s"]) for c in candidates] == [
        ("clipA/s1b1", 10.0, 60.0),
        ("clipA/s1b2", 60.0, 150.0),
    ]


def test_two_beats_of_one_take_with_another_take_between(tmp_path):
    workspace = build_beats_workspace(tmp_path / "ws")
    result = run_director(
        "--workspace", workspace, "--out", tmp_path / "edit.json",
        "--target-duration-s", "12", "--order", "score", "--pacing", "uniform", "--json",
    )
    assert result.returncode == 0, result.stderr
    record = json.loads((tmp_path / "edit.json").read_text(encoding="utf-8"))
    clips = [slot["clip"] for slot in record["video"]]
    assert clips == ["clipA.mp4", "clipB.mp4", "clipA.mp4"]
    first, middle, last = record["video"]
    assert first["out_s"] <= 60.0
    assert last["in_s"] >= 60.0
    assert middle["clip"] == "clipB.mp4"


def test_window_ends_at_the_steady_motion_end():
    clip = {"file": "clipA.mp4"}
    quality = quality_doc("clipA.mp4", 30.0, [segment("s1", 0.0, 30.0, "move", motion_label="pan_right")])
    motion = motion_doc(
        "clipA.mp4", 30.0,
        [move("m1", 0.0, 30.0, "pan_right", "regular", "no_shaking", 2.0, 18.0, 1.75, 25.0)],
    )
    candidates, _ = director.build_candidates(clip, None, 0.0, 0.0, quality, motion, 2.0)
    assert len(candidates) == 1
    assert candidates[0]["in_s"] == 1.75
    assert candidates[0]["out_s"] == 18.0


def test_important_cap_defaults_to_the_pacing_hero_max(tmp_path):
    workspace = build_important_workspace(
        tmp_path / "ws",
        [segment("s1", 0.0, 20.0, "move", motion_label="pan_right")],
        [move("m1", 0.0, 20.0, "pan_right", "regular", "no_shaking", 0.0, 20.0, 0.0, 20.0)],
        [
            shotlog_shot(
                "0001/s1", "clip_0001_x.mp4", "s1", 0.0, 20.0, True, 0.9, "hero move",
                "interest 90 · hero", hero=True, role="move", motion_label="pan_right",
            )
        ],
    )
    _, record = run_important_edit(workspace, tmp_path / "edit.json")
    slot = record["video"][0]
    assert round(slot_screen_length(slot), 2) == 3.5
    assert slot["middle_speed"] == 1.2
    assert slot["out_s"] == 20.0


def test_prompts_state_the_hero_cap_without_hardcoding_it():
    for name in ("editor.md", "reviewer.md", "screenwriter.md"):
        text = (ROOT / "prompts" / name).read_text(encoding="utf-8")
        assert "8.0 s" not in text
        assert "8 seconds" not in text
        assert "up to 8 s" not in text
    editor = (ROOT / "prompts" / "editor.md").read_text(encoding="utf-8")
    assert "hero cap" in editor
    assert "capped at 6.0 s" not in editor
    reviewer = (ROOT / "prompts" / "reviewer.md").read_text(encoding="utf-8")
    assert "hero cap" in reviewer
    assert "up to 6 s" not in reviewer
    assert "ends where the steady motion ends" in reviewer
    screenwriter = (ROOT / "prompts" / "screenwriter.md").read_text(encoding="utf-8")
    assert "hero cap" in screenwriter
    assert "1.5" in screenwriter


def write_photo(path, size=(64, 48), color=(120, 80, 40)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG")
    return path


def photo_ingest_record(path, creation_time="2026-10-12T11:42:00+00:00"):
    return {
        "file": path.name,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "width": 64,
        "height": 48,
        "orientation": None,
        "creation_time": creation_time,
        "creation_time_source": "exif",
        "format": "JPEG",
        "warnings": [],
    }


def photo_shot(
    key,
    name,
    capture_time,
    score,
    subject_position=None,
    tags=None,
    hero=None,
    eligible=True,
    description="a still",
    hook="lighthouse",
    reason="interest 70 · static by geometry · hold · clean",
):
    return {
        "key": key,
        "clip": name,
        "segment_id": "p1",
        "start_s": 0.0,
        "end_s": 0.04,
        "media_type": "photo",
        "capture_time": capture_time,
        "subject_position": subject_position,
        "eligible": eligible,
        "role": "hold",
        "role_source": "geometry",
        "motion_label": "static",
        "label_source": "geometry",
        "confidence": 0.7,
        "uncertain": False,
        "uncertain_reasons": [],
        "description": description,
        "summary": hook,
        "tags": tags or [],
        "subject": hook,
        "scenery": "coast",
        "semantic_motion": "none",
        "interest": 70,
        "hero": hero,
        "score": score,
        "scores": {"vision": score, "labels": 0.7},
        "hook": hook,
        "reason": reason,
        "similar_to": [],
        "suppressed_by": None,
    }


def build_photo_workspace(root, clips, photos, video_shots, photo_shots):
    write_json(
        root / "ingest.json",
        {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "source_dir": str(root),
            "clip_count": len(clips),
            "clips": clips,
            "photo_count": len(photos),
            "photos": photos,
            "warnings": [],
        },
    )
    for clip in clips:
        stem = Path(clip["file"]).stem
        write_json(
            root / "quality" / f"{stem}.json",
            quality_doc(
                clip["file"],
                clip["duration_s"],
                [segment("s1", 0.0, clip["duration_s"], "hold", motion_label="static")],
            ),
        )
        write_json(root / "motion" / f"{stem}.json", motion_doc(clip["file"], clip["duration_s"], []))
    write_shotlog(root, video_shots + photo_shots)
    return root


def test_photo_shot_becomes_photo_slot(tmp_path):
    photo = write_photo(tmp_path / "media" / "photo_01.jpg")
    clips = [
        {"file": "clipA.mp4", "path": "/media/clipA.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:00:00"},
    ]
    photos = [photo_ingest_record(photo, "2026-01-01T10:01:00+00:00")]
    video_shots = [
        shotlog_shot("clipA/s1", "clipA.mp4", "s1", 0.0, 60.0, True, 0.5, "a hold", "interest 50 · static by geometry · hold · clean"),
    ]
    photo_shots = [photo_shot("photo_01/p1", photo.name, "2026-01-01T10:01:00+00:00", 0.8)]
    workspace = build_photo_workspace(tmp_path / "ws", clips, photos, video_shots, photo_shots)
    _, record = run_edit(workspace, tmp_path / "edit.json", "--target-duration-s", "30")
    photo_slots = [slot for slot in record["video"] if slot.get("kind") == "photo"]
    assert photo_slots
    slot = photo_slots[0]
    assert slot["photo"] == photo.name
    assert 2.0 <= slot["duration_s"] <= 4.0
    assert slot["ken_burns"]["move"] in {"zoom_in", "zoom_out", "pan_left", "pan_right"}
    assert 0.01 <= slot["ken_burns"]["amount"] <= 0.5
    assert 0.0 <= slot["ken_burns"]["focus_x"] <= 1.0
    assert 0.0 <= slot["ken_burns"]["focus_y"] <= 1.0
    assert validate(record, "edit") == []
    photo_sources = [source for source in record["sources"] if source.get("media_type") == "photo"]
    assert photo_sources == [
        {"file": photo.name, "path": str(photo), "duration_s": None, "media_type": "photo"}
    ]


def test_photo_ken_burns_from_subject_position(tmp_path):
    specs = [
        ("photo_a.jpg", {"x": 0.2, "y": 0.5}, [], "pan_left"),
        ("photo_b.jpg", {"x": 0.8, "y": 0.5}, [], "pan_right"),
        ("photo_c.jpg", {"x": 0.5, "y": 0.5}, [], "zoom_in"),
        ("photo_d.jpg", {"x": 0.5, "y": 0.5}, ["close-up"], "zoom_out"),
    ]
    photos = []
    photo_shots = []
    for index, (name, position, tags, _) in enumerate(specs):
        path = write_photo(tmp_path / "media" / name, color=(10 + index * 30, 20, 30))
        capture = f"2026-01-01T10:0{index + 1}:00+00:00"
        photos.append(photo_ingest_record(path, capture))
        photo_shots.append(
            photo_shot(f"{Path(name).stem}/p1", name, capture, 0.9, subject_position=position, tags=tags)
        )
    clips = [
        {"file": "clipA.mp4", "path": "/media/clipA.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:00:00"},
    ]
    video_shots = [
        shotlog_shot("clipA/s1", "clipA.mp4", "s1", 0.0, 60.0, True, 0.3, "a hold", "interest 30 · static by geometry · hold · clean"),
    ]
    workspace = build_photo_workspace(tmp_path / "ws", clips, photos, video_shots, photo_shots)
    _, record = run_edit(workspace, tmp_path / "edit.json", "--target-duration-s", "30", "--order", "score")
    by_photo = {slot["photo"]: slot for slot in record["video"] if slot.get("kind") == "photo"}
    assert by_photo["photo_a.jpg"]["ken_burns"]["move"] == "pan_left"
    assert by_photo["photo_b.jpg"]["ken_burns"]["move"] == "pan_right"
    assert by_photo["photo_c.jpg"]["ken_burns"]["move"] == "zoom_in"
    assert by_photo["photo_c.jpg"]["ken_burns"]["focus_x"] == 0.5
    assert by_photo["photo_c.jpg"]["ken_burns"]["focus_y"] == 0.5
    assert by_photo["photo_d.jpg"]["ken_burns"]["move"] == "zoom_out"
    assert all(slot["ken_burns"]["amount"] == 0.08 for slot in by_photo.values())


def test_ken_burns_missing_subject_defaults_to_centre_zoom():
    burns = director.ken_burns_for(None, [])
    assert burns == {"move": "zoom_in", "amount": 0.08, "focus_x": 0.5, "focus_y": 0.5}


def test_hero_photo_never_missed(tmp_path):
    photo = write_photo(tmp_path / "media" / "hero_photo.jpg")
    clips = [
        {"file": "clipO.mp4", "path": "/media/clipO.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:00:00"},
    ]
    photos = [photo_ingest_record(photo, "2026-01-01T10:01:00+00:00")]
    video_shots = [
        shotlog_shot("clipO/s1", "clipO.mp4", "s1", 0.0, 60.0, True, 0.95, "decoy", "interest 95 · static by geometry · hold · clean"),
    ]
    photo_shots = [photo_shot("hero_photo/p1", photo.name, "2026-01-01T10:01:00+00:00", 0.75, hero=True)]
    workspace = build_photo_workspace(tmp_path / "ws", clips, photos, video_shots, photo_shots)
    _, record = run_edit(
        workspace, tmp_path / "edit.json",
        "--target-duration-s", "12", "--order", "score", "--pacing", "uniform",
    )
    assert any(slot.get("kind") == "photo" and slot["photo"] == photo.name for slot in record["video"])


def test_chronological_interleaves_photo_and_video(tmp_path):
    photo = write_photo(tmp_path / "media" / "mid_photo.jpg")
    clips = [
        {"file": "clipA.mp4", "path": "/media/clipA.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T11:40:00"},
        {"file": "clipB.mp4", "path": "/media/clipB.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T11:50:00"},
    ]
    photos = [photo_ingest_record(photo, "2026-01-01T11:45:00+00:00")]
    video_shots = [
        shotlog_shot("clipA/s1", "clipA.mp4", "s1", 0.0, 60.0, True, 0.5, "a", "interest 50 · static by geometry · hold · clean"),
        shotlog_shot("clipB/s1", "clipB.mp4", "s1", 0.0, 60.0, True, 0.5, "b", "interest 50 · static by geometry · hold · clean"),
    ]
    photo_shots = [photo_shot("mid_photo/p1", photo.name, "2026-01-01T11:45:00+00:00", 0.9)]
    workspace = build_photo_workspace(tmp_path / "ws", clips, photos, video_shots, photo_shots)
    _, record = run_edit(workspace, tmp_path / "edit.json", "--target-duration-s", "30", "--order", "chronological")
    kinds = [(slot.get("kind") or "video", slot.get("clip") or slot.get("photo")) for slot in record["video"]]
    photo_index = next(index for index, (kind, _) in enumerate(kinds) if kind == "photo")
    assert kinds[photo_index - 1][1] == "clipA.mp4"
    assert kinds[photo_index + 1][1] == "clipB.mp4"


def test_burst_suppressed_photo_is_not_a_candidate(tmp_path):
    photo = write_photo(tmp_path / "media" / "burst_photo.jpg")
    clips = [
        {"file": "clipA.mp4", "path": "/media/clipA.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:00:00"},
    ]
    photos = [photo_ingest_record(photo, "2026-01-01T10:01:00+00:00")]
    video_shots = [
        shotlog_shot("clipA/s1", "clipA.mp4", "s1", 0.0, 60.0, True, 0.5, "a", "interest 50 · static by geometry · hold · clean"),
    ]
    photo_shots = [photo_shot("burst_photo/p1", photo.name, "2026-01-01T10:01:00+00:00", 0.9, eligible=False)]
    workspace = build_photo_workspace(tmp_path / "ws", clips, photos, video_shots, photo_shots)
    _, record = run_edit(workspace, tmp_path / "edit.json", "--target-duration-s", "30")
    assert not any(slot.get("kind") == "photo" for slot in record["video"])


def write_beats_file(path, phase=0.0, period=0.5, count=200, downbeat_every=4, nested=True):
    beats = [round(phase + period * index, 6) for index in range(count)]
    downbeats = beats[::downbeat_every]
    payload = {
        "beat_s": beats,
        "downbeat_s": downbeats,
        "beat_period_s": period,
        "duration_s": round(beats[-1] + period, 3),
        "bpm": round(60.0 / period, 3),
    }
    write_json(path, {"measurement": payload} if nested else payload)
    return beats


def edit_cuts(record):
    cuts = []
    position = 0.0
    for slot in record["video"]:
        position += slot["duration_s"] if slot.get("kind") == "photo" else slot_screen_length(slot)
        cuts.append(round(position, 4))
    return cuts


def test_beats_snap_every_cut_to_a_beat(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    beats_path = tmp_path / "beats.json"
    beats = write_beats_file(beats_path, phase=0.1)
    out = tmp_path / "edit.json"
    result = run_director(
        "--workspace", workspace, "--out", out, "--target-duration-s", "24",
        "--pacing", "uniform", "--beats", beats_path, "--json",
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["beat_snap"]["unsnapped"] == 0
    record = json.loads(out.read_text(encoding="utf-8"))
    assert len(record["video"]) == 4
    for cut in edit_cuts(record):
        assert min(abs(cut - beat) for beat in beats) <= 0.02
    assert all(slot["transition"]["type"] == "cut" for slot in record["video"][1:])


def test_beat_snap_cut_beyond_the_slack_warns_and_stays():
    grid = {"beat_s": [0.0, 5.0, 10.0], "downbeat_s": [0.0, 5.0, 10.0], "beat_period_s": 0.5}
    video = {
        "clip": "a.mp4",
        "in_s": 0.0,
        "out_s": 2.3,
        "window_in_s": 0.0,
        "window_out_s": 2.3,
        "landing_floor_s": None,
        "important": False,
    }
    photo = {"media_type": "photo", "clip": "p.jpg", "in_s": 0.0, "out_s": 3.0, "duration_s": 3.0}
    warnings = []
    stats = director.snap_slots([video, photo], grid, [False, False], warnings)
    assert video["out_s"] == 2.3
    assert any("cut 1: not snapped" in warning for warning in warnings)
    assert stats["unsnapped"] >= 1


def test_beat_snap_moves_a_cut_past_the_window_within_the_slack():
    beat_s = [round(0.1 + 0.5 * index, 4) for index in range(40)]
    grid = {"beat_s": beat_s, "downbeat_s": beat_s[::4], "beat_period_s": 0.5}
    video = {
        "clip": "a.mp4",
        "in_s": 0.0,
        "out_s": 2.5,
        "window_in_s": 0.0,
        "window_out_s": 2.5,
        "landing_floor_s": None,
        "important": True,
    }
    photo = {"media_type": "photo", "clip": "p.jpg", "in_s": 0.0, "out_s": 3.0, "duration_s": 3.0}
    warnings = []
    stats = director.snap_slots([video, photo], grid, [False, False], warnings)
    assert abs(video["out_s"] - 2.6) <= 0.02
    assert stats["unsnapped"] == 0


def test_beat_snap_photo_duration_lands_on_a_whole_beat():
    beat_s = [round(0.5 * index, 4) for index in range(40)]
    grid = {"beat_s": beat_s, "downbeat_s": beat_s[::4], "beat_period_s": 0.5}
    photo = {"media_type": "photo", "clip": "p.jpg", "in_s": 0.0, "out_s": 2.9, "duration_s": 2.9}
    warnings = []
    director.snap_slots([photo], grid, [False], warnings)
    assert abs(photo["duration_s"] - round(photo["duration_s"] / 0.5) * 0.5) <= 0.02
    assert 2.0 <= photo["duration_s"] <= 4.0
    assert not [warning for warning in warnings if "not snapped" in warning]


def test_beat_snap_photo_slots_stay_on_beats_in_an_edit(tmp_path):
    photo = write_photo(tmp_path / "media" / "photo_01.jpg")
    clips = [
        {"file": "clipA.mp4", "path": "/media/clipA.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:00:00"},
    ]
    photos = [photo_ingest_record(photo, "2026-01-01T10:01:00+00:00")]
    video_shots = [
        shotlog_shot("clipA/s1", "clipA.mp4", "s1", 0.0, 60.0, True, 0.5, "a hold", "interest 50 · static by geometry · hold · clean"),
    ]
    photo_shots = [photo_shot("photo_01/p1", photo.name, "2026-01-01T10:01:00+00:00", 0.8)]
    workspace = build_photo_workspace(tmp_path / "ws", clips, photos, video_shots, photo_shots)
    beats_path = tmp_path / "beats.json"
    beats = write_beats_file(beats_path, phase=0.1)
    _, record = run_edit(
        workspace, tmp_path / "edit.json", "--target-duration-s", "30", "--beats", beats_path,
    )
    photo_slots = [slot for slot in record["video"] if slot.get("kind") == "photo"]
    assert photo_slots
    for slot in photo_slots:
        assert 2.0 <= slot["duration_s"] <= 4.0
        assert abs(slot["duration_s"] - round(slot["duration_s"] / 0.5) * 0.5) <= 0.02
    for cut in edit_cuts(record):
        assert min(abs(cut - beat) for beat in beats) <= 0.02


def test_without_beats_transitions_stay_crossfades(tmp_path):
    _, _, summary, record = write_edit(tmp_path)
    assert summary["beat_snap"] is None
    assert record["video"][1]["transition"] == {"type": "crossfade", "duration_s": 0.3}


def test_help_lists_beats():
    result = run_director("--help")
    assert result.returncode == 0
    assert "--beats" in result.stdout


def test_beats_joins_the_input_mtimes(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    beats_path = tmp_path / "beats.json"
    write_beats_file(beats_path, phase=0.1)
    out = tmp_path / "edit.json"
    args = (
        "--workspace", workspace, "--out", out, "--target-duration-s", "24",
        "--pacing", "uniform", "--beats", beats_path, "--json",
    )
    first = run_director(*args)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["status"] == "written"
    second = run_director(*args)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["status"] == "skipped"
    beats_path.write_text(beats_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    third = run_director(*args)
    assert third.returncode == 0, third.stderr
    assert json.loads(third.stdout)["status"] == "written"


def test_beat_snap_pair_absorb_moves_a_cut_pinned_at_the_window_end():
    beat_s = [round(0.5 * index, 4) for index in range(40)]
    grid = {"beat_s": beat_s, "downbeat_s": beat_s[::4], "beat_period_s": 0.5}
    video = {
        "clip": "a.mp4",
        "in_s": 0.5,
        "out_s": 2.9,
        "window_in_s": 0.0,
        "window_out_s": 2.9,
        "landing_floor_s": None,
        "important": False,
    }
    photo = {"media_type": "photo", "clip": "p.jpg", "in_s": 0.0, "out_s": 3.5, "duration_s": 3.5}
    warnings = []
    stats = director.snap_slots([video, photo], grid, [False, False], warnings)
    assert abs(video["out_s"] - 3.0) <= 0.02
    assert 2.0 <= photo["duration_s"] <= 4.0
    assert not [warning for warning in warnings if "not snapped" in warning]
    assert stats["unsnapped"] == 0


def test_beat_snap_pair_absorb_moves_a_cut_past_both_windows_within_the_slack():
    beat_s = [round(0.5 * index, 4) for index in range(40)]
    grid = {"beat_s": beat_s, "downbeat_s": beat_s[::4], "beat_period_s": 0.5}
    video = {
        "clip": "a.mp4",
        "in_s": 0.0,
        "out_s": 2.9,
        "window_in_s": 0.0,
        "window_out_s": 2.9,
        "landing_floor_s": None,
        "important": False,
    }
    photo = {"media_type": "photo", "clip": "p.jpg", "in_s": 0.0, "out_s": 2.0, "duration_s": 2.0}
    warnings = []
    stats = director.snap_slots([video, photo], grid, [False, False], warnings)
    assert abs(video["out_s"] - 3.0) <= 0.02
    assert stats["unsnapped"] == 0


def test_pacing_table_at_the_anchors():
    assert director.pacing_table(20) == {
        "slot_min": 1.5,
        "slot_target": 2.0,
        "slot_max": 2.5,
        "hero_max": 3.5,
        "n_slots": 10,
    }
    assert director.pacing_table(90) == {
        "slot_min": 2.0,
        "slot_target": 3.6,
        "slot_max": 5.0,
        "hero_max": 6.0,
        "n_slots": 25,
    }
    assert director.pacing_table(180) == {
        "slot_min": 2.5,
        "slot_target": 5.0,
        "slot_max": 6.0,
        "hero_max": 6.0,
        "n_slots": 32,
    }


def test_pacing_table_clamps_outside_the_anchors():
    assert director.pacing_table(10) == director.pacing_table(20)
    assert director.pacing_table(240) == director.pacing_table(180)


def test_pacing_table_interpolates_between_the_anchors():
    pacing = director.pacing_table(60)
    assert 1.5 < pacing["slot_min"] < 2.0
    assert 2.0 < pacing["slot_target"] < 3.6
    assert 2.5 < pacing["slot_max"] < 5.0
    assert 3.5 < pacing["hero_max"] < 6.0
    assert 10 < pacing["n_slots"] < 25


def build_pacing_workspace(root, clip_count=25):
    clips = [
        {
            "file": f"clip_{index:04d}_x.mp4",
            "path": f"/media/clip_{index:04d}_x.mp4",
            "duration_s": 60.0,
            "creation_time": f"2026-01-01T10:{index:02d}:00",
        }
        for index in range(clip_count)
    ]
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    write_json(
        root / "quality" / "clip_0000_x.json",
        quality_doc("clip_0000_x.mp4", 60.0, [segment("s1", 0.0, 20.0, "move", motion_label="travel_forward")]),
    )
    write_json(
        root / "motion" / "clip_0000_x.json",
        motion_doc(
            "clip_0000_x.mp4", 60.0,
            [move("m1", 0.0, 20.0, "travel_forward", "regular", "no_shaking", 0.0, 20.0, 0.0, 20.0)],
        ),
    )
    for clip in clips[1:]:
        stem = Path(clip["file"]).stem
        write_json(
            root / "quality" / f"{stem}.json",
            quality_doc(clip["file"], 60.0, [segment("s1", 0.0, 8.0, "hold", motion_label="static")]),
        )
        write_json(root / "motion" / f"{stem}.json", motion_doc(clip["file"], 60.0, []))
    write_shotlog(
        root,
        [
            shotlog_shot(
                "0000/s1", "clip_0000_x.mp4", "s1", 0.0, 20.0, True, 0.95, "the hero move",
                "interest 95 · hero · travel_forward by geometry · move · clean",
                hero=True, role="move", motion_label="travel_forward",
            ),
        ]
        + [
            shotlog_shot(
                f"{index:04d}/s1", f"clip_{index:04d}_x.mp4", "s1", 0.0, 8.0, True, 0.5, "a hold",
                "interest 50 · static by geometry · hold · clean",
            )
            for index in range(1, clip_count)
        ],
    )
    return root


def run_pacing_edit(workspace, out, *extra):
    result = run_director("--workspace", workspace, "--out", out, "--json", *extra)
    assert result.returncode == 0, result.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def test_short_target_produces_many_short_slots(tmp_path):
    workspace = build_pacing_workspace(tmp_path / "ws")
    record = run_pacing_edit(workspace, tmp_path / "edit.json", "--target-duration-s", "20")
    slots = record["video"]
    assert 8 <= len(slots) <= 10
    hero_slots = [slot for slot in slots if slot.get("shot_key") == "0000/s1"]
    ordinary = [slot for slot in slots if slot.get("shot_key") != "0000/s1"]
    assert len(hero_slots) == 1
    assert round(slot_screen_length(hero_slots[0]), 2) <= 3.5
    lengths = sorted((round(slot_screen_length(slot), 2) for slot in ordinary), reverse=True)
    assert all(1.5 <= length for length in lengths)
    assert sum(length > 2.5 + 0.01 for length in lengths) <= 2


def test_ninety_second_target_produces_twenty_to_twenty_five_slots(tmp_path):
    workspace = build_pacing_workspace(tmp_path / "ws")
    record = run_pacing_edit(workspace, tmp_path / "edit.json", "--target-duration-s", "90")
    slots = record["video"]
    assert 20 <= len(slots) <= 25
    hero_slots = [slot for slot in slots if slot.get("shot_key") == "0000/s1"]
    ordinary = [slot for slot in slots if slot.get("shot_key") != "0000/s1"]
    assert all(slot_screen_length(slot) <= 6.0 for slot in hero_slots)
    lengths = [round(slot_screen_length(slot), 2) for slot in ordinary]
    assert all(2.0 <= length for length in lengths)
    assert sum(length > 5.0 + 0.01 for length in lengths) <= 4


def test_min_slot_s_override_wins_over_the_table(tmp_path):
    workspace = build_pacing_workspace(tmp_path / "ws")
    record = run_pacing_edit(workspace, tmp_path / "edit.json", "--target-duration-s", "20", "--min-slot-s", "2.5")
    ordinary = [slot for slot in record["video"] if slot.get("shot_key") != "0000/s1"]
    lengths = [round(slot["out_s"] - slot["in_s"], 2) for slot in ordinary]
    assert lengths
    assert all(length >= 2.5 - 0.01 for length in lengths)


POLICY_SLOT_MAX_90 = 5.0
POLICY_HERO_MAX_90 = 6.0


def build_policy_workspace(root, pans, holds, pan_window=10.0, hold_window=10.0):
    total = pans + holds
    clips = [
        {
            "file": f"clip_{index:04d}_x.mp4",
            "path": f"/media/clip_{index:04d}_x.mp4",
            "duration_s": 60.0,
            "creation_time": f"2026-01-01T10:{index:02d}:00",
        }
        for index in range(total)
    ]
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    for index, clip in enumerate(clips):
        stem = Path(clip["file"]).stem
        if index < pans:
            segments = [segment("s1", 0.0, pan_window, "move", motion_label="pan_right")]
            moves = [move("m1", 0.0, pan_window, "pan_right", "regular", "no_shaking", 0.0, pan_window, 0.0, pan_window)]
        else:
            segments = [segment("s1", 0.0, hold_window, "hold", motion_label="static")]
            moves = []
        write_json(root / "quality" / f"{stem}.json", quality_doc(clip["file"], 60.0, segments))
        write_json(root / "motion" / f"{stem}.json", motion_doc(clip["file"], 60.0, moves))
    return root


def run_policy_edit(workspace, out, *extra):
    result = run_director("--workspace", workspace, "--out", out, "--target-duration-s", 90, "--json", *extra)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), json.loads(out.read_text(encoding="utf-8"))


def test_static_hold_stays_within_slot_max(tmp_path):
    workspace = build_policy_workspace(tmp_path / "ws", pans=0, holds=12, hold_window=30.0)
    _, record = run_policy_edit(workspace, tmp_path / "edit.json")
    assert record["video"]
    for slot in record["video"]:
        assert slot_screen_length(slot) <= POLICY_SLOT_MAX_90 + 1e-6
        assert "middle_speed" not in slot


def test_long_pan_is_extended_with_a_middle_ramp_of_at_most_1_2(tmp_path):
    workspace = build_policy_workspace(tmp_path / "ws", pans=1, holds=12, pan_window=5.8)
    summary, record = run_policy_edit(workspace, tmp_path / "edit.json")
    pans = [slot for slot in record["video"] if slot["motion_label"] == "pan_right"]
    assert len(pans) == 1
    pan = pans[0]
    assert pan["out_s"] - pan["in_s"] > POLICY_SLOT_MAX_90 - 0.01
    assert 1.0 <= pan.get("middle_speed", 1.0) <= 1.2
    assert slot_screen_length(pan) <= POLICY_HERO_MAX_90 + 1e-6
    assert any(warning.startswith("exception:") for warning in summary["warnings"])
    assert not validate(record, "edit")


def test_exception_share_is_at_most_fifteen_percent_of_the_slots(tmp_path):
    workspace = build_policy_workspace(tmp_path / "ws", pans=20, holds=6, pan_window=12.0)
    summary, record = run_policy_edit(workspace, tmp_path / "edit.json")
    slots = record["video"]
    over = [slot for slot in slots if slot_screen_length(slot) > POLICY_SLOT_MAX_90 + 1e-6]
    assert 1 <= len(over) <= math.ceil(0.15 * len(slots))
    assert sum(warning.startswith("exception:") for warning in summary["warnings"]) == len(over)
    assert all(1.0 <= slot.get("middle_speed", 1.0) <= 1.2 for slot in slots)


def test_policy_windows_stay_inside_the_candidate_window(tmp_path):
    workspace = build_policy_workspace(tmp_path / "ws", pans=6, holds=6, pan_window=7.0)
    _, record = run_policy_edit(workspace, tmp_path / "edit.json")
    for slot in record["video"]:
        limit = 7.0 if slot["motion_label"] == "pan_right" else 10.0
        assert slot["in_s"] >= -1e-9
        assert slot["out_s"] <= limit + 1e-9


def test_beat_snap_keeps_ramped_cuts_on_beats_and_slots_within_their_cap(tmp_path):
    workspace = build_policy_workspace(tmp_path / "ws", pans=10, holds=10, pan_window=12.0)
    beats_path = tmp_path / "beats.json"
    beats = write_beats_file(beats_path, phase=0.0, period=0.5)
    summary, record = run_policy_edit(workspace, tmp_path / "edit.json", "--beats", beats_path)
    position = 0.0
    for slot in record["video"]:
        position += slot_screen_length(slot)
        assert min(abs(position - beat) for beat in beats) <= 0.03
    holds = [slot for slot in record["video"] if slot["motion_label"] == "static"]
    assert holds
    assert all(slot_screen_length(slot) <= POLICY_SLOT_MAX_90 + 1e-6 for slot in holds)
    assert summary["beat_snap"]["unsnapped"] == 0
