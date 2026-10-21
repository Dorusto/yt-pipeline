import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import chapters
import subs_from_edit
from common.llm import MockClient
from common.schema import validate_or_raise
from fs_probe import safe_is_dir

CONFIG = {
    "models": {"metadata": "mock-model"},
    "llm_budget_usd": None,
    "llm_backend": "mock",
}

VALID = {"chapters": [{"start_s": 0.0, "title": "Intro", "summary": "Start."}]}

FIXTURES = Path(__file__).parent / "fixtures" / "publish"
FCPROJ = FIXTURES / "fixture.fcproj"
MOCK_CONFIG = Path(__file__).parent / "fixtures" / "mock_llm_config.yaml"
REFERENCE_DIR = Path("/root/media/eval/second-brain")
REFERENCE_FCPROJ = REFERENCE_DIR / "Second Brain.fcproj"
REFERENCE_SEQUENCE = "Second Brain - 15 min"
REFERENCE_WORDS = REFERENCE_DIR / "json"
REFERENCE_END_S = 866.09


class FakeClient:
    """A client returning a different canned text per call, for the retry tests."""

    def __init__(self, texts, cost_per_call=0.0):
        self._texts = list(texts)
        self.calls = 0
        self.spent_usd = 0.0
        self.cost_per_call = cost_per_call

    def complete(self, role, messages, temperature=None, **kwargs):
        index = min(self.calls, len(self._texts) - 1)
        self.calls += 1
        self.spent_usd = round(self.spent_usd + self.cost_per_call, 8)
        return SimpleNamespace(text=self._texts[index], cost_usd=self.cost_per_call)


def write_edit(path, video, sources=None):
    document = {
        "schema_version": "0.1",
        "generated_at": "2026-10-21T00:00:00+00:00",
        "tool": "tests/test_chapters.py",
        "brief": {"text": "a tiny synthetic edit for the chapters tests"},
        "sources": sources or [
            {"file": "cam.mp4", "path": "/x/cam.mp4", "duration_s": 10.0, "media_type": "video"},
        ],
        "video": video,
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def write_words(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "segments": [{
                "id": 0,
                "start": entries[0][1],
                "end": entries[-1][2],
                "text": " ".join(entry[0] for entry in entries),
                "words": [
                    {"word": text, "start": start, "end": end}
                    for text, start, end in entries
                ],
            }]
        }),
        encoding="utf-8",
    )
    return path


def patch(monkeypatch, client):
    monkeypatch.setattr(chapters, "get_client", lambda: client)


def run_main(tmp_path, monkeypatch, client, edit, words, extra=None):
    patch(monkeypatch, client)
    argv = [str(edit), str(words)]
    if extra:
        argv.extend(extra)
    return chapters.main(argv)


def two_window_edit(tmp_path):
    return write_edit(
        tmp_path / "edit.json",
        [
            {"id": "v1", "clip": "cam.mp4", "in_s": 0.0, "out_s": 2.0, "score": 1.0, "reason": "a"},
            {"id": "v2", "clip": "cam2.mp4", "in_s": 0.0, "out_s": 2.0, "score": 1.0, "reason": "b"},
        ],
        sources=[
            {"file": "cam.mp4", "path": "/x/cam.mp4", "duration_s": 10.0, "media_type": "video"},
            {"file": "cam2.mp4", "path": "/x/cam2.mp4", "duration_s": 10.0, "media_type": "video"},
        ],
    )


def test_map_words_uses_timeline_seconds(tmp_path):
    edit = two_window_edit(tmp_path)
    words = tmp_path / "words"
    write_words(words / "cam.json", [("unu", 0.5, 1.0), ("doi", 1.5, 2.0)])
    write_words(words / "cam2.json", [("trei", 0.5, 1.0)])
    document = json.loads(edit.read_text(encoding="utf-8"))
    groups = chapters.build_groups(document)
    words_map, single = chapters.build_words_map(words)
    mapped, warnings = chapters.map_words(groups, words_map, single)
    assert warnings == []
    assert [round(item["start"], 3) for item in mapped] == [0.5, 1.5, 2.5]
    assert [item["word"] for item in mapped] == ["unu", "doi", "trei"]


