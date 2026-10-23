import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

import explain
from common.exit_codes import EXIT_OK, EXIT_USAGE
from common.schema import validate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "explain.py"
FOURCC = cv2.VideoWriter_fourcc(*"mp4v")

BRIEF_TEXT = "a calm 20 second recap of the lake morning"
LOGLINE = "A quiet lake morning that wakes up with the wind."
MUSIC_PATH = "/media/music/track.mp3"

DECISION_REASONS = {
    "0071/s2": "calm opening hold",
    "0071/s3": "energy beat",
    "0072/p1": "the lighthouse still",
    "0073/s1": "near-duplicate of 0071/s2",
    "0074/s1": "setup/teardown",
}


def write_video(path, seconds=3, fps=10, size=(64, 48)):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), FOURCC, fps, size)
    if not writer.isOpened():
        pytest.skip("OpenCV mp4v VideoWriter is unavailable on this build")
    for index in range(seconds * fps):
        frame = np.full((size[1], size[0], 3), (index * 7) % 255, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return path


def write_photo(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), (120, 80, 40)).save(path, "JPEG")
    return path


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def build_edit(video_path, photo_path):
    return {
        "schema_version": "0.1",
        "generated_at": "2026-10-14T10:00:00+00:00",
        "tool": "scripts/director.py",
        "brief": {
            "text": BRIEF_TEXT,
            "target_duration_s": 20.0,
            "order": "chronological",
            "pacing": "calm-to-energy",
        },
        "sources": [
            {"file": "clip_0071.mp4", "path": str(video_path), "duration_s": 3.0, "media_type": "video"},
            {"file": "photo_01.jpg", "path": str(photo_path), "duration_s": None, "media_type": "photo"},
        ],
        "video": [
            {
                "id": "v1",
                "clip": "clip_0071.mp4",
                "in_s": 0.5,
                "out_s": 2.5,
                "score": 0.8,
                "reason": "calm opening hold",
                "shot_key": "0071/s2",
            },
            {
                "id": "p1",
                "kind": "photo",
                "photo": "photo_01.jpg",
                "duration_s": 3.0,
                "ken_burns": {"move": "zoom_in", "amount": 0.08, "focus_x": 0.5, "focus_y": 0.4},
                "score": 0.7,
                "reason": "the lighthouse still",
                "shot_key": "0072/p1",
                "capture_time": None,
                "description": None,
            },
            {
                "id": "v2",
                "clip": "clip_0071.mp4",
                "in_s": 1.0,
                "out_s": 2.0,
                "score": 0.6,
                "reason": "energy beat",
                "transition": {"type": "crossfade", "duration_s": 0.3},
                "shot_key": "0071/s3",
            },
        ],
        "audio": {"music": {"path": MUSIC_PATH}, "ambient_under_music_db": 12.0},
        "decisions": [
            {
                "id": "0071/s2",
                "status": "used",
                "reason": "calm opening hold",
                "slot": "v1",
                "score": 0.8,
                "clip": "clip_0071.mp4",
                "window_in_s": 0.0,
                "window_out_s": 3.0,
            },
            {
                "id": "0071/s3",
                "status": "used",
                "reason": "energy beat",
                "slot": "v2",
                "score": 0.6,
                "clip": "clip_0071.mp4",
                "window_in_s": None,
                "window_out_s": None,
            },
            {
                "id": "0072/p1",
                "status": "used",
                "reason": "the lighthouse still",
                "slot": "p1",
                "score": 0.7,
                "clip": "photo_01.jpg",
                "window_in_s": None,
                "window_out_s": None,
            },
            {
                "id": "0073/s1",
                "status": "rejected",
                "reason": "near-duplicate of 0071/s2",
                "slot": None,
                "score": 0.55,
                "clip": "clip_0073.mp4",
                "window_in_s": 0.0,
                "window_out_s": 4.0,
            },
            {
                "id": "0074/s1",
                "status": "rejected",
                "reason": "setup/teardown",
                "slot": None,
                "score": None,
                "clip": None,
                "window_in_s": None,
                "window_out_s": None,
            },
        ],
        "meta": {"tool": "scripts/director.py", "wall_time_s": 0.5, "llm_cost_usd": 0.0},
    }


def build_plan():
    return {
        "schema_version": "0.1",
        "generated_at": "2026-10-14T09:00:00+00:00",
        "tool": "scripts/llm_director.py",
        "brief": {
            "text": BRIEF_TEXT,
            "target_duration_s": 20.0,
            "order": "chronological",
            "pacing": "calm-to-energy",
        },
        "content_type": "vacation",
        "logline": LOGLINE,
        "structure": [
            {"id": "b1", "name": "Calm open", "intent": "open on the water, calm", "target_s": 8.0},
            {"id": "b2", "name": "Wind picks up", "intent": "energy arrives", "target_s": 12.0},
        ],
        "slot_plan": [
            {"id": "v1", "beat": "b1", "shot_key": "0071/s2", "target_s": 5.0, "why": "the still water hold"},
            {"id": "p1", "beat": "b1", "shot_key": "0072/p1", "target_s": 3.0, "why": "the lighthouse still"},
            {"id": "v2", "beat": "b2", "shot_key": "0071/s3", "target_s": 4.0, "why": "the wake picks up"},
        ],
        "meta": {"tool": "scripts/llm_director.py", "wall_time_s": 1.0, "llm_cost_usd": 0.01},
    }


