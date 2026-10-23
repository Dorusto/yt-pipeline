import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import shorts_rank
import subs_from_edit
from common.schema import validate_or_raise
from fs_probe import safe_is_dir

CONFIG = {
    "models": {"director": "mock-model"},
    "llm_budget_usd": None,
    "llm_backend": "mock",
}

MOCK_CONFIG = Path(__file__).parent / "fixtures" / "mock_llm_config.yaml"
REFERENCE_DIR = Path("/root/media/eval/second-brain")
REFERENCE_FCPROJ = REFERENCE_DIR / "Second Brain.fcproj"
REFERENCE_SEQUENCE = "Second Brain - 15 min"
REFERENCE_WORDS = REFERENCE_DIR / "json"
REFERENCE_END_S = 866.09
TICKS = 254016000000

SHORT_SENTENCES = [
    (70.0, 75.0), (76.0, 81.0), (82.0, 87.0), (88.0, 93.0),
    (170.0, 175.0), (176.0, 181.0), (182.0, 187.0), (188.0, 193.0),
    (270.0, 275.0), (276.0, 281.0), (282.0, 287.0), (288.0, 293.0),
]
LONG_SENTENCES = [(index * 6.0, index * 6.0 + 5.0) for index in range(17)]
SPREAD_SENTENCES = [
    (70.0 + group * 100.0 + index * 6.0, 70.0 + group * 100.0 + index * 6.0 + 5.0)
    for group in range(6)
    for index in range(5)
]
INTRO_ONLY_SENTENCES = [(0.0, 5.0), (6.0, 11.0), (12.0, 17.0), (18.0, 23.0), (24.0, 29.0), (30.0, 35.0)]
CAMERA_CLIP = "camera 2026-09-29 20-58-37.mp4"
SCREEN_CLIP = "2026-09-29 20-58-35.mp4"
PAIRED_SENTENCES = [(index * 6.0, index * 6.0 + 5.0) for index in range(50)]


class AutoScoreClient:
    """A client that scores every candidate id it finds in the message."""

    def __init__(self, scores=None, default=(50, 50), reason="ok", cost_per_call=0.0, omit=None,
                 description="Prima propozitie. A doua propozitie."):
        self.scores = scores or {}
        self.default = default
        self.reason = reason
        self.cost_per_call = cost_per_call
        self.omit = set(omit or ())
        self.description = description
        self.calls = 0
        self.spent_usd = 0.0

    def complete(self, role, messages, temperature=None, **kwargs):
        self.calls += 1
        self.spent_usd = round(self.spent_usd + self.cost_per_call, 8)
        message = messages[0]["content"]
        ids = [int(match) for match in re.findall(r"^(\d+)\. \[", message, re.M)]
        rows = []
        for candidate_id in ids:
            if candidate_id in self.omit:
                continue
            hook, payoff = self.scores.get(candidate_id, self.default)
            rows.append({
                "id": candidate_id,
                "hook": hook,
                "payoff": payoff,
                "reason": self.reason,
                "title": f"Titlu {candidate_id}",
                "description": self.description,
            })
        return SimpleNamespace(text=json.dumps({"scores": rows}), cost_usd=self.cost_per_call)