def test_paired_group_counts_words_once(tmp_path):
    edit = write_edit(
        tmp_path / "edit.json",
        [
            {"id": "w001", "clip": "screen.mp4", "in_s": 1.0, "out_s": 3.0, "score": 1.0, "reason": "start"},
            {"id": "w001", "clip": "cam.mp4", "in_s": 1.35, "out_s": 3.35, "score": 1.0, "reason": "start"},
        ],
        sources=[
            {"file": "screen.mp4", "path": "/x/screen.mp4", "duration_s": 10.0, "media_type": "video"},
            {"file": "cam.mp4", "path": "/x/cam.mp4", "duration_s": 10.0, "media_type": "video"},
        ],
    )
    words = tmp_path / "words"
    write_words(words / "cam.json", [("unu", 1.5, 2.0), ("doi", 2.5, 3.0), ("trei", 3.0, 3.3)])
    document = json.loads(edit.read_text(encoding="utf-8"))
    groups = chapters.build_groups(document)
    words_map, single = chapters.build_words_map(words)
    mapped, warnings = chapters.map_words(groups, words_map, single)
    assert len(mapped) == 3
    assert [round(item["start"], 3) for item in mapped] == [0.15, 1.15, 1.65]


def test_llm_pass_writes_and_validates(tmp_path, monkeypatch, capsys):
    edit = two_window_edit(tmp_path)
    words = tmp_path / "words"
    write_words(words / "cam.json", [("unu", 0.5, 1.0), ("doi", 1.5, 2.0)])
    write_words(words / "cam2.json", [("trei", 0.5, 1.0)])
    data = {"chapters": [
        {"start_s": 0.0, "title": "Intro", "summary": "Start."},
        {"start_s": 2.5, "title": "Demo", "summary": "Demo."},
    ]}
    client = MockClient(text=json.dumps(data), config=CONFIG, cost_per_call=0.005)
    code = run_main(tmp_path, monkeypatch, client, edit, words, extra=["--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["outputs"]["chapters"] == str(tmp_path / "chapters.json")
    assert summary["chapters"] == 2
    assert summary["words_mapped"] == 3
    assert summary["wall_time_s"] >= 0
    assert summary["llm_cost_usd"] == 0.005
    document = json.loads((tmp_path / "chapters.json").read_text(encoding="utf-8"))
    validate_or_raise(document, "chapters")
    assert document["tool"] == "scripts/chapters.py"
    assert document["chapters"][1]["start_s"] == 2.5


def test_first_chapter_snapped_to_zero(tmp_path, monkeypatch):
    edit = two_window_edit(tmp_path)
    words = tmp_path / "words"
    write_words(words / "cam.json", [("unu", 0.5, 1.0), ("doi", 1.5, 2.0)])
    write_words(words / "cam2.json", [("trei", 0.5, 1.0)])
    data = {"chapters": [{"start_s": 0.5, "title": "Intro", "summary": "Start."}]}
    client = MockClient(text=json.dumps(data), config=CONFIG)
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 0
    document = json.loads((tmp_path / "chapters.json").read_text(encoding="utf-8"))
    assert document["chapters"][0]["start_s"] == 0.0


def test_decreasing_chapters_exit_2(tmp_path, monkeypatch, capsys):
    edit = two_window_edit(tmp_path)
    words = tmp_path / "words"
    write_words(words / "cam.json", [("unu", 0.5, 1.0), ("doi", 1.5, 2.0)])
    write_words(words / "cam2.json", [("trei", 0.5, 1.0)])
    data = {"chapters": [
        {"start_s": 0.0, "title": "Intro", "summary": "Start."},
        {"start_s": 1.5, "title": "Demo", "summary": "Demo."},
        {"start_s": 1.0, "title": "Back", "summary": "Back."},
    ]}
    client = MockClient(text=json.dumps(data), config=CONFIG)
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 2
    err = capsys.readouterr().err
    assert "chapter 3" in err
    assert "Traceback" not in err


def test_long_title_exit_2(tmp_path, monkeypatch, capsys):
    edit = two_window_edit(tmp_path)
    words = tmp_path / "words"
    write_words(words / "cam.json", [("unu", 0.5, 1.0), ("doi", 1.5, 2.0)])
    write_words(words / "cam2.json", [("trei", 0.5, 1.0)])
    data = {"chapters": [{"start_s": 0.0, "title": "x" * 41, "summary": "Start."}]}
    client = MockClient(text=json.dumps(data), config=CONFIG)
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 2
    err = capsys.readouterr().err
    assert "chapter 1" in err
    assert "Traceback" not in err


def test_empty_kept_transcript_exit_2(tmp_path, monkeypatch, capsys):
    edit = two_window_edit(tmp_path)
    words = tmp_path / "words"
    write_words(words / "cam.json", [("unu", 5.0, 6.0)])
    write_words(words / "cam2.json", [("trei", 5.0, 6.0)])
    client = MockClient(text=json.dumps(VALID), config=CONFIG)
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 2
    err = capsys.readouterr().err
    assert "empty" in err
    assert "Traceback" not in err


def test_missing_words_file_exit_2(tmp_path, monkeypatch, capsys):
    edit = two_window_edit(tmp_path)
    words = tmp_path / "words"
    words.mkdir()
    client = MockClient(text=json.dumps(VALID), config=CONFIG)
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 2
    err = capsys.readouterr().err
    assert "empty" in err
    assert "Traceback" not in err


def test_retry_once_on_unparseable_output(tmp_path, monkeypatch):
    edit = two_window_edit(tmp_path)
    words = tmp_path / "words"
    write_words(words / "cam.json", [("unu", 0.5, 1.0), ("doi", 1.5, 2.0)])
    write_words(words / "cam2.json", [("trei", 0.5, 1.0)])
    client = FakeClient(["not json at all", json.dumps(VALID)])
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 0
    assert client.calls == 2
    assert (tmp_path / "chapters.json").is_file()


def test_two_unparseable_outputs_exit_2(tmp_path, monkeypatch, capsys):
    edit = two_window_edit(tmp_path)
    words = tmp_path / "words"
    write_words(words / "cam.json", [("unu", 0.5, 1.0), ("doi", 1.5, 2.0)])
    write_words(words / "cam2.json", [("trei", 0.5, 1.0)])
    client = FakeClient(["nope", "still nope"])
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 2
    assert client.calls == 2
    err = capsys.readouterr().err
    assert "retry" in err
    assert "Traceback" not in err


def test_rerun_is_atomic(tmp_path, monkeypatch):
    edit = two_window_edit(tmp_path)
    words = tmp_path / "words"
    write_words(words / "cam.json", [("unu", 0.5, 1.0), ("doi", 1.5, 2.0)])
    write_words(words / "cam2.json", [("trei", 0.5, 1.0)])
    client = MockClient(text=json.dumps(VALID), config=CONFIG)
    patch(monkeypatch, client)
    assert chapters.main([str(edit), str(words)]) == 0
    first = json.loads((tmp_path / "chapters.json").read_text(encoding="utf-8"))
    assert chapters.main([str(edit), str(words)]) == 0
    second = json.loads((tmp_path / "chapters.json").read_text(encoding="utf-8"))
    assert {k: v for k, v in first.items() if k != "generated_at"} == {
        k: v for k, v in second.items() if k != "generated_at"
    }
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob("*.part")) == []


def test_missing_edit_exit_2(tmp_path, monkeypatch, capsys):
    words = tmp_path / "words"
    write_words(words / "cam.json", [("unu", 0.5, 1.0)])
    client = MockClient(text=json.dumps(VALID), config=CONFIG)
    code = run_main(tmp_path, monkeypatch, client, tmp_path / "nope.json", words)
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_non_linear_slot_exit_2(tmp_path, monkeypatch, capsys):
    edit = write_edit(
        tmp_path / "edit.json",
        [{"id": "v1", "clip": "cam.mp4", "in_s": 0.0, "out_s": 2.0, "score": 1.0,
          "reason": "a", "middle_speed": 1.2}],
    )
    words = tmp_path / "words"
    write_words(words / "cam.json", [("unu", 0.5, 1.0)])
    client = MockClient(text=json.dumps(VALID), config=CONFIG)
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 2
    assert "middle_speed" in capsys.readouterr().err


def fcproj_args(extra=None):
    argv = ["--fcproj", str(FCPROJ), "--sequence", "Main", "--words-dir", str(FIXTURES)]
    if extra:
        argv.extend(extra)
    return argv


def test_fcproj_mode_matches_subs_from_edit(tmp_path, monkeypatch, capsys):
    out_dir = tmp_path / "sfe"
    rc = subs_from_edit.main([
        "--fcproj", str(FCPROJ), "--sequence", "Main",
        "--words-dir", str(FIXTURES), "--out-dir", str(out_dir), "--json",
    ])
    assert rc == 0
    capsys.readouterr()
    expected = json.loads((out_dir / "Main_words.json").read_text(encoding="utf-8"))

    client = MockClient(text=json.dumps(VALID), config=CONFIG)
    patch(monkeypatch, client)
    out = tmp_path / "chapters.json"
    code = chapters.main(fcproj_args(["--out", str(out), "--json"]))
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["words_mapped"] == len(expected)
    assert summary["warnings"] == []
    assert summary["wall_time_s"] >= 0
    assert summary["llm_cost_usd"] == 0.0
    document = json.loads(out.read_text(encoding="utf-8"))
    validate_or_raise(document, "chapters")

    args = chapters.build_parser().parse_args(fcproj_args())
    mapped, warnings, primary = chapters.resolve_inputs(args)
    assert warnings == []
    assert mapped == expected
    assert primary == FCPROJ


def test_timeline_words_mode_matches_fcproj(tmp_path, monkeypatch, capsys):
    args = chapters.build_parser().parse_args(fcproj_args())
    mapped, _, _ = chapters.resolve_inputs(args)
    timeline = tmp_path / "timeline_words.json"
    timeline.write_text(json.dumps(mapped, ensure_ascii=False), encoding="utf-8")

    client = MockClient(text=json.dumps(VALID), config=CONFIG)
    patch(monkeypatch, client)
    out = tmp_path / "chapters.json"
    code = chapters.main(["--timeline-words", str(timeline), "--out", str(out), "--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["words_mapped"] == len(mapped)
    assert summary["warnings"] == []
    assert summary["wall_time_s"] >= 0
    assert summary["llm_cost_usd"] == 0.0
    document = json.loads(out.read_text(encoding="utf-8"))
    validate_or_raise(document, "chapters")

    args = chapters.build_parser().parse_args(["--timeline-words", str(timeline)])
    mapped2, warnings2, primary = chapters.resolve_inputs(args)
    assert warnings2 == []
    assert mapped2 == mapped
    assert primary == timeline
    assert chapters.format_timeline(mapped2) == chapters.format_timeline(mapped)


@pytest.mark.parametrize("argv", [
    ["edit.json", "words", "--fcproj", "p.fcproj", "--sequence", "Main", "--words-dir", "d"],
    ["edit.json", "words", "--timeline-words", "t.json"],
    ["--fcproj", "p.fcproj", "--timeline-words", "t.json"],
    ["--fcproj", "p.fcproj", "--words-dir", "d"],
    ["--fcproj", "p.fcproj", "--sequence", "Main"],
    ["--sequence", "Main"],
    ["--words-dir", "d"],
    ["--track", "Audio 1"],
    [],
    ["edit.json"],
])
def test_mode_errors_exit_2(argv, monkeypatch, capsys):
    client = MockClient(text=json.dumps(VALID), config=CONFIG)
    patch(monkeypatch, client)
    code = chapters.main(argv)
    assert code == 2
    err = capsys.readouterr().err
    assert "[error]" in err
    assert "Traceback" not in err


def test_mode_error_json_reports_not_ok(capsys):
    code = chapters.main(["--json"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]


def test_help_lists_three_modes():
    text = chapters.build_parser().format_help()
    assert "rough cut" in text
    assert "final project" in text
    assert "timeline words" in text


@pytest.mark.skipif(not safe_is_dir(REFERENCE_DIR), reason="reference material not present")
def test_reference_final_project(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("YT_CONFIG", str(MOCK_CONFIG))
    monkeypatch.setattr(chapters, "_client", None)
    out = tmp_path / "chapters.json"
    code = chapters.main([
        "--fcproj", str(REFERENCE_FCPROJ), "--sequence", REFERENCE_SEQUENCE,
        "--words-dir", str(REFERENCE_WORDS), "--out", str(out), "--json",
    ])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["words_mapped"] == 1955
    assert summary["wall_time_s"] >= 0
    assert summary["llm_cost_usd"] == 0.0
    document = json.loads(out.read_text(encoding="utf-8"))
    validate_or_raise(document, "chapters")
    for chapter in document["chapters"]:
        assert 0 <= chapter["start_s"] <= REFERENCE_END_S