def build_shotlog():
    return {
        "generated_at": "2026-10-14T08:00:00+00:00",
        "weights": {"vision": 0.55, "labels": 0.45},
        "shots": [
            {
                "key": "0071/s3",
                "clip": "clip_0071.mp4",
                "segment_id": "s3",
                "start_s": 2.0,
                "end_s": 3.0,
                "eligible": True,
                "role": "hold",
                "role_source": "geometry",
                "motion_label": "static",
                "label_source": "geometry",
                "score": 0.6,
                "scores": {"labels": 0.6},
                "hook": "the wake",
                "reason": "interest 60 · static by geometry · hold · clean",
            },
            {
                "key": "0072/p1",
                "clip": "photo_01.jpg",
                "segment_id": "p1",
                "start_s": 0.0,
                "end_s": 0.04,
                "eligible": True,
                "role": "hold",
                "role_source": "geometry",
                "motion_label": "static",
                "label_source": "geometry",
                "score": 0.7,
                "scores": {"labels": 0.7},
                "hook": "the lighthouse",
                "reason": "interest 70 · static by geometry · hold · clean",
                "media_type": "photo",
            },
        ],
    }


def build_workspace(root):
    video_path = write_video(root / "media" / "clip_0071.mp4")
    photo_path = write_photo(root / "media" / "photo_01.jpg")
    write_json(root / "edit.json", build_edit(video_path, photo_path))
    write_json(root / "shot_plan.json", build_plan())
    write_json(root / "shotlog.json", build_shotlog())
    return root


def run_tool(workspace, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--workspace", str(workspace), *extra],
        capture_output=True,
        text=True,
    )


def test_fixtures_are_schema_valid(tmp_path):
    video_path = tmp_path / "media" / "clip_0071.mp4"
    photo_path = tmp_path / "media" / "photo_01.jpg"
    assert validate(build_edit(video_path, photo_path), "edit") == []
    assert validate(build_plan(), "shot_plan") == []
    assert validate(build_shotlog(), "shotlog") == []


