"""Golden regression tests for the editing grammar, on fixtures only, no footage.

Every golden scenario is a frozen workspace (ingest, quality, motion, shotlog) with
frozen expected cut windows. The rule-based director must reproduce those windows
and the produced edit must never break the editing grammar, whatever changes next.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "director.py"
GOLDEN = Path(__file__).parent / "fixtures" / "golden"

SKIP_ROLES = {"accidental", "setup", "teardown"}
MOVE_ROLES = {"move", "transition_in", "transition_out"}
CONTIGUOUS_TOLERANCE_S = 0.5
LANDING_GAP_S = 0.75
LANDING_FLOOR_S = 0.75
TOLERANCE_S = 0.01

REQUIRED_SCENARIOS = {
    "skip_roles",
    "phrase_move_strong",
    "phrase_hold_strong",
    "same_take",
    "similar_excluded",
    "similar_not_adjacent",
    "hero_present",
    "photo_slots",
    "photo_similar",
}

PHOTO_MIN_S = 2.0
PHOTO_MAX_S = 4.0
KEN_BURNS_MOVES = {"zoom_in", "zoom_out", "pan_left", "pan_right"}


def load_scenarios():
    files = sorted(GOLDEN.glob("*.json"))
    return [json.loads(path.read_text(encoding="utf-8")) for path in files]


SCENARIOS = load_scenarios()
SCENARIOS_BY_NAME = {scenario["name"]: scenario for scenario in SCENARIOS}


def materialize(workspace, root):
    for relative, doc in workspace.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return root


def run_director(workspace, out, args):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--workspace", str(workspace), "--out", str(out), "--force", "--json", *args],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), json.loads(out.read_text(encoding="utf-8"))


def run_scenario(tmp_path, name):
    scenario = SCENARIOS_BY_NAME[name]
    root = tmp_path / name
    workspace = materialize(scenario["workspace"], root / "ws")
    summary, edit = run_director(workspace, root / "edit.json", scenario["args"])
    return summary, edit, workspace


def slot_times(edit):
    return [slot_signature(slot) for slot in edit.get("video") or []]


def slot_signature(slot):
    """One frozen expectation row: a video window or a photo hold."""
    if slot.get("kind") == "photo":
        return ("photo", slot["photo"], round(slot["duration_s"], 3))
    return ("video", slot["clip"], round(slot["in_s"], 3), round(slot["out_s"], 3))


def expected_signature(slot):
    if slot.get("kind") == "photo":
        return ("photo", slot["photo"], round(slot["duration_s"], 3))
    return ("video", slot["clip"], round(slot["in_s"], 3), round(slot["out_s"], 3))


def slot_shot(shots, slot):
    """The shotlog row a slot comes from: by window for video, by file for photos."""
    if slot.get("kind") == "photo":
        for shot in shots:
            if shot.get("media_type") == "photo" and shot.get("clip") == slot["photo"]:
                return shot
        return None
    return shot_for(shots, slot["clip"], slot["in_s"], slot["out_s"])


def quality_segments(root, clip_name):
    stem = Path(clip_name).stem
    path = root / "quality" / f"{stem}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc.get("segments") or []


def segment_for(segments, window_in, window_out):
    best, best_overlap = None, 0.0
    for segment in segments:
        overlap = max(0.0, min(window_out, segment["end_s"]) - max(window_in, segment["start_s"]))
        if overlap > best_overlap:
            best, best_overlap = segment, overlap
    return best


def landing_hold(segments, segment):
    if segment is None or segment.get("role") not in MOVE_ROLES:
        return None
    for other in segments:
        if other.get("role") != "hold":
            continue
        if -TOLERANCE_S <= other["start_s"] - segment["end_s"] <= LANDING_GAP_S:
            return other
    return None


def shotlog_shots(root):
    path = root / "shotlog.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("shots") or []


def shot_for(shots, clip_name, window_in, window_out):
    best, best_overlap = None, 0.0
    for shot in shots:
        if shot.get("clip") != clip_name:
            continue
        overlap = max(0.0, min(window_out, shot["end_s"]) - max(window_in, shot["start_s"]))
        if overlap > best_overlap:
            best, best_overlap = shot, overlap
    return best


def shots_similar(first, second):
    if first is None or second is None:
        return False
    return second.get("key") in (first.get("similar_to") or []) or first.get("key") in (second.get("similar_to") or [])


def source_contiguous(first, second):
    return (
        abs(first["out_s"] - second["in_s"]) <= CONTIGUOUS_TOLERANCE_S
        or abs(second["out_s"] - first["in_s"]) <= CONTIGUOUS_TOLERANCE_S
    )


def grammar_violations(edit, root):
    """Every editing-grammar violation of an edit judged against its workspace."""
    slots = edit.get("video") or []
    shots = shotlog_shots(root)
    violations = []
    segments_by_clip = {}
    for slot in slots:
        if slot.get("kind") != "photo":
            segments_by_clip.setdefault(slot["clip"], quality_segments(root, slot["clip"]))
    for slot in slots:
        if slot.get("kind") == "photo":
            duration = slot.get("duration_s")
            if (
                not isinstance(duration, (int, float))
                or isinstance(duration, bool)
                or duration < PHOTO_MIN_S - TOLERANCE_S
                or duration > PHOTO_MAX_S + TOLERANCE_S
            ):
                violations.append(f"{slot['id']}: photo duration {duration} is outside {PHOTO_MIN_S}-{PHOTO_MAX_S}")
            burns = slot.get("ken_burns") or {}
            if burns.get("move") not in KEN_BURNS_MOVES:
                violations.append(f"{slot['id']}: photo has no valid Ken Burns move")
            if slot.get("role") in SKIP_ROLES:
                violations.append(f"{slot['id']}: role {slot['role']} is never a slot")
            continue
        segments = segments_by_clip[slot["clip"]]
        segment = segment_for(segments, slot["in_s"], slot["out_s"])
        if slot.get("role") in SKIP_ROLES:
            violations.append(f"{slot['id']}: role {slot['role']} is never a slot")
        if segment is not None and segment.get("role") in SKIP_ROLES:
            violations.append(f"{slot['id']}: covers geometry {segment['role']} segment {segment.get('id')}")
        hold = landing_hold(segments, segment)
        if hold is not None and slot["out_s"] < hold["start_s"] + LANDING_FLOOR_S - TOLERANCE_S:
            violations.append(f"{slot['id']}: move cut before its landing at {hold['start_s']}")
    for first, second in zip(slots, slots[1:]):
        if first.get("kind") != "photo" and second.get("kind") != "photo":
            if first["clip"] == second["clip"] and not source_contiguous(first, second):
                violations.append(f"{second['id']}: adjacent same-take slot, not source-contiguous with {first['id']}")
            segments = segments_by_clip[first["clip"]]
            if first["clip"] == second["clip"]:
                segment = segment_for(segments, first["in_s"], first["out_s"])
                hold = landing_hold(segments, segment)
                if hold is not None and second["in_s"] < hold["end_s"] and second["out_s"] > hold["start_s"]:
                    if first["out_s"] <= hold["start_s"] + TOLERANCE_S:
                        violations.append(f"{first['id']}: move then its own landing {second['id']}")
        if shots_similar(slot_shot(shots, first), slot_shot(shots, second)):
            violations.append(f"{second['id']}: adjacent similar pair with {first['id']}")
    photo_files = {slot["photo"] for slot in slots if slot.get("kind") == "photo"}
    for shot in shots:
        if not shot.get("eligible") or not shot.get("hero"):
            continue
        if shot.get("media_type") == "photo":
            if shot["clip"] not in photo_files:
                violations.append(f"eligible hero {shot['key']} is missing from the edit")
            continue
        segments = segments_by_clip.setdefault(shot["clip"], quality_segments(root, shot["clip"]))
        segment = segment_for(segments, shot["start_s"], shot["end_s"])
        if segment is not None and segment.get("role") in SKIP_ROLES:
            continue
        covered = any(
            slot.get("kind") != "photo"
            and slot["clip"] == shot["clip"]
            and max(0.0, min(slot["out_s"], shot["end_s"]) - max(slot["in_s"], shot["start_s"])) > 0.0
            for slot in slots
        )
        if not covered:
            violations.append(f"eligible hero {shot['key']} is missing from the edit")
    return violations


def craft_edit(slots):
    return {"video": slots}


def craft_slot(slot_id, clip_name, in_s, out_s, role=None):
    slot = {"id": slot_id, "clip": clip_name, "in_s": in_s, "out_s": out_s, "score": 0.5, "reason": "crafted"}
    if role is not None:
        slot["role"] = role
    return slot


def craft_photo_slot(slot_id, photo_name, duration_s=3.0, move="zoom_in"):
    return {
        "id": slot_id,
        "kind": "photo",
        "photo": photo_name,
        "duration_s": duration_s,
        "ken_burns": {"move": move, "amount": 0.08, "focus_x": 0.5, "focus_y": 0.5},
        "score": 0.5,
        "reason": "crafted",
    }


def test_golden_scenarios_cover_every_rule():
    assert {scenario["name"] for scenario in SCENARIOS} == REQUIRED_SCENARIOS


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[scenario["name"] for scenario in SCENARIOS])
def test_golden_edit_matches_frozen_expectations(tmp_path, scenario):
    root = tmp_path / scenario["name"]
    workspace = materialize(scenario["workspace"], root / "ws")
    _, edit = run_director(workspace, root / "edit.json", scenario["args"])
    assert slot_times(edit) == [expected_signature(slot) for slot in scenario["expected_slots"]]
    assert grammar_violations(edit, workspace) == []


def test_golden_never_contains_accidental_setup_teardown(tmp_path):
    _, edit, workspace = run_scenario(tmp_path, "skip_roles")
    assert {slot["clip"] for slot in edit["video"]} == {"clip_0004_x.mp4"}
    assert grammar_violations(edit, workspace) == []


def test_golden_never_cuts_a_move_before_its_landing(tmp_path):
    _, strong, strong_ws = run_scenario(tmp_path, "phrase_move_strong")
    assert grammar_violations(strong, strong_ws) == []
    clip_a = [slot for slot in strong["video"] if slot["clip"] == "clipA.mp4"]
    assert len(clip_a) == 1
    assert clip_a[0]["in_s"] < 10.0
    assert clip_a[0]["out_s"] >= 10.0 + LANDING_FLOOR_S
    _, weak, weak_ws = run_scenario(tmp_path, "phrase_hold_strong")
    assert grammar_violations(weak, weak_ws) == []
    for slot in weak["video"]:
        if slot["clip"] == "clipA.mp4":
            assert slot["in_s"] >= 10.0


def test_golden_never_places_noncontiguous_same_take_slots_adjacent(tmp_path):
    _, edit, workspace = run_scenario(tmp_path, "same_take")
    assert grammar_violations(edit, workspace) == []
    for first, second in zip(edit["video"], edit["video"][1:]):
        if first["clip"] == second["clip"]:
            assert source_contiguous(first, second)


def test_golden_never_places_adjacent_similar_pairs(tmp_path):
    _, excluded, excluded_ws = run_scenario(tmp_path, "similar_excluded")
    assert grammar_violations(excluded, excluded_ws) == []
    clips = {slot["clip"] for slot in excluded["video"]}
    assert not {"clip_0001_x.mp4", "clip_0003_x.mp4"} <= clips
    _, adjacent, adjacent_ws = run_scenario(tmp_path, "similar_not_adjacent")
    assert grammar_violations(adjacent, adjacent_ws) == []
    for first, second in zip(adjacent["video"], adjacent["video"][1:]):
        assert {first["clip"], second["clip"]} != {"clip_0001_x.mp4", "clip_0003_x.mp4"}


def test_golden_never_misses_an_eligible_hero(tmp_path):
    _, edit, workspace = run_scenario(tmp_path, "hero_present")
    assert any(slot["clip"] == "clipH.mp4" for slot in edit["video"])
    assert grammar_violations(edit, workspace) == []


def workspace_root(tmp_path, name):
    scenario = SCENARIOS_BY_NAME[name]
    return materialize(scenario["workspace"], tmp_path / name / "ws")


def test_checker_flags_skip_roles(tmp_path):
    root = workspace_root(tmp_path, "skip_roles")
    edit = craft_edit([craft_slot("v1", "clip_0001_x.mp4", 10.0, 20.0, role="move")])
    assert any("never a slot" in item or "accidental" in item for item in grammar_violations(edit, root))


def test_checker_flags_move_cut_before_its_landing(tmp_path):
    root = workspace_root(tmp_path, "phrase_move_strong")
    edit = craft_edit([craft_slot("v1", "clipA.mp4", 0.0, 9.5, role="move")])
    assert any("move cut before its landing" in item for item in grammar_violations(edit, root))


def test_checker_flags_move_then_own_landing(tmp_path):
    root = workspace_root(tmp_path, "phrase_move_strong")
    edit = craft_edit([
        craft_slot("v1", "clipA.mp4", 0.0, 10.0, role="move"),
        craft_slot("v2", "clipA.mp4", 10.0, 20.0, role="hold"),
    ])
    assert any("move then its own landing" in item for item in grammar_violations(edit, root))


def test_checker_flags_noncontiguous_same_take(tmp_path):
    root = workspace_root(tmp_path, "same_take")
    edit = craft_edit([
        craft_slot("v1", "clipA.mp4", 0.0, 5.0),
        craft_slot("v2", "clipA.mp4", 20.0, 25.0),
    ])
    assert any("same-take" in item for item in grammar_violations(edit, root))


def test_checker_flags_adjacent_similar_pair(tmp_path):
    root = workspace_root(tmp_path, "similar_excluded")
    edit = craft_edit([
        craft_slot("v1", "clip_0001_x.mp4", 0.0, 10.0),
        craft_slot("v2", "clip_0003_x.mp4", 0.0, 10.0),
    ])
    assert any("similar" in item for item in grammar_violations(edit, root))


def test_checker_flags_missing_hero(tmp_path):
    root = workspace_root(tmp_path, "hero_present")
    edit = craft_edit([craft_slot("v1", "clipO1.mp4", 0.0, 10.0)])
    assert any("hero" in item for item in grammar_violations(edit, root))


def test_checker_accepts_clean_edit(tmp_path):
    root = workspace_root(tmp_path, "hero_present")
    edit = craft_edit([
        craft_slot("v1", "clipH.mp4", 22.0, 30.0),
        craft_slot("v2", "clipO1.mp4", 0.0, 10.0),
    ])
    assert grammar_violations(edit, root) == []


def test_golden_photo_slots_have_ken_burns_and_length(tmp_path):
    _, edit, workspace = run_scenario(tmp_path, "photo_slots")
    photos = [slot for slot in edit["video"] if slot.get("kind") == "photo"]
    assert photos
    for slot in photos:
        assert PHOTO_MIN_S <= slot["duration_s"] <= PHOTO_MAX_S
        assert slot["ken_burns"]["move"] in KEN_BURNS_MOVES
    assert grammar_violations(edit, workspace) == []


def test_golden_chronological_order_mixes_photos_by_capture_time(tmp_path):
    _, edit, _ = run_scenario(tmp_path, "photo_slots")
    names = [slot.get("photo") or slot["clip"] for slot in edit["video"]]
    assert names.index("still_0002_a.jpg") < names.index("still_0003_b.jpg")


def test_golden_photo_hero_is_never_missed(tmp_path):
    _, edit, workspace = run_scenario(tmp_path, "photo_slots")
    assert any(slot.get("photo") == "still_0002_a.jpg" for slot in edit["video"])
    assert grammar_violations(edit, workspace) == []


def test_golden_photo_similar_never_both_in_edit(tmp_path):
    _, edit, workspace = run_scenario(tmp_path, "photo_similar")
    names = {slot.get("photo") for slot in edit["video"] if slot.get("kind") == "photo"}
    assert not {"still_0004_c.jpg", "still_0005_d.jpg"} <= names
    assert grammar_violations(edit, workspace) == []


def test_checker_flags_photo_duration_out_of_range(tmp_path):
    root = workspace_root(tmp_path, "photo_slots")
    edit = craft_edit([
        craft_photo_slot("p1", "still_0002_a.jpg", duration_s=8.0),
        craft_photo_slot("p2", "still_0003_b.jpg", duration_s=3.0),
    ])
    assert any("photo duration" in item for item in grammar_violations(edit, root))


def test_checker_flags_photo_without_ken_burns(tmp_path):
    root = workspace_root(tmp_path, "photo_slots")
    slot = craft_photo_slot("p1", "still_0002_a.jpg")
    slot["ken_burns"] = {"move": "slide", "amount": 0.08, "focus_x": 0.5, "focus_y": 0.5}
    edit = craft_edit([slot, craft_photo_slot("p2", "still_0003_b.jpg")])
    assert any("Ken Burns" in item for item in grammar_violations(edit, root))


def test_checker_flags_missing_photo_hero(tmp_path):
    root = workspace_root(tmp_path, "photo_slots")
    edit = craft_edit([craft_photo_slot("p2", "still_0003_b.jpg")])
    assert any("hero" in item for item in grammar_violations(edit, root))
