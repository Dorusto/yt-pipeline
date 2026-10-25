import json
import subprocess
import sys
from pathlib import Path

from common.decisions import build_decisions

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "director.py"


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def shot(key, **overrides):
    record = {
        "key": key,
        "clip": f"{key.split('/')[0]}.mp4",
        "segment_id": key.split("/")[1],
        "start_s": 0.0,
        "end_s": 10.0,
        "eligible": True,
        "role": "hold",
        "score": 0.5,
        "defects": [],
        "similar_to": [],
        "suppressed_by": None,
    }
    record.update(overrides)
    return record


def test_used_row_wins_and_extra_ids_share_the_slot():
    shotlog = {
        "0001/s1": shot("0001/s1", score=0.8),
        "0001/s2": shot("0001/s2", score=0.7),
    }
    rows = [
        {
            "id": "0001/s1",
            "slot": "v1",
            "reason": "hold, clean",
            "score": 0.8,
            "extra_ids": ["0001/s2"],
            "window_in_s": 1.0,
            "window_out_s": 5.0,
        }
    ]
    decisions = build_decisions(shotlog, rows)
    by_id = {item["id"]: item for item in decisions}
    assert by_id["0001/s1"]["status"] == "used"
    assert by_id["0001/s1"]["slot"] == "v1"
    assert by_id["0001/s1"]["reason"] == "hold, clean"
    assert by_id["0001/s2"]["status"] == "used"
    assert by_id["0001/s2"]["slot"] == "v1"
    assert "0001/s1" in by_id["0001/s2"]["reason"]


def test_reject_reasons_follow_the_taxonomy():
    shotlog = {
        "0001/s1": shot("0001/s1", score=0.9),
        "0002/s1": shot("0002/s1", role="setup", eligible=False, score=0.0),
        "0003/s1": shot("0003/s1", suppressed_by="0001/s1", eligible=False, score=0.0),
        "0004/s1": shot("0004/s1", similar_to=["0001/s1"], score=0.8),
        "0005/s1": shot("0005/s1", defects=["blur", "shake"], score=0.4),
        "0006/s1": shot("0006/s1", score=0.2),
        "0007/s1": shot("0007/s1", score=0.95),
    }
    rows = [{"id": "0001/s1", "slot": "v1", "reason": "hero", "score": 0.9}]
    decisions = build_decisions(shotlog, rows, pool_keys={"0006/s1", "0007/s1"})
    by_id = {item["id"]: item for item in decisions}
    assert by_id["0002/s1"]["reason"] == "setup/teardown"
    assert by_id["0003/s1"]["reason"] == "near-duplicate of 0001/s1"
    assert by_id["0004/s1"]["reason"] == "near-duplicate of 0001/s1"
    assert by_id["0005/s1"]["reason"] == "defect: blur, shake"
    assert by_id["0006/s1"]["reason"] == "low score"
    assert by_id["0007/s1"]["reason"] == "did not fit the length"
    assert [item["id"] for item in decisions] == sorted(item["id"] for item in decisions)


def test_drop_reasons_override_and_unknown_ids_are_covered():
    decisions = build_decisions(
        {},
        [],
        pool_keys={"a/s1"},
        drop_reasons={"a/s1": "overlapping window of b/s1, lower score", "b/s1": "kept"},
    )
    by_id = {item["id"]: item for item in decisions}
    assert by_id["a/s1"]["reason"] == "overlapping window of b/s1, lower score"
    assert by_id["b/s1"]["reason"] == "kept"
    assert all(item["status"] == "rejected" for item in decisions)