def test_explain_writes_html_with_brief_plan_timeline_and_candidates(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    result = run_tool(workspace, "--json")
    assert result.returncode == EXIT_OK, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "written"
    assert summary["out"] == str(workspace / "explain.html")
    assert summary["slots"] == 3
    assert summary["candidates"] == 5
    assert summary["used"] == 3
    assert summary["rejected"] == 2
    assert summary["llm_cost_usd"] == 0.0
    assert summary["wall_time_s"] >= 0
    html = (workspace / "explain.html").read_text(encoding="utf-8")
    assert BRIEF_TEXT in html
    assert "target 20.00 s" in html
    assert "order chronological" in html
    assert "pacing calm-to-energy" in html
    assert f"music {MUSIC_PATH}" in html
    assert "ambient 12.00 dB under the music" in html
    assert LOGLINE in html
    assert "open on the water, calm" in html
    assert "the still water hold" in html
    for slot_id, shot_key in (("v1", "0071/s2"), ("p1", "0072/p1"), ("v2", "0071/s3")):
        assert slot_id in html
        assert shot_key in html
    assert "cut 0.50–2.50 s of 0.00–3.00 s" in html
    assert "cut 1.00–2.00 s of 2.00–3.00 s" in html
    assert "photo 3.00 s of 0.00–0.04 s" in html
    assert "ken_burns zoom_in" in html
    for decision_id, reason in DECISION_REASONS.items():
        assert decision_id in html
        assert reason in html
    assert "data:image/jpeg;base64," in html
    assert 'src="http' not in html
    assert 'href="http' not in html


def test_second_run_skips_and_force_rewrites(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    first = run_tool(workspace, "--json")
    assert json.loads(first.stdout)["status"] == "written"
    html_path = workspace / "explain.html"
    html_path.write_text("stale sentinel", encoding="utf-8")
    os.utime(html_path, None)
    second = run_tool(workspace, "--json")
    summary = json.loads(second.stdout)
    assert summary["status"] == "skipped"
    assert html_path.read_text(encoding="utf-8") == "stale sentinel"
    forced = run_tool(workspace, "--force", "--json")
    assert json.loads(forced.stdout)["status"] == "written"
    rewritten = html_path.read_text(encoding="utf-8")
    assert "stale sentinel" not in rewritten
    assert BRIEF_TEXT in rewritten


def test_stale_output_is_rewritten_without_force(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    first = run_tool(workspace, "--json")
    assert json.loads(first.stdout)["status"] == "written"
    os.utime(workspace / "edit.json", None)
    second = run_tool(workspace, "--json")
    assert json.loads(second.stdout)["status"] == "written"


def test_missing_edit_json_exits_2(tmp_path):
    workspace = tmp_path / "empty"
    workspace.mkdir()
    result = run_tool(workspace, "--json")
    assert result.returncode == EXIT_USAGE
    assert "[error]" in result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["exit_code"] == EXIT_USAGE


def test_missing_workspace_exits_2(tmp_path):
    result = run_tool(tmp_path / "nope", "--json")
    assert result.returncode == EXIT_USAGE
    assert "[error]" in result.stderr


def test_without_shot_plan_says_so_in_one_line(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    (workspace / "shot_plan.json").unlink()
    result = run_tool(workspace, "--json")
    assert result.returncode == EXIT_OK, result.stderr
    html = (workspace / "explain.html").read_text(encoding="utf-8")
    assert "No shot plan (shot_plan.json) in this workspace" in html
    assert LOGLINE not in html


def test_unreadable_video_source_shows_placeholder_and_photo_still_renders(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    (workspace / "media" / "clip_0071.mp4").unlink()
    result = run_tool(workspace, "--json")
    assert result.returncode == EXIT_OK, result.stderr
    html = (workspace / "explain.html").read_text(encoding="utf-8")
    assert 'class="thumb placeholder"' in html
    assert "clip_0071.mp4" in html
    assert "data:image/jpeg;base64," in html


def test_out_path_override_and_refusals(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    out = tmp_path / "elsewhere" / "custom.html"
    result = run_tool(workspace, "--out", str(out), "--json")
    assert result.returncode == EXIT_OK, result.stderr
    summary = json.loads(result.stdout)
    assert summary["out"] == str(out)
    assert out.exists()
    inside_sources = run_tool(workspace, "--out", str(workspace / "media" / "explain.html"), "--json")
    assert inside_sources.returncode == EXIT_USAGE
    assert "sources folder" in inside_sources.stderr
    onto_source = run_tool(workspace, "--out", str(workspace / "media" / "photo_01.jpg"), "--json")
    assert onto_source.returncode == EXIT_USAGE
    assert "[error]" in onto_source.stderr
    assert (workspace / "media" / "photo_01.jpg").is_file()


def test_human_output_mentions_written_and_up_to_date(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    first = run_tool(workspace)
    assert first.returncode == EXIT_OK, first.stderr
    assert "wrote" in first.stdout
    assert first.stdout.strip().startswith("{") is False
    second = run_tool(workspace)
    assert second.returncode == EXIT_OK, second.stderr
    assert "up to date" in second.stdout


def test_help_prints_examples():
    result = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == EXIT_OK
    assert "examples:" in result.stdout
    assert "--workspace" in result.stdout
    assert "--force" in result.stdout


def test_run_returns_skipped_summary_counts(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    run_tool(workspace)
    summary, exit_code = explain.run(workspace)
    assert exit_code == EXIT_OK
    assert summary["status"] == "skipped"
    assert summary["slots"] == 3
    assert summary["candidates"] == 5
    assert summary["used"] == 3
    assert summary["rejected"] == 2


def test_explain_shows_the_middle_speed_ramp_on_the_slot(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    edit["video"][0]["middle_speed"] = 1.2
    write_json(workspace / "edit.json", edit)
    result = run_tool(workspace, "--force")
    assert result.returncode == EXIT_OK, result.stderr
    html = (workspace / "explain.html").read_text(encoding="utf-8")
    assert "middle at 1.20x" in html


def test_explain_says_where_a_centred_cut_is_centred_and_from_what(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    first = edit["video"][0]
    first.update({"peak_s": 12.4, "peak_source": "vision", "centred": True})
    write_json(workspace / "edit.json", edit)
    result = run_tool(workspace, "--force")
    assert result.returncode == EXIT_OK, result.stderr
    html = (workspace / "explain.html").read_text(encoding="utf-8")
    assert "centred on the best moment at 12.4 s (from vision)" in html


def test_explain_stays_quiet_when_the_cut_was_not_trimmed_around_a_peak(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    edit["video"][0].update({"peak_s": 12.4, "peak_source": "motion", "centred": False})
    write_json(workspace / "edit.json", edit)
    result = run_tool(workspace, "--force")
    assert result.returncode == EXIT_OK, result.stderr
    html = (workspace / "explain.html").read_text(encoding="utf-8")
    assert "centred on the best moment" not in html


def test_rule_fallback_is_said_and_the_plan_marked_unused():
    import explain

    director = {"by": "rules", "approved": False, "notes": ["violation v7: same_take: same take as v6"]}
    html = explain._director_section(director)
    assert "rule-based director" in html
    assert "same_take" in html
    assert "not used" in explain._plan_section({"logline": "x"}, used_plan=False)
    assert explain._director_section(None) == ""


def test_thumbnails_read_the_workspace_proxy_when_the_recorded_path_is_gone(tmp_path):
    import explain

    (tmp_path / "proxy").mkdir()
    (tmp_path / "proxy" / "a.mp4").write_bytes(b"x")
    paths = explain._source_paths([{"file": "a.mp4", "path": "/gone/a.mp4"}], tmp_path)
    assert paths["a.mp4"] == str(tmp_path / "proxy" / "a.mp4")
