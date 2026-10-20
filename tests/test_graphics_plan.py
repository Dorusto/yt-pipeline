import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import graphics_plan
from common.llm import MockClient
from common.schema import validate

ROOT = Path(__file__).resolve().parents[1]

FLAT_WORDS = [
    {"word": "doi", "start": 0.2, "end": 0.6},
    {"word": "trei", "start": 0.7, "end": 1.1},
    {"word": "patru", "start": 1.2, "end": 1.6},
    {"word": "cinci", "start": 1.7, "end": 2.1},
    {"word": "șase", "start": 6.0, "end": 6.4},
    {"word": "șapte", "start": 6.5, "end": 6.9},
    {"word": "opt", "start": 9.0, "end": 9.4},
    {"word": "nouă", "start": 9.5, "end": 9.9},
]

WHISPER_WORDS = {
    "text": "doi trei patru cinci șase șapte opt nouă",
    "segments": [
        {"id": 0, "start": 0.2, "end": 2.1, "text": "doi trei patru cinci", "words": FLAT_WORDS[:4]},
        {"id": 1, "start": 6.0, "end": 9.9, "text": "șase șapte opt nouă", "words": FLAT_WORDS[4:]},
    ],
}

CONFIG = {
    "models": {"graphics": "deepseek/deepseek-v4.1-flash"},
    "llm_budget_usd": None,
    "llm_backend": "mock",
}