class SequenceClient:
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
        "tool": "tests/test_shorts_rank.py",
        "brief": {"text": "a tiny synthetic edit for the shorts ranking tests"},
        "sources": sources or [
            {"file": "cam.mp4", "path": "/x/cam.mp4", "duration_s": 200.0, "media_type": "video"},
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


def write_sentence_words(path, sentences):
    return write_words(path, [(f"w{index}", start, end) for index, (start, end) in enumerate(sentences)])


def one_slot_edit(tmp_path, out_s=40.0):
    return write_edit(
        tmp_path / "edit.json",
        [{"id": "v1", "clip": "cam.mp4", "in_s": 0.0, "out_s": out_s, "score": 1.0, "reason": "a"}],
    )


def short_fixture(tmp_path):
    edit = one_slot_edit(tmp_path, out_s=300.0)
    words = write_sentence_words(tmp_path / "words" / "cam.json", SHORT_SENTENCES)
    return edit, words


def long_fixture(tmp_path):
    edit = one_slot_edit(tmp_path, out_s=110.0)
    words = write_sentence_words(tmp_path / "words" / "cam.json", LONG_SENTENCES)
    return edit, words


def spread_fixture(tmp_path):
    edit = one_slot_edit(tmp_path, out_s=600.0)
    words = write_sentence_words(tmp_path / "words" / "cam.json", SPREAD_SENTENCES)
    return edit, words


def paired_edit(tmp_path):
    edit = write_edit(
        tmp_path / "edit.json",
        [
            {"id": "w001", "clip": SCREEN_CLIP, "in_s": 0.0, "out_s": 84.0, "score": 1.0, "reason": "a"},
            {"id": "w001", "clip": CAMERA_CLIP, "in_s": 0.0, "out_s": 84.0, "score": 1.0, "reason": "a"},
            {"id": "w002", "clip": CAMERA_CLIP, "in_s": 84.0, "out_s": 300.0, "score": 1.0, "reason": "b"},
        ],
        sources=[
            {"file": SCREEN_CLIP, "path": "/x/" + SCREEN_CLIP, "duration_s": 300.0, "media_type": "video"},
            {"file": CAMERA_CLIP, "path": "/x/" + CAMERA_CLIP, "duration_s": 300.0, "media_type": "video"},
        ],
    )
    words = write_sentence_words(
        tmp_path / "words" / f"{Path(CAMERA_CLIP).stem}.json", PAIRED_SENTENCES
    )
    return edit, words


def camera_only_edit(tmp_path):
    edit = write_edit(
        tmp_path / "edit.json",
        [{"id": "v1", "clip": CAMERA_CLIP, "in_s": 0.0, "out_s": 300.0, "score": 1.0, "reason": "a"}],
        sources=[
            {"file": CAMERA_CLIP, "path": "/x/" + CAMERA_CLIP, "duration_s": 300.0, "media_type": "video"},
        ],
    )
    words = write_sentence_words(
        tmp_path / "words" / f"{Path(CAMERA_CLIP).stem}.json", SHORT_SENTENCES
    )
    return edit, words


def write_config(tmp_path, shorts_layout):
    lines = [
        "models:",
        "  director: mock-model",
        "llm_budget_usd: null",
        "llm_backend: mock",
    ]
    if shorts_layout is not None:
        lines.append(f"shorts_layout: {shorts_layout}")
    path = tmp_path / "config.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def first_face_candidate_id(edit, words):
    candidates = candidates_of(edit, words)
    return next(
        index for index, candidate in enumerate(candidates, 1)
        if candidate["start_s"] >= 84.0
    )


def write_chapters(tmp_path, chapters):
    path = tmp_path / "chapters.json"
    path.write_text(
        json.dumps({
            "schema_version": "0.1",
            "generated_at": "2026-10-21T00:00:00+00:00",
            "tool": "scripts/chapters.py",
            "chapters": chapters,
        }),
        encoding="utf-8",
    )
    return path


def candidates_of(edit_path, words_path, chapters_path=None):
    document = json.loads(edit_path.read_text(encoding="utf-8"))
    groups = shorts_rank.build_groups(document)
    words_map, single = shorts_rank.build_words_map(words_path)
    mapped, _warnings, _dropped = shorts_rank.map_words(groups, words_map, single)
    chapters = (
        shorts_rank.load_chapters(chapters_path)
        if chapters_path
        else [{"start_s": 0.0, "title": None}]
    )
    return shorts_rank.build_candidates(mapped, chapters)


def patch(monkeypatch, client):
    monkeypatch.setattr(shorts_rank, "get_client", lambda: client)


def run_main(tmp_path, monkeypatch, client, edit, words, extra=None):
    patch(monkeypatch, client)
    argv = [str(edit), str(words)]
    if extra:
        argv.extend(extra)
    return shorts_rank.main(argv)


def test_candidates_are_sentence_aligned_and_20_60s(tmp_path):
    edit, words = long_fixture(tmp_path)
    candidates = candidates_of(edit, words)
    assert candidates
    sentence_starts = {start for start, _ in LONG_SENTENCES}
    sentence_ends = {end for _, end in LONG_SENTENCES}
    for candidate in candidates:
        assert candidate["start_s"] in sentence_starts
        assert candidate["end_s"] in sentence_ends
        assert 20.0 <= candidate["end_s"] - candidate["start_s"] <= 60.0


def test_chapter_boundary_splits_candidates(tmp_path):
    edit, words = long_fixture(tmp_path)
    chapters = write_chapters(tmp_path, [
        {"start_s": 0.0, "title": "Intro", "summary": "Start."},
        {"start_s": 50.0, "title": "Demo", "summary": "Demo."},
    ])
    candidates = candidates_of(edit, words, chapters)
    assert candidates
    for candidate in candidates:
        if candidate["start_s"] < 50.0:
            assert candidate["end_s"] <= 50.0
            assert candidate["chapter_start_s"] == 0.0
            assert candidate["chapter_title"] == "Intro"
        else:
            assert candidate["start_s"] >= 50.0
            assert candidate["chapter_start_s"] == 50.0
            assert candidate["chapter_title"] == "Demo"


def test_without_chapters_fields_are_null(tmp_path, monkeypatch, capsys):
    edit, words = short_fixture(tmp_path)
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, edit, words, extra=["--json"])
    assert code == 0
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    validate_or_raise(document, "shorts_candidates")
    assert all(candidate["chapter_start_s"] is None for candidate in document["candidates"])
    assert all(candidate["chapter_title"] is None for candidate in document["candidates"])


