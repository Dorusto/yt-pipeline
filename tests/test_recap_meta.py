import json
import os
from pathlib import Path
from types import SimpleNamespace

import recap_meta
from common.llm import MockClient
from common.schema import validate_or_raise
from common.exit_codes import EXIT_OK, EXIT_USAGE, EXIT_RUNTIME

CONFIG = {
    "models": {"metadata": "mock-model"},
    "llm_budget_usd": None,
    "llm_backend": "mock",
}

VALID = {
    "title": "O zi pe munte",
    "description": "Prima propoziție. A doua propoziție.",
    "hashtags": ["#munte", "#drumeție", "#recap", "#vara", "#drone"],
}


class FakeClient:
    """A client returning a different canned text per call, for the retry tests."""

    def __init__(self, texts, cost_per_call=0.0):
        self._texts = list(texts)
        self.calls = 0
        self.spent_usd = 0.0
        self.cost_per_call = cost_per_call
        self.model = "fake-model"

    def complete(self, role, messages, temperature=None, **kwargs):
        index = min(self.calls, len(self._texts) - 1)
        self.calls += 1
        self.spent_usd = round(self.spent_usd + self.cost_per_call, 8)
        return SimpleNamespace(
            text=self._texts[index], cost_usd=self.cost_per_call, model=self.model
        )


def patch(monkeypatch, client, config=CONFIG):
    monkeypatch.setattr(recap_meta, "get_client", lambda config=None: client)
    monkeypatch.setattr(recap_meta, "load_config", lambda: config)