def write_workspace(root):
    clips = [
        {"file": "clip_0001_a.mp4", "path": "/media/clip_0001_a.mp4", "duration_s": 30.0,
         "creation_time": "2026-01-01T10:00:00"},
        {"file": "clip_0002_b.mp4", "path": "/media/clip_0002_b.mp4", "duration_s": 30.0,
         "creation_time": "2026-01-01T10:01:00"},
    ]
    write_json(root / "ingest.json", {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips})
    segments = [
        {"id": "s1", "start_s": 0.0, "end_s": 25.0, "role": "hold", "confidence": 0.9, "defects": []},
        {"id": "s2", "start_s": 25.0, "end_s": 30.0, "role": "teardown", "confidence": 0.9, "defects": []},
    ]
    write_json(root / "quality" / "clip_0001_a.json",
               {"file": "clip_0001_a.mp4", "duration_s": 30.0, "segments": segments})
    write_json(root / "quality" / "clip_0002_b.json",
               {"file": "clip_0002_b.mp4", "duration_s": 30.0, "segments": segments})
    write_json(root / "shotlog.json", {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "shots": [
            {"key": "0001/s1", "clip": "clip_0001_a.mp4", "segment_id": "s1", "start_s": 0.0, "end_s": 25.0,
             "eligible": True, "role": "hold", "score": 0.8, "defects": [], "similar_to": [], "suppressed_by": None,
             "reason": "hold, clean"},
            {"key": "0001/s2", "clip": "clip_0001_a.mp4", "segment_id": "s2", "start_s": 25.0, "end_s": 30.0,
             "eligible": False, "role": "teardown", "score": 0.0, "defects": [], "similar_to": [],
             "suppressed_by": None, "reason": "teardown"},
            {"key": "0002/s1", "clip": "clip_0002_b.mp4", "segment_id": "s1", "start_s": 0.0, "end_s": 25.0,
             "eligible": True, "role": "hold", "score": 0.6, "defects": [], "similar_to": [], "suppressed_by": None,
             "reason": "hold"},
        ],
    })


def test_director_records_music_ambient_shot_key_and_decisions(tmp_path):
    workspace = tmp_path / "ws"
    write_workspace(workspace)
    track = tmp_path / "track.mp3"
    track.write_bytes(b"fake")
    out = tmp_path / "edit.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--workspace", str(workspace), "--out", str(out),
         "--target-duration-s", "20", "--json",
         "--music", str(track), "--ambient-under-music-db", "10"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["audio"]["music"] == {"path": str(track)}
    assert record["audio"]["ambient_under_music_db"] == 10.0
    assert record["video"][0]["shot_key"] == "0001/s1"
    by_id = {item["id"]: item for item in record["decisions"]}
    assert by_id["0001/s1"]["status"] == "used"
    assert by_id["0001/s2"]["reason"] == "setup/teardown"
    assert by_id["0002/s1"]["status"] in {"used", "rejected"}
    assert by_id["0002/s1"]["reason"]


def test_director_without_music_keeps_audio_empty(tmp_path):
    workspace = tmp_path / "ws"
    write_workspace(workspace)
    out = tmp_path / "edit.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--workspace", str(workspace), "--out", str(out),
         "--target-duration-s", "20", "--json"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["audio"]["music"] is None
    assert record["audio"]["ambient_under_music_db"] is None


def test_an_eligible_photo_with_a_minor_flaw_is_not_called_a_defect():
    from common.decisions import _reject_reason

    photo = {"media_type": "photo", "eligible": True, "defects": ["cut_off"], "score": 0.6}
    assert _reject_reason(photo, True, 0.5) == "did not fit the length (minor flaw: cut_off)"


def test_a_deliberate_move_shake_is_not_reported_as_a_defect():
    from common.decisions import _reject_reason

    move = {"motion_label": "pan_left", "defects": ["shake"], "score": 0.4}
    assert _reject_reason(move, True, 0.5) == "low score"
    assert _reject_reason(move, False, 0.5) == "low score"
    orbit = {"semantic_motion": "orbit", "defects": ["shake"], "score": 0.9}
    assert _reject_reason(orbit, True, 0.5) == "did not fit the length"


def test_a_static_hold_blur_is_still_a_defect():
    from common.decisions import _reject_reason

    hold = {"motion_label": "static", "defects": ["blur"], "score": 0.4}
    assert _reject_reason(hold, True, 0.5) == "defect: blur"