class CountingClient(MockClient):
    """MockClient that counts how many completions were requested."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = 0
        self.messages = []

    def complete(self, role, messages, model=None, **kwargs):
        self.calls += 1
        self.messages.append(messages)
        return super().complete(role, messages, model=model, **kwargs)


def patch(monkeypatch, client, config=CONFIG):
    monkeypatch.setattr(graphics_plan, "load_config", lambda: config)
    monkeypatch.setattr(graphics_plan, "make_client", lambda config=None, ledger_path=None: client)


def write_words(tmp_path, payload=FLAT_WORDS):
    path = tmp_path / "words.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def write_markers(tmp_path, payload):
    path = tmp_path / "markers.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def model_text(entries):
    return json.dumps({"entries": entries})


def test_both_shapes_flatten_to_same_words(tmp_path):
    flat = write_words(tmp_path)
    whisper = tmp_path / "whisper.json"
    whisper.write_text(json.dumps(WHISPER_WORDS, ensure_ascii=False), encoding="utf-8")
    flat_words, flat_shape = graphics_plan.load_words(flat)
    whisper_words, whisper_shape = graphics_plan.load_words(whisper)
    assert flat_shape == "flat"
    assert whisper_shape == "whisper"
    assert flat_words == whisper_words


@pytest.mark.parametrize(
    "payload,needle",
    [
        ([], "no words"),
        ([{"word": "a", "start": 0.0}], "start/end"),
        ([{"word": "a", "start": "x", "end": 1.0}], "start/end"),
        ([{"word": "", "start": 0.0, "end": 1.0}], "no 'word' text"),
        ({"text": "x"}, "neither"),
    ],
)
def test_bad_words_exit_2(tmp_path, capsys, payload, needle):
    path = write_words(tmp_path, payload)
    rc = graphics_plan.main([str(path)])
    assert rc == 2
    assert needle in capsys.readouterr().err


def test_mango_only_markers(tmp_path):
    path = write_markers(
        tmp_path,
        [
            {"time_s": 1.0, "colour": "Mango", "name": "intro"},
            {"time_s": 2.0, "colour": "Green", "name": "section"},
            {"time_s": 3.0, "colour": "Rose", "name": "redo"},
            {"time_s": 4.0, "colour": "mango", "comment": "lower"},
        ],
    )
    candidates = graphics_plan.load_markers(path)
    assert [candidate["time_s"] for candidate in candidates] == [1.0, 4.0]
    assert candidates[0]["name"] == "intro"
    assert candidates[1]["comment"] == "lower"


def test_markers_object_shape(tmp_path):
    path = write_markers(tmp_path, {"markers": [{"time_s": 1.0, "colour": "Mango"}]})
    assert len(graphics_plan.load_markers(path)) == 1


def test_marker_without_time_exits_2(tmp_path, capsys):
    path = write_markers(tmp_path, [{"colour": "Mango", "name": "x"}])
    rc = graphics_plan.main([str(write_words(tmp_path)), "--markers", str(path)])
    assert rc == 2
    assert "time_s" in capsys.readouterr().err


def test_plan_normalisation(tmp_path, monkeypatch, capsys):
    words = write_words(tmp_path)
    markers = write_markers(tmp_path, [{"time_s": 6.0, "colour": "Mango", "name": "count"}])
    client = CountingClient(
        text=model_text(
            [
                {
                    "time_s": 6.1,
                    "duration_s": 3.0,
                    "template": "counter",
                    "props": {"number": "42", "label": "clips"},
                    "reason": "a number is spoken",
                },
                {
                    "time_s": 1.0,
                    "duration_s": 2.0,
                    "template": "lower_third",
                    "props": {"name": "Doru", "role": "creator"},
                    "reason": "the speaker is named",
                },
            ]
        ),
        config=CONFIG,
    )
    patch(monkeypatch, client)
    out = tmp_path / "plan.json"
    rc = graphics_plan.main([str(words), "--markers", str(markers), "--out", str(out), "--json"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert validate(plan, "graphics_plan") == []
    assert [entry["id"] for entry in plan["entries"]] == ["g1", "g2"]
    assert [entry["time_s"] for entry in plan["entries"]] == [1.0, 6.1]
    assert all(entry["approved"] is False for entry in plan["entries"])
    assert plan["entries"][0]["origin"] == "model"
    assert plan["entries"][1]["origin"] == "marker"
    assert plan["entries"][1]["marker"]["name"] == "count"
    assert plan["entries"][1]["hint"] == "count"
    assert plan["tool"] == "scripts/graphics_plan.py"
    assert plan["source_words"] == str(words)
    assert plan["markers_path"] == str(markers)
    assert summary["entries"] == 2
    assert summary["approved"] == 0
    assert summary["markers_used"] == 1
    assert summary["words_shape"] == "flat"
    assert summary["model"] == "deepseek/deepseek-v4.1-flash"
    assert isinstance(summary["wall_time_s"], (int, float))
    assert summary["llm_cost_usd"] == 0.0


def test_context_and_transcript_reach_the_prompt(tmp_path, monkeypatch):
    words = write_words(tmp_path)
    context = tmp_path / "context.md"
    context.write_text("Angle: test", encoding="utf-8")
    client = CountingClient(text=model_text([]), config=CONFIG)
    patch(monkeypatch, client)
    rc = graphics_plan.main([str(words), "--context", str(context), "--out", str(tmp_path / "plan.json")])
    assert rc == 0
    message = client.messages[0][0]["content"]
    assert "Angle: test" in message
    assert "[00:00] doi trei patru cinci" in message
    assert "## Max entries" in message


def test_invalid_model_json_retries_once_then_exit_2(tmp_path, monkeypatch, capsys):
    words = write_words(tmp_path)
    client = CountingClient(text=model_text([{"time_s": 1.0}]), config=CONFIG)
    patch(monkeypatch, client)
    out = tmp_path / "plan.json"
    rc = graphics_plan.main([str(words), "--out", str(out)])
    assert rc == 2
    assert client.calls == 2
    assert "duration_s" in capsys.readouterr().err
    assert not out.exists()


def test_missing_props_caught(tmp_path, monkeypatch, capsys):
    words = write_words(tmp_path)
    client = CountingClient(
        text=model_text(
            [
                {
                    "time_s": 1.0,
                    "duration_s": 3.0,
                    "template": "lower_third",
                    "props": {"name": "Doru"},
                    "reason": "x",
                }
            ]
        ),
        config=CONFIG,
    )
    patch(monkeypatch, client)
    rc = graphics_plan.main([str(words), "--out", str(tmp_path / "plan.json")])
    assert rc == 2
    assert client.calls == 2
    assert "role" in capsys.readouterr().err


def test_time_outside_span_caught(tmp_path, monkeypatch, capsys):
    words = write_words(tmp_path)
    client = CountingClient(
        text=model_text(
            [
                {
                    "time_s": 20.0,
                    "duration_s": 3.0,
                    "template": "callout",
                    "props": {"text": "hi"},
                    "reason": "x",
                }
            ]
        ),
        config=CONFIG,
    )
    patch(monkeypatch, client)
    rc = graphics_plan.main([str(words), "--out", str(tmp_path / "plan.json")])
    assert rc == 2
    assert "outside" in capsys.readouterr().err


def test_overlap_caught(tmp_path, monkeypatch, capsys):
    words = write_words(tmp_path)
    client = CountingClient(
        text=model_text(
            [
                {
                    "time_s": 1.0,
                    "duration_s": 4.0,
                    "template": "callout",
                    "props": {"text": "a"},
                    "reason": "x",
                },
                {
                    "time_s": 2.0,
                    "duration_s": 4.0,
                    "template": "callout",
                    "props": {"text": "b"},
                    "reason": "y",
                },
            ]
        ),
        config=CONFIG,
    )
    patch(monkeypatch, client)
    rc = graphics_plan.main([str(words), "--out", str(tmp_path / "plan.json")])
    assert rc == 2
    assert "overlap" in capsys.readouterr().err


def test_max_entries_caps(tmp_path, monkeypatch, capsys):
    words = write_words(tmp_path)
    entries = [
        {
            "time_s": float(index),
            "duration_s": 2.0,
            "template": "callout",
            "props": {"text": f"t{index}"},
            "reason": "x",
        }
        for index in range(3)
    ]
    client = CountingClient(text=model_text(entries), config=CONFIG)
    patch(monkeypatch, client)
    out = tmp_path / "plan.json"
    rc = graphics_plan.main([str(words), "--max-entries", "1", "--out", str(out), "--json"])
    assert rc == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert len(plan["entries"]) == 1
    assert plan["entries"][0]["time_s"] == 0.0


def test_max_entries_zero_writes_empty_plan(tmp_path, monkeypatch, capsys):
    words = write_words(tmp_path)
    client = CountingClient(text="{}", config=CONFIG)
    patch(monkeypatch, client)
    out = tmp_path / "plan.json"
    rc = graphics_plan.main([str(words), "--max-entries", "0", "--out", str(out), "--json"])
    assert rc == 0
    assert client.calls == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert plan["entries"] == []
    assert validate(plan, "graphics_plan") == []


def test_negative_max_entries_exit_2(tmp_path, capsys):
    words = write_words(tmp_path)
    rc = graphics_plan.main([str(words), "--max-entries", "-1"])
    assert rc == 2
    assert "max-entries" in capsys.readouterr().err


def test_human_line(tmp_path, monkeypatch, capsys):
    words = write_words(tmp_path)
    client = CountingClient(text=model_text([]), config=CONFIG)
    patch(monkeypatch, client)
    out = tmp_path / "plan.json"
    rc = graphics_plan.main([str(words), "--out", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "plan.json" in text
    assert "approved" in text


def test_help_lists_the_interface(capsys):
    with pytest.raises(SystemExit) as excinfo:
        graphics_plan.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--markers", "--context", "--out", "--max-entries", "--json"):
        assert flag in out


def write_mock_config(tmp_path, plan_json):
    config = {
        "models": {"graphics": "mock/model"},
        "llm_budget_usd": 5.0,
        "llm_backend": "mock",
        "llm_mock_text": {"graphics": plan_json},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path


def test_chain_with_mock_llm(tmp_path):
    words = write_words(tmp_path)
    plan_json = model_text(
        [
            {
                "time_s": 1.0,
                "duration_s": 3.0,
                "template": "lower_third",
                "props": {"name": "Doru", "role": "creator"},
                "reason": "introduces the speaker",
            },
            {
                "time_s": 6.0,
                "duration_s": 2.5,
                "template": "counter",
                "props": {"number": "42", "label": "clips"},
                "reason": "a number is spoken",
            },
        ]
    )
    config_path = write_mock_config(tmp_path, plan_json)
    env = dict(os.environ)
    env["YT_CONFIG"] = str(config_path)
    out = tmp_path / "plan.json"

    run1 = subprocess.run(
        [sys.executable, "scripts/graphics_plan.py", str(words), "--out", str(out), "--json"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
    )
    assert run1.returncode == 0, run1.stderr
    summary1 = json.loads(run1.stdout)
    assert summary1["entries"] == 2
    assert summary1["approved"] == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert validate(plan, "graphics_plan") == []

    plan["entries"][0]["approved"] = True
    out.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    overlays = tmp_path / "overlays"
    run2 = subprocess.run(
        [
            sys.executable,
            "scripts/graphics.py",
            "--plan",
            str(out),
            "--out-dir",
            str(overlays),
            "--width",
            "320",
            "--height",
            "180",
            "--backend",
            "mock",
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
    )
    assert run2.returncode == 0, run2.stderr
    summary2 = json.loads(run2.stdout)
    assert len(summary2["outputs"]) == 1
    assert summary2["outputs"][0]["id"] == "g1"
    assert (overlays / "g1.mov").is_file()
    assert not (overlays / "g2.mov").exists()