def test_with_chapters_fields_are_filled(tmp_path, monkeypatch, capsys):
    edit, words = long_fixture(tmp_path)
    chapters = write_chapters(tmp_path, [
        {"start_s": 0.0, "title": "Intro", "summary": "Start."},
        {"start_s": 50.0, "title": "Demo", "summary": "Demo."},
    ])
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, edit, words, extra=["--chapters", str(chapters), "--json"])
    assert code == 0
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    validate_or_raise(document, "shorts_candidates")
    assert all(candidate["chapter_start_s"] is not None for candidate in document["candidates"])
    assert all(candidate["chapter_title"] in ("Intro", "Demo") for candidate in document["candidates"])


def test_layout_split_written_on_every_candidate(tmp_path, monkeypatch, capsys):
    edit, words = short_fixture(tmp_path)
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, edit, words, extra=["--layout", "split", "--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["layout"] == "split"
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    validate_or_raise(document, "shorts_candidates")
    assert document["candidates"]
    assert all(candidate["layout"] == "split" for candidate in document["candidates"])


def test_layout_defaults_to_face_without_flag(tmp_path, monkeypatch, capsys):
    edit, words = short_fixture(tmp_path)
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, edit, words, extra=["--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["layout"] is None
    assert summary["layout_policy"] == "auto"
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    validate_or_raise(document, "shorts_candidates")
    assert document["candidates"]
    assert all(candidate["layout"] == "face" for candidate in document["candidates"])


def test_layout_policy_auto_paired(tmp_path, monkeypatch, capsys):
    edit, words = paired_edit(tmp_path)
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, edit, words, extra=["--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["layout"] is None
    assert summary["layout_policy"] == "auto"
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    validate_or_raise(document, "shorts_candidates")
    assert {candidate["layout"] for candidate in document["candidates"]} == {"split", "face"}
    for candidate in document["candidates"]:
        expected = "face" if candidate["start_s"] >= 84.0 else "split"
        assert candidate["layout"] == expected


def test_layout_policy_auto_camera_only(tmp_path, monkeypatch, capsys):
    edit, words = camera_only_edit(tmp_path)
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, edit, words, extra=["--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["layout_policy"] == "auto"
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    validate_or_raise(document, "shorts_candidates")
    assert document["candidates"]
    assert all(candidate["layout"] == "face" for candidate in document["candidates"])


def test_layout_policy_on_paired(tmp_path, monkeypatch, capsys):
    edit, words = paired_edit(tmp_path)
    face_id = first_face_candidate_id(edit, words)
    client = AutoScoreClient(scores={face_id: (100, 100)})
    code = run_main(
        tmp_path, monkeypatch, client, edit, words,
        extra=["--layout-policy", "on", "--json"],
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["layout_policy"] == "on"
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    validate_or_raise(document, "shorts_candidates")
    assert document["candidates"]
    assert all(candidate["layout"] == "split" for candidate in document["candidates"])


def test_layout_policy_on_camera_only(tmp_path, monkeypatch, capsys):
    edit, words = camera_only_edit(tmp_path)
    client = AutoScoreClient()
    code = run_main(
        tmp_path, monkeypatch, client, edit, words,
        extra=["--layout-policy", "on", "--json"],
    )
    assert code == 0
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    assert all(candidate["layout"] == "face" for candidate in document["candidates"])


def test_layout_policy_off_paired(tmp_path, monkeypatch, capsys):
    edit, words = paired_edit(tmp_path)
    client = AutoScoreClient()
    code = run_main(
        tmp_path, monkeypatch, client, edit, words,
        extra=["--layout-policy", "off", "--json"],
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["layout_policy"] == "off"
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    assert all(candidate["layout"] == "face" for candidate in document["candidates"])


def test_layout_flag_beats_policy(tmp_path, monkeypatch, capsys):
    edit, words = paired_edit(tmp_path)
    client = AutoScoreClient()
    code = run_main(
        tmp_path, monkeypatch, client, edit, words,
        extra=["--layout", "split", "--layout-policy", "off", "--json"],
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["layout"] == "split"
    assert summary["layout_policy"] == "off"
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    assert all(candidate["layout"] == "split" for candidate in document["candidates"])


def test_layout_policy_from_config(tmp_path, monkeypatch, capsys):
    edit, words = paired_edit(tmp_path)
    config = write_config(tmp_path, "off")
    monkeypatch.setenv("YT_CONFIG", str(config))
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, edit, words, extra=["--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["layout_policy"] == "off"
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    assert all(candidate["layout"] == "face" for candidate in document["candidates"])


def test_layout_policy_flag_beats_config(tmp_path, monkeypatch, capsys):
    edit, words = paired_edit(tmp_path)
    config = write_config(tmp_path, "off")
    monkeypatch.setenv("YT_CONFIG", str(config))
    client = AutoScoreClient()
    code = run_main(
        tmp_path, monkeypatch, client, edit, words,
        extra=["--layout-policy", "on", "--json"],
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["layout_policy"] == "on"
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    assert all(candidate["layout"] == "split" for candidate in document["candidates"])


def test_bad_layout_policy_flag_exit_2(tmp_path, monkeypatch, capsys):
    edit, words = paired_edit(tmp_path)
    client = AutoScoreClient()
    code = run_main(
        tmp_path, monkeypatch, client, edit, words,
        extra=["--layout-policy", "bogus"],
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "bogus" in err
    assert "Traceback" not in err
    assert not (tmp_path / "shorts_candidates.json").exists()


def test_bad_layout_policy_config_exit_2(tmp_path, monkeypatch, capsys):
    edit, words = paired_edit(tmp_path)
    config = write_config(tmp_path, "bogus")
    monkeypatch.setenv("YT_CONFIG", str(config))
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 2
    err = capsys.readouterr().err
    assert "bogus" in err
    assert "Traceback" not in err
    assert not (tmp_path / "shorts_candidates.json").exists()


def test_layout_policy_timeline_words_is_face(tmp_path, monkeypatch, capsys):
    fcproj, words_dir = long_fcproj_fixture(tmp_path)
    args = shorts_rank.build_parser().parse_args([
        "--fcproj", str(fcproj), "--sequence", "Main", "--words-dir", str(words_dir),
    ])
    mapped, _, _ = shorts_rank.resolve_inputs(args)
    timeline = tmp_path / "timeline_words.json"
    timeline.write_text(json.dumps(mapped, ensure_ascii=False), encoding="utf-8")
    client = AutoScoreClient()
    patch(monkeypatch, client)
    out = tmp_path / "shorts_candidates.json"
    code = shorts_rank.main(["--timeline-words", str(timeline), "--out", str(out), "--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["layout_policy"] == "auto"
    document = json.loads(out.read_text(encoding="utf-8"))
    validate_or_raise(document, "shorts_candidates")
    assert document["candidates"]
    assert all(candidate["layout"] == "face" for candidate in document["candidates"])


def test_json_reports_layout_in_both_cases(tmp_path, monkeypatch, capsys):
    edit, words = short_fixture(tmp_path)
    client = AutoScoreClient()
    patch(monkeypatch, client)
    assert shorts_rank.main([str(edit), str(words), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["layout"] is None
    assert shorts_rank.main([str(edit), str(words), "--layout", "face", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["layout"] == "face"


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
    groups = shorts_rank.build_groups(document)
    words_map, single = shorts_rank.build_words_map(words)
    mapped, warnings, _dropped = shorts_rank.map_words(groups, words_map, single)
    assert warnings == []
    assert len(mapped) == 3
    assert [round(item["start"], 3) for item in mapped] == [0.15, 1.15, 1.65]


def test_scoring_and_ranking(tmp_path, monkeypatch, capsys):
    edit, words = spread_fixture(tmp_path)
    client = AutoScoreClient(scores={index: ((index * 7) % 100, (index * 7) % 100) for index in range(1, 20)})
    code = run_main(tmp_path, monkeypatch, client, edit, words, extra=["--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["candidates_scored"] == 12
    assert summary["written"] == 5
    assert summary["outputs"]["candidates"] == str(tmp_path / "shorts_candidates.json")
    assert summary["wall_time_s"] >= 0
    assert summary["llm_cost_usd"] == 0.0
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    validate_or_raise(document, "shorts_candidates")
    candidates = document["candidates"]
    assert [candidate["rank"] for candidate in candidates] == [1, 2, 3, 4, 5]
    scores = [candidate["score"] for candidate in candidates]
    assert scores == sorted(scores, reverse=True)
    for candidate in candidates:
        assert candidate["score"] == (candidate["hook"] + candidate["payoff"]) / 2
        assert candidate["duration_s"] == round(candidate["end_s"] - candidate["start_s"], 3)


def test_tie_break_earlier_start(tmp_path, monkeypatch, capsys):
    edit, words = spread_fixture(tmp_path)
    client = AutoScoreClient(default=(50, 50))
    code = run_main(tmp_path, monkeypatch, client, edit, words, extra=["--json"])
    assert code == 0
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    starts = [candidate["start_s"] for candidate in document["candidates"]]
    assert starts == sorted(starts)
    assert starts == [70.0, 170.0, 270.0, 370.0, 470.0]


@pytest.mark.parametrize("start_s,expected", [
    (0.0, False),
    (10.0, False),
    (50.0, False),
    (59.9, False),
    (60.0, True),
    (70.0, True),
    (470.0, True),
])
def test_is_standalone_table(start_s, expected):
    assert shorts_rank.is_standalone(start_s) is expected


def test_intro_span_crossing_60_is_dropped(tmp_path):
    edit = one_slot_edit(tmp_path, out_s=120.0)
    words = write_sentence_words(tmp_path / "words" / "cam.json", [
        (50.0, 55.0), (56.0, 61.0), (62.0, 67.0), (68.0, 73.0), (74.0, 79.0), (80.0, 85.0),
    ])
    candidates = candidates_of(edit, words)
    assert candidates
    assert all(candidate["start_s"] >= shorts_rank.INTRO_END_S for candidate in candidates)
    assert not any(candidate["start_s"] == 50.0 for candidate in candidates)


def test_build_candidates_never_intro(tmp_path):
    edit, words = long_fixture(tmp_path)
    candidates = candidates_of(edit, words)
    assert candidates
    for candidate in candidates:
        assert candidate["start_s"] >= shorts_rank.INTRO_END_S


def test_build_candidates_drops_intro_only(tmp_path):
    edit = one_slot_edit(tmp_path, out_s=40.0)
    words = write_sentence_words(tmp_path / "words" / "cam.json", INTRO_ONLY_SENTENCES)
    assert candidates_of(edit, words) == []


def test_only_intro_candidates_exit_2(tmp_path, monkeypatch, capsys):
    edit = one_slot_edit(tmp_path, out_s=40.0)
    words = write_sentence_words(tmp_path / "words" / "cam.json", INTRO_ONLY_SENTENCES)
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 2
    err = capsys.readouterr().err
    assert "standalone" in err
    assert "Traceback" not in err
    assert not (tmp_path / "shorts_candidates.json").exists()


def test_json_reports_dropped_intro(tmp_path, monkeypatch, capsys):
    edit, words = long_fixture(tmp_path)
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, edit, words, extra=["--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["dropped_intro"] >= 1


def make_candidate(start_s, end_s):
    return {
        "start_s": start_s,
        "end_s": end_s,
        "chapter_start_s": 0.0,
        "chapter_title": None,
        "text": "x",
    }


def make_score(hook, payoff):
    return {
        "hook": hook,
        "payoff": payoff,
        "reason": "r",
        "title": "t",
        "description": "a. b.",
    }


def test_overlap_cap_exactly_20_percent_kept():
    candidates = [make_candidate(0.0, 100.0), make_candidate(80.0, 180.0)]
    scores = {1: make_score(50, 50), 2: make_score(50, 50)}
    accepted, dropped = shorts_rank.rank_candidates(candidates, scores)
    assert len(accepted) == 2
    assert dropped == 0


def test_overlap_cap_over_20_percent_dropped():
    candidates = [make_candidate(0.0, 100.0), make_candidate(79.0, 179.0)]
    scores = {1: make_score(50, 50), 2: make_score(50, 50)}
    accepted, dropped = shorts_rank.rank_candidates(candidates, scores)
    assert len(accepted) == 1
    assert dropped == 1


def test_overlap_cap_fills_with_next_distinct(tmp_path, monkeypatch, capsys):
    edit, words = spread_fixture(tmp_path)
    client = AutoScoreClient(scores={1: (100, 100), 2: (90, 90)})
    code = run_main(tmp_path, monkeypatch, client, edit, words, extra=["--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["written"] == 5
    assert summary["dropped_overlap"] >= 1
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    validate_or_raise(document, "shorts_candidates")
    starts = [candidate["start_s"] for candidate in document["candidates"]]
    assert 70.0 in starts
    assert 76.0 not in starts
    for index, first in enumerate(document["candidates"]):
        for second in document["candidates"][index + 1:]:
            overlap_s = max(
                0.0, min(first["end_s"], second["end_s"]) - max(first["start_s"], second["start_s"])
            )
            assert overlap_s / min(first["duration_s"], second["duration_s"]) <= 0.2 + 1e-9


def test_title_and_description_written(tmp_path, monkeypatch, capsys):
    edit, words = short_fixture(tmp_path)
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, edit, words, extra=["--json"])
    assert code == 0
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    validate_or_raise(document, "shorts_candidates")
    for candidate in document["candidates"]:
        assert candidate["title"]
        assert len(candidate["title"]) <= 60
        assert candidate["description"]
        assert len(candidate["description"]) <= 400


def test_description_sentence_rule_is_a_warning_json(tmp_path, monkeypatch, capsys):
    edit, words = short_fixture(tmp_path)
    client = AutoScoreClient(description="O singura propozitie")
    code = run_main(tmp_path, monkeypatch, client, edit, words, extra=["--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert any(
        "description should be exactly 2 sentences (found 1)" in warning
        for warning in summary["warnings"]
    )
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    validate_or_raise(document, "shorts_candidates")
    assert document["candidates"]
    assert all(
        candidate["description"] == "O singura propozitie"
        for candidate in document["candidates"]
    )


def test_description_sentence_rule_is_a_warning_printed(tmp_path, monkeypatch, capsys):
    edit, words = short_fixture(tmp_path)
    client = AutoScoreClient(description="O singura propozitie. Si a doua. Si a treia.")
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 0
    out = capsys.readouterr().out
    assert "warning: candidate 1: description should be exactly 2 sentences (found 3)" in out
    assert "written:" in out
    assert (tmp_path / "shorts_candidates.json").is_file()


def test_title_too_long_exit_2(tmp_path, monkeypatch, capsys):
    edit, words = short_fixture(tmp_path)
    long_title = "x" * 61
    rows = [
        {"id": candidate_id, "hook": 50, "payoff": 50, "reason": "r",
         "title": long_title, "description": "a. b."}
        for candidate_id in (1, 2, 3)
    ]
    client = SequenceClient([json.dumps({"scores": rows})])
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 2
    assert client.calls == 2
    err = capsys.readouterr().err
    assert "1" in err
    assert "title" in err
    assert "Traceback" not in err
    assert not (tmp_path / "shorts_candidates.json").exists()


def test_invalid_then_valid_retry(tmp_path, monkeypatch):
    edit, words = short_fixture(tmp_path)
    invalid = json.dumps({"scores": [
        {"id": 1, "hook": 50, "payoff": 50, "reason": "r", "title": "", "description": "a. b."},
        {"id": 2, "hook": 50, "payoff": 50, "reason": "r", "title": "t", "description": "a. b."},
        {"id": 3, "hook": 50, "payoff": 50, "reason": "r", "title": "t", "description": "a. b."},
    ]})
    valid = json.dumps({"scores": [
        {"id": 1, "hook": 50, "payoff": 50, "reason": "r", "title": "t", "description": "a. b."},
        {"id": 2, "hook": 50, "payoff": 50, "reason": "r", "title": "t", "description": "a. b."},
        {"id": 3, "hook": 50, "payoff": 50, "reason": "r", "title": "t", "description": "a. b."},
    ]})
    client = SequenceClient([invalid, valid])
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 0
    assert client.calls == 2
    assert (tmp_path / "shorts_candidates.json").is_file()


def test_non_json_stdout_ranked_list(tmp_path, monkeypatch, capsys):
    edit, words = spread_fixture(tmp_path)
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if re.match(r"^\d+\. ", line)]
    assert len(lines) == 5
    assert [int(line.split(".")[0]) for line in lines] == [1, 2, 3, 4, 5]
    assert "Titlu" in lines[0]
    assert "written:" in out


def test_retry_once_on_unparseable_output(tmp_path, monkeypatch):
    edit, words = short_fixture(tmp_path)
    valid = json.dumps({"scores": [
        {"id": 1, "hook": 80, "payoff": 70, "reason": "a", "title": "unu", "description": "unu. doi."},
        {"id": 2, "hook": 60, "payoff": 50, "reason": "b", "title": "doi", "description": "unu. doi."},
        {"id": 3, "hook": 40, "payoff": 30, "reason": "c", "title": "trei", "description": "unu. doi."},
    ]})
    client = SequenceClient(["not json at all", valid])
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 0
    assert client.calls == 2
    assert (tmp_path / "shorts_candidates.json").is_file()


def test_two_unparseable_outputs_exit_2(tmp_path, monkeypatch, capsys):
    edit, words = short_fixture(tmp_path)
    client = SequenceClient(["nope", "still nope"])
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 2
    assert client.calls == 2
    err = capsys.readouterr().err
    assert "retry" in err
    assert "Traceback" not in err


def test_missing_score_row_exit_2(tmp_path, monkeypatch, capsys):
    edit, words = short_fixture(tmp_path)
    client = AutoScoreClient(omit={3})
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 2
    err = capsys.readouterr().err
    assert "3" in err
    assert "Traceback" not in err


def test_out_of_range_hook_exit_2(tmp_path, monkeypatch, capsys):
    edit, words = short_fixture(tmp_path)
    client = AutoScoreClient(scores={1: (150, 70), 2: (60, 50), 3: (40, 30)})
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 2
    err = capsys.readouterr().err
    assert "1" in err
    assert "Traceback" not in err


def test_unknown_score_rows_are_ignored(tmp_path, monkeypatch, capsys):
    edit, words = short_fixture(tmp_path)
    text = json.dumps({"scores": [
        {"id": 99, "hook": 10, "payoff": 10, "reason": "noise", "title": "x", "description": "a. b."},
        {"id": 1, "hook": 80, "payoff": 70, "reason": "a", "title": "unu", "description": "a. b."},
        {"id": 2, "hook": 60, "payoff": 50, "reason": "b", "title": "doi", "description": "a. b."},
        {"id": 3, "hook": 40, "payoff": 30, "reason": "c", "title": "trei", "description": "a. b."},
    ]})
    client = SequenceClient([text])
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 0
    document = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    assert [candidate["rank"] for candidate in document["candidates"]] == [1, 2, 3]


def test_empty_kept_transcript_exit_2(tmp_path, monkeypatch, capsys):
    edit = one_slot_edit(tmp_path, out_s=40.0)
    words = write_words(tmp_path / "words" / "cam.json", [("unu", 50.0, 51.0)])
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 2
    err = capsys.readouterr().err
    assert "empty" in err
    assert "Traceback" not in err


def test_rerun_is_atomic(tmp_path, monkeypatch):
    edit, words = short_fixture(tmp_path)
    client = AutoScoreClient()
    patch(monkeypatch, client)
    assert shorts_rank.main([str(edit), str(words)]) == 0
    first = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    assert shorts_rank.main([str(edit), str(words)]) == 0
    second = json.loads((tmp_path / "shorts_candidates.json").read_text(encoding="utf-8"))
    assert {k: v for k, v in first.items() if k != "generated_at"} == {
        k: v for k, v in second.items() if k != "generated_at"
    }
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob("*.part")) == []


def test_missing_edit_exit_2(tmp_path, monkeypatch, capsys):
    words = write_sentence_words(tmp_path / "words" / "cam.json", SHORT_SENTENCES)
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, tmp_path / "nope.json", words)
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_non_linear_slot_exit_2(tmp_path, monkeypatch, capsys):
    edit = write_edit(
        tmp_path / "edit.json",
        [{"id": "v1", "clip": "cam.mp4", "in_s": 0.0, "out_s": 40.0, "score": 1.0,
          "reason": "a", "middle_speed": 1.2}],
    )
    words = write_sentence_words(tmp_path / "words" / "cam.json", SHORT_SENTENCES)
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, edit, words)
    assert code == 2
    assert "middle_speed" in capsys.readouterr().err


def test_invalid_chapters_exit_2(tmp_path, monkeypatch, capsys):
    edit, words = short_fixture(tmp_path)
    bad = tmp_path / "bad_chapters.json"
    bad.write_text('{"chapters": []}', encoding="utf-8")
    client = AutoScoreClient()
    code = run_main(tmp_path, monkeypatch, client, edit, words, extra=["--chapters", str(bad)])
    assert code == 2
    assert "chapters" in capsys.readouterr().err


def write_fcproj(path, sequence, clips):
    items = {}
    track_items = []
    for index, clip in enumerate(clips, 1):
        items[str(index)] = {
            "id": index,
            "name": clip["name"],
            "kind": {"Media": {"media": {"File": {"path": clip["path"]}}}},
        }
        track_items.append({
            "id": 100 + index,
            "item": index,
            "name": clip["name"],
            "start": int(clip["start_s"] * TICKS),
            "duration": int(clip["duration_s"] * TICKS),
            "source_in": int(clip["source_in_s"] * TICKS),
            "speed": 1.0,
            "reverse": False,
            "enabled": True,
        })
    items["10"] = {
        "id": 10,
        "name": sequence,
        "kind": {"Sequence": {"audio_tracks": [{"name": "Audio 1", "items": track_items}]}},
    }
    path.write_text(
        json.dumps({"format": "filmcraft-project", "schema_version": 1, "project": {"items": items}}),
        encoding="utf-8",
    )
    return path


def long_fcproj_fixture(tmp_path):
    fcproj = write_fcproj(
        tmp_path / "long.fcproj", "Main",
        [{"name": "cam.mp4", "path": "/footage/cam.mp4",
          "start_s": 0.0, "duration_s": 110.0, "source_in_s": 0.0}],
    )
    words_dir = tmp_path / "words"
    write_sentence_words(words_dir / "cam.json", LONG_SENTENCES)
    return fcproj, words_dir


def test_fcproj_mode_matches_subs_from_edit(tmp_path, monkeypatch, capsys):
    fcproj, words_dir = long_fcproj_fixture(tmp_path)
    out_dir = tmp_path / "sfe"
    rc = subs_from_edit.main([
        "--fcproj", str(fcproj), "--sequence", "Main",
        "--words-dir", str(words_dir), "--out-dir", str(out_dir), "--json",
    ])
    assert rc == 0
    capsys.readouterr()
    expected = json.loads((out_dir / "Main_words.json").read_text(encoding="utf-8"))

    client = AutoScoreClient()
    patch(monkeypatch, client)
    out = tmp_path / "shorts_candidates.json"
    code = shorts_rank.main([
        "--fcproj", str(fcproj), "--sequence", "Main", "--words-dir", str(words_dir),
        "--out", str(out), "--json",
    ])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["candidates_scored"] > 0
    assert 1 <= summary["written"] <= min(5, summary["candidates_scored"])
    assert summary["warnings"] == []
    assert summary["wall_time_s"] >= 0
    assert summary["llm_cost_usd"] == 0.0
    document = json.loads(out.read_text(encoding="utf-8"))
    validate_or_raise(document, "shorts_candidates")

    args = shorts_rank.build_parser().parse_args([
        "--fcproj", str(fcproj), "--sequence", "Main", "--words-dir", str(words_dir),
    ])
    mapped, warnings, primary = shorts_rank.resolve_inputs(args)
    assert warnings == []
    assert mapped == expected
    assert primary == fcproj


def test_timeline_words_mode_matches_fcproj(tmp_path, monkeypatch, capsys):
    fcproj, words_dir = long_fcproj_fixture(tmp_path)
    args = shorts_rank.build_parser().parse_args([
        "--fcproj", str(fcproj), "--sequence", "Main", "--words-dir", str(words_dir),
    ])
    mapped, _, _ = shorts_rank.resolve_inputs(args)
    timeline = tmp_path / "timeline_words.json"
    timeline.write_text(json.dumps(mapped, ensure_ascii=False), encoding="utf-8")

    client = AutoScoreClient()
    patch(monkeypatch, client)
    out = tmp_path / "shorts_candidates.json"
    code = shorts_rank.main(["--timeline-words", str(timeline), "--out", str(out), "--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["candidates_scored"] > 0
    assert 1 <= summary["written"] <= min(5, summary["candidates_scored"])
    assert summary["warnings"] == []
    assert summary["wall_time_s"] >= 0
    assert summary["llm_cost_usd"] == 0.0
    document = json.loads(out.read_text(encoding="utf-8"))
    validate_or_raise(document, "shorts_candidates")

    args = shorts_rank.build_parser().parse_args(["--timeline-words", str(timeline)])
    mapped2, warnings2, primary = shorts_rank.resolve_inputs(args)
    assert warnings2 == []
    assert mapped2 == mapped
    assert primary == timeline
    assert shorts_rank.build_candidates(mapped2, [{"start_s": 0.0, "title": None}]) == \
        shorts_rank.build_candidates(mapped, [{"start_s": 0.0, "title": None}])


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
    client = AutoScoreClient()
    patch(monkeypatch, client)
    code = shorts_rank.main(argv)
    assert code == 2
    err = capsys.readouterr().err
    assert "[error]" in err
    assert "Traceback" not in err


def test_mode_error_json_reports_not_ok(capsys):
    code = shorts_rank.main(["--json"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]


def test_help_lists_three_modes():
    text = shorts_rank.build_parser().format_help()
    assert "rough cut" in text
    assert "final project" in text
    assert "timeline words" in text


@pytest.mark.skipif(not safe_is_dir(REFERENCE_DIR), reason="reference material not present")
def test_reference_final_project(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("YT_CONFIG", str(MOCK_CONFIG))
    monkeypatch.setattr(shorts_rank, "_client", None)
    chapters_out = tmp_path / "chapters.json"
    chapters_out.write_text(json.dumps({
        "schema_version": "0.1",
        "generated_at": "2026-10-21T00:00:00+00:00",
        "tool": "scripts/chapters.py",
        "chapters": [{"start_s": 0.0, "title": "Intro", "summary": "Start."}],
    }), encoding="utf-8")
    out = tmp_path / "shorts_candidates.json"
    code = shorts_rank.main([
        "--fcproj", str(REFERENCE_FCPROJ), "--sequence", REFERENCE_SEQUENCE,
        "--words-dir", str(REFERENCE_WORDS), "--chapters", str(chapters_out),
        "--out", str(out), "--json",
    ])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["written"] <= 5
    assert summary["wall_time_s"] >= 0
    assert summary["llm_cost_usd"] == 0.0
    document = json.loads(out.read_text(encoding="utf-8"))
    validate_or_raise(document, "shorts_candidates")
    for candidate in document["candidates"]:
        assert 0 <= candidate["start_s"] <= REFERENCE_END_S
        assert 0 <= candidate["end_s"] <= REFERENCE_END_S
        assert 20.0 <= candidate["end_s"] - candidate["start_s"] <= 60.0
        assert candidate["chapter_start_s"] == 0.0
    for index, first in enumerate(document["candidates"]):
        for second in document["candidates"][index + 1:]:
            overlap_s = max(
                0.0, min(first["end_s"], second["end_s"]) - max(first["start_s"], second["start_s"])
            )
            assert overlap_s / min(first["duration_s"], second["duration_s"]) <= 0.2 + 1e-9


def test_fcproj_default_out_is_the_publish_folder(tmp_path, monkeypatch, capsys):
    fcproj, words_dir = long_fcproj_fixture(tmp_path)
    clip = tmp_path / "clip"
    moved_words = clip / "json"
    moved_words.mkdir(parents=True)
    for source in words_dir.glob("*.json"):
        (moved_words / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    moved_fcproj = tmp_path / "other" / "Second Brain.fcproj"
    moved_fcproj.parent.mkdir(parents=True)
    moved_fcproj.write_text(fcproj.read_text(encoding="utf-8"), encoding="utf-8")
    client = AutoScoreClient()
    patch(monkeypatch, client)
    code = shorts_rank.main([
        "--fcproj", str(moved_fcproj), "--sequence", "Main", "--words-dir", str(moved_words), "--json",
    ])
    assert code == 0
    assert (clip / "shorts_candidates.json").is_file()
    assert not (moved_fcproj.parent / "shorts_candidates.json").exists()
