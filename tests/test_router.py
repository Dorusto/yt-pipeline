import json
import subprocess
import sys
from pathlib import Path

import pytest

import router
from common.schema import validate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "router.py"

S1_BRIEF = "about 90 seconds, chronological, calm start, more energy towards the end, ambient sound only"

CONTENT_BRIEFS = {
    "tutorial": "a tutorial on cutting clips",
    "presentation": "a keynote presentation for the team",
    "talking-head": "a talking head interview by the window",
    "vacation": "a travel recap of the weekend",
}


def run_cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def route_from(tmp_path, brief, *extra):
    out = tmp_path / "route.json"
    result = run_cli("--brief", brief, "--out", out, *extra, "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(out.read_text(encoding="utf-8")), json.loads(result.stdout)


def station(record, name):
    return next(row for row in record["stations"] if row["name"] == name)


def test_s1_brief_parses(tmp_path):
    record, _ = route_from(tmp_path, S1_BRIEF)
    brief = record["brief"]
    assert brief["target_duration_s"] == 90.0
    assert brief["order"] == "chronological"
    assert brief["pacing"] == "calm-to-energy"
    assert brief["ambient_only"] is True
    assert brief["music_file"] is None
    assert brief["signals"]


def test_minutes_are_converted():
    brief, _ = router.parse_brief("a 3 minute recap")
    assert brief["target_duration_s"] == 180.0
    brief, _ = router.parse_brief("about ~2 min")
    assert brief["target_duration_s"] == 120.0


def test_music_is_picked_up():
    assert router.parse_music("music: /tmp/song.mp3")[0] == "/tmp/song.mp3"
    assert router.parse_music("add a mix.wav under it")[0] == "mix.wav"
    assert router.parse_music("no music named here")[0] is None


@pytest.mark.parametrize("content_type", sorted(CONTENT_BRIEFS))
def test_content_type_from_keywords(tmp_path, content_type):
    record, _ = route_from(tmp_path, CONTENT_BRIEFS[content_type])
    assert record["content_type"] == content_type
    assert record["content_type_source"] == "brief"


def test_flag_wins_over_brief(tmp_path):
    out = tmp_path / "route.json"
    result = run_cli("--brief", "a travel trip", "--content-type", "tutorial", "--out", out, "--json")
    assert result.returncode == 0, result.stderr
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["content_type"] == "tutorial"
    assert record["content_type_source"] == "flag"


def make_workspace(tmp_path, shots, transcript=False):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "shotlog.json").write_text(json.dumps({"shots": shots}), encoding="utf-8")
    if transcript:
        (workspace / "clip.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    return workspace


def test_workspace_detects_vacation(tmp_path):
    workspace = make_workspace(
        tmp_path,
        [
            {"motion_label": "travel_forward", "role": "move"},
            {"motion_label": "orbit", "role": "move"},
            {"motion_label": "static", "role": "hold"},
        ],
    )
    out = workspace / "route.json"
    result = run_cli("--brief", "make a nice video", "--workspace", workspace, "--out", out, "--json")
    assert result.returncode == 0, result.stderr
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["content_type"] == "vacation"
    assert record["content_type_source"] == "workspace"


def test_workspace_detects_talking_head(tmp_path):
    workspace = make_workspace(
        tmp_path,
        [
            {"motion_label": "static", "role": "hold"},
            {"motion_label": "minor_motion", "role": "hold"},
            {"motion_label": "pan_left", "role": "move"},
        ],
        transcript=True,
    )
    out = workspace / "route.json"
    result = run_cli("--brief", "make a nice video", "--workspace", workspace, "--out", out, "--json")
    assert result.returncode == 0, result.stderr
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["content_type"] == "talking-head"
    assert record["content_type_source"] == "workspace"


def test_beats_only_with_music(tmp_path):
    with_music, _ = route_from(tmp_path, "a trip recap, music: /tmp/song.mp3")
    beats = station(with_music, "beats")
    assert beats["enabled"] is True
    assert beats["settings"]["align"] is True
    assert beats["settings"]["music_file"] == "/tmp/song.mp3"

    ambient, _ = route_from(tmp_path, "a trip recap, ambient sound only")
    assert station(ambient, "beats")["enabled"] is False

    tutorial, _ = route_from(tmp_path, "a tutorial, music: /tmp/song.mp3")
    assert station(tutorial, "beats")["enabled"] is False


def test_vacation_without_music_marks_required_when(tmp_path):
    record, _ = route_from(tmp_path, "a trip recap")
    beats = station(record, "beats")
    assert beats["enabled"] is False
    assert beats["required_when"] == "a music file in the brief"
    assert "required_when" not in station(record, "ingest")


def test_graphics_chapters_shorts_follow_table(tmp_path):
    expected = {
        "vacation": (False, False, False),
        "tutorial": (True, True, True),
        "presentation": (True, True, True),
        "talking-head": (True, True, True),
    }
    for content_type, (graphics, chapters, shorts) in expected.items():
        record, _ = route_from(tmp_path, CONTENT_BRIEFS[content_type])
        assert record["content_type"] == content_type
        assert station(record, "graphics")["enabled"] is graphics
        assert station(record, "chapters")["enabled"] is chapters
        assert station(record, "shorts")["enabled"] is shorts


def test_graphics_kits(tmp_path):
    tutorial, _ = route_from(tmp_path, CONTENT_BRIEFS["tutorial"])
    assert station(tutorial, "graphics")["settings"]["kits"] == ["titles", "steps"]
    presentation, _ = route_from(tmp_path, CONTENT_BRIEFS["presentation"])
    assert station(presentation, "graphics")["settings"]["kits"] == ["slides", "charts"]
    talking, _ = route_from(tmp_path, CONTENT_BRIEFS["talking-head"])
    assert station(talking, "graphics")["settings"]["kits"] == ["lower_thirds"]


def test_director_settings(tmp_path):
    record, _ = route_from(tmp_path, "a travel recap")
    assert station(record, "director")["settings"] == {"mode": "llm", "fallback": "rules", "max_rounds": 3}

    rules_only, _ = route_from(tmp_path, "a travel recap, rules only")
    assert station(rules_only, "director")["settings"] == {"mode": "rules", "fallback": None, "max_rounds": 0}
    assert "rules only" in rules_only["brief"]["signals"]


def test_motion_settings(tmp_path):
    vacation, _ = route_from(tmp_path, CONTENT_BRIEFS["vacation"])
    assert station(vacation, "motion")["settings"] == {"sample_fps": 10}
    tutorial, _ = route_from(tmp_path, CONTENT_BRIEFS["tutorial"])
    assert station(tutorial, "motion")["settings"] == {"sample_fps": 10, "weight": 0.2}
    presentation, _ = route_from(tmp_path, CONTENT_BRIEFS["presentation"])
    assert station(presentation, "motion")["settings"] == {"sample_fps": 10, "weight": 0.2}
    talking, _ = route_from(tmp_path, CONTENT_BRIEFS["talking-head"])
    assert station(talking, "motion")["settings"] == {"sample_fps": 10, "weight": 0.3}


def test_stations_are_complete_and_in_order(tmp_path):
    record, _ = route_from(tmp_path, S1_BRIEF)
    assert [row["name"] for row in record["stations"]] == list(router.STATION_ORDER)
    for row in record["stations"]:
        assert row["reason"]


def test_schema_and_summary(tmp_path):
    record, summary = route_from(tmp_path, S1_BRIEF)
    assert validate(record, "route") == []
    for key in (
        "status",
        "out",
        "content_type",
        "content_type_source",
        "stations_enabled",
        "target_duration_s",
        "music_file",
        "warnings",
        "wall_time_s",
        "llm_cost_usd",
    ):
        assert key in summary
    assert summary["status"] == "written"
    assert summary["content_type"] == "vacation"
    assert summary["content_type_source"] in ("brief", "workspace", "default")
    assert summary["stations_enabled"] == [
        row["name"] for row in record["stations"] if row["enabled"]
    ]
    assert summary["llm_cost_usd"] == 0.0


def test_rerun_is_skipped(tmp_path):
    out = tmp_path / "route.json"
    first = run_cli("--brief", S1_BRIEF, "--out", out, "--json")
    assert json.loads(first.stdout)["status"] == "written"
    second = run_cli("--brief", S1_BRIEF, "--out", out, "--json")
    assert second.returncode == 0
    assert json.loads(second.stdout)["status"] == "skipped"
    forced = run_cli("--brief", S1_BRIEF, "--out", out, "--force", "--json")
    assert json.loads(forced.stdout)["status"] == "written"


def test_brief_file_and_workspace_default_out(tmp_path):
    brief = tmp_path / "brief.txt"
    brief.write_text(S1_BRIEF, encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = run_cli("--brief-file", brief, "--workspace", workspace, "--json")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert Path(summary["out"]) == workspace / "route.json"
    assert (workspace / "route.json").is_file()


def test_missing_brief_errors():
    result = run_cli("--out", "/tmp/route-nowhere.json")
    assert result.returncode == 1
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0].startswith("[error]")


def test_missing_workspace_warns(tmp_path):
    out = tmp_path / "route.json"
    result = run_cli("--brief", S1_BRIEF, "--workspace", tmp_path / "nope", "--out", out, "--json")
    assert result.returncode == 0, result.stderr
    record = json.loads(out.read_text(encoding="utf-8"))
    assert any("workspace" in warning for warning in record["warnings"])


def test_help_mentions_flags():
    result = run_cli("--help")
    assert result.returncode == 0
    assert "--brief" in result.stdout
    assert "--workspace" in result.stdout
    assert "--json" in result.stdout