def write_edit(path, brief_text="a calm recap", descriptions=("a wide shot of the valley",)):
    video = []
    for index, description in enumerate(descriptions):
        video.append(
            {
                "id": f"v{index + 1}",
                "clip": "cam.mp4",
                "in_s": 0.0,
                "out_s": 2.0,
                "score": 1.0,
                "reason": "test",
                "description": description,
            }
        )
    document = {
        "schema_version": "0.1",
        "generated_at": "2026-10-21T00:00:00+00:00",
        "tool": "tests/test_recap_meta.py",
        "brief": {"text": brief_text},
        "sources": [
            {"file": "cam.mp4", "path": "/x/cam.mp4", "duration_s": 10.0, "media_type": "video"}
        ],
        "video": video,
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_dry_run_writes_nothing_and_calls_no_model(tmp_path, monkeypatch, capsys):
    write_edit(tmp_path / "edit.json")
    client = FakeClient([json.dumps(VALID)])
    patch(monkeypatch, client)
    code = recap_meta.main(["--workspace", str(tmp_path), "--dry-run", "--json"])
    assert code == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "dry_run"
    assert summary["message"]
    assert summary["llm_cost_usd"] == 0.0
    assert "title" not in summary
    assert "hashtags" not in summary
    assert "model" not in summary
    assert client.calls == 0
    assert not (tmp_path / "recap_meta.json").exists()


def test_dry_run_message_has_the_sections(tmp_path, monkeypatch, capsys):
    write_edit(
        tmp_path / "edit.json",
        brief_text="a calm recap",
        descriptions=("a wide shot of the valley",),
    )
    client = FakeClient([json.dumps(VALID)])
    patch(monkeypatch, client)
    assert recap_meta.main(["--workspace", str(tmp_path), "--dry-run", "--json"]) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    message = summary["message"]
    assert "## Brief" in message
    assert "a calm recap" in message
    assert "## Facts" in message
    assert "Place: (unknown)" in message
    assert "Date: (unknown)" in message
    assert "## Shots" in message
    assert "- a wide shot of the valley" in message


def test_valid_answer_writes_and_validates(tmp_path, monkeypatch, capsys):
    write_edit(tmp_path / "edit.json")
    client = MockClient(text=json.dumps(VALID), config=CONFIG, cost_per_call=0.002)
    patch(monkeypatch, client)
    code = recap_meta.main(["--workspace", str(tmp_path), "--json"])
    assert code == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "written"
    assert summary["title"] == VALID["title"]
    assert summary["hashtags"] == len(VALID["hashtags"])
    assert summary["llm_cost_usd"] == 0.002
    document = json.loads((tmp_path / "recap_meta.json").read_text(encoding="utf-8"))
    validate_or_raise(document, "recap_meta")
    assert document["title"] == VALID["title"]
    assert document["description"] == VALID["description"]
    assert document["hashtags"] == VALID["hashtags"]
    assert document["model"] == "mock-model"
    assert document["llm_cost_usd"] == 0.002
    assert document["missing"] == ["place name", "date"]


def test_two_invalid_answers_exit_3(tmp_path, monkeypatch, capsys):
    write_edit(tmp_path / "edit.json")
    client = FakeClient(["nope", "still nope"])
    patch(monkeypatch, client)
    code = recap_meta.main(["--workspace", str(tmp_path)])
    assert code == EXIT_RUNTIME
    assert client.calls == 2
    err = capsys.readouterr().err
    assert "retry" in err
    assert "Traceback" not in err
    assert not (tmp_path / "recap_meta.json").exists()


def test_bad_then_good_succeeds_on_retry(tmp_path, monkeypatch, capsys):
    write_edit(tmp_path / "edit.json")
    client = FakeClient(["not json at all", json.dumps(VALID)])
    patch(monkeypatch, client)
    code = recap_meta.main(["--workspace", str(tmp_path), "--json"])
    assert code == EXIT_OK
    assert client.calls == 2
    assert (tmp_path / "recap_meta.json").is_file()


def test_missing_place_name_when_brief_has_no_place_line(tmp_path, monkeypatch, capsys):
    write_edit(tmp_path / "edit.json", brief_text="a calm recap")
    client = MockClient(text=json.dumps(VALID), config=CONFIG)
    patch(monkeypatch, client)
    assert recap_meta.main(["--workspace", str(tmp_path), "--json"]) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert "place name" in summary["missing"]


def test_place_line_removes_the_missing_hint(tmp_path, monkeypatch, capsys):
    write_edit(
        tmp_path / "edit.json",
        brief_text="a calm recap\nWhere and when: Retezat, July",
    )
    client = MockClient(text=json.dumps(VALID), config=CONFIG)
    patch(monkeypatch, client)
    assert recap_meta.main(["--workspace", str(tmp_path), "--json"]) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert "place name" not in summary["missing"]


def test_date_comes_from_ingest(tmp_path, monkeypatch, capsys):
    write_edit(tmp_path / "edit.json")
    (tmp_path / "ingest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-11T00:00:00+00:00",
                "source_dir": "/x",
                "clips": [
                    {
                        "file": "a.mp4",
                        "path": "/x/a.mp4",
                        "duration_s": 1.0,
                        "streams": [],
                        "gyro": False,
                        "creation_time": "2026-07-10T10:00:00+00:00",
                    },
                    {
                        "file": "b.mp4",
                        "path": "/x/b.mp4",
                        "duration_s": 1.0,
                        "streams": [],
                        "gyro": False,
                        "creation_time": "2026-07-08T10:00:00+00:00",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    client = MockClient(text=json.dumps(VALID), config=CONFIG)
    patch(monkeypatch, client)
    assert recap_meta.main(["--workspace", str(tmp_path), "--json"]) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert "date" not in summary["missing"]
    document = json.loads((tmp_path / "recap_meta.json").read_text(encoding="utf-8"))
    assert document["missing"] == ["place name"]


def test_second_run_is_skipped_and_force_rewrites(tmp_path, monkeypatch, capsys):
    write_edit(tmp_path / "edit.json")
    client = MockClient(text=json.dumps(VALID), config=CONFIG)
    patch(monkeypatch, client)
    assert recap_meta.main(["--workspace", str(tmp_path), "--json"]) == EXIT_OK
    capsys.readouterr()
    out = tmp_path / "recap_meta.json"
    os.utime(out, (out.stat().st_mtime + 5, out.stat().st_mtime + 5))

    skipped_client = FakeClient([json.dumps(VALID)])
    patch(monkeypatch, skipped_client)
    assert recap_meta.main(["--workspace", str(tmp_path), "--json"]) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "skipped"
    assert skipped_client.calls == 0

    forced_client = FakeClient([json.dumps(VALID)])
    patch(monkeypatch, forced_client)
    assert recap_meta.main(["--workspace", str(tmp_path), "--force", "--json"]) == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "written"
    assert forced_client.calls == 1


def test_missing_edit_exit_2(tmp_path, capsys):
    code = recap_meta.main(["--workspace", str(tmp_path)])
    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "[error]" in err
    assert "Traceback" not in err


def test_missing_edit_json_reports_not_ok(tmp_path, capsys):
    code = recap_meta.main(["--workspace", str(tmp_path), "--json"])
    assert code == EXIT_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]
    assert payload["exit_code"] == 2


def test_workspace_not_a_directory_exit_2(tmp_path, capsys):
    code = recap_meta.main(["--workspace", str(tmp_path / "nope")])
    assert code == EXIT_USAGE
    assert "[error]" in capsys.readouterr().err


def test_missing_prompt_exit_2(tmp_path, monkeypatch, capsys):
    write_edit(tmp_path / "edit.json")
    client = MockClient(text=json.dumps(VALID), config=CONFIG)
    patch(monkeypatch, client)
    code = recap_meta.main(
        ["--workspace", str(tmp_path), "--prompt", str(tmp_path / "nope.md")]
    )
    assert code == EXIT_USAGE
    assert "prompt file not found" in capsys.readouterr().err


def test_help_lists_the_flags():
    text = recap_meta.build_parser().format_help()
    for flag in ("--workspace", "--out", "--prompt", "--dry-run", "--force", "--budget-usd", "--json"):
        assert flag in text
    assert "examples:" in text
