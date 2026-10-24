import copy
import json

import pytest

import metadata
from common.llm import MockClient
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME

SRT = (
    "1\n00:00:00,000 --> 00:00:02,000\nBună ziua\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\nLume\n\n"
)

CONFIG = {
    "models": {"metadata": "deepseek/deepseek-v4.1-flash"},
    "llm_budget_usd": None,
    "llm_backend": "mock",
    "channel": {
        "members": "Andrei, Mihai și Maria",
        "github_url": "https://example.invalid/channel",
        "fixed": {
            "members_sentence": {
                "ro": "Mulțumesc {members} pentru susținere.",
                "en": "Thank you {members} for the support.",
            },
            "closing": {
                "ro": "Pe curând.",
                "en": "See you soon.",
            },
        },
    },
}

CHANNEL_URL = "https://example.invalid/channel"

MEMBERS_RO = "Mulțumesc Andrei, Mihai și Maria pentru susținere."
MEMBERS_EN = "Thank you Andrei, Mihai și Maria for the support."
CLOSING_RO = "Pe curând."
CLOSING_EN = "See you soon."

FULL = {
    "titles": ["Titlu unu", "Titlu doi", "Titlu trei"],
    "intro": {"ro": "Intro RO", "en": "Intro EN"},
    "github_line": {"ro": "GitHub RO", "en": "GitHub EN"},
    "previous_line": {"ro": "Previous RO", "en": "Previous EN"},
    "subscribe_line": {"ro": "Subscribe RO", "en": "Subscribe EN"},
    "promo_lines": [{"ro": "Promo RO", "en": "Promo EN", "label": "affiliate"}],
    "chapters": [
        {"timestamp": "00:00:00", "title": "Intro"},
        {"timestamp": "00:00:02", "title": "Lume"},
    ],
    "tags": ["tag1", "tag2", "tag3"],
    "pinned_comment": "Ce întrebare?",
}


def patch(monkeypatch, client, config=CONFIG):
    monkeypatch.setattr(metadata, "get_client", lambda: client)
    monkeypatch.setattr(metadata, "load_config", lambda: config)


def write_inputs(tmp_path):
    srt = tmp_path / "clip.srt"
    srt.write_text(SRT, encoding="utf-8")
    context = tmp_path / "context.md"
    context.write_text("Angle: test", encoding="utf-8")
    return srt, context


def run_main(tmp_path, monkeypatch, data, extra=None, config=CONFIG, cost_per_call=0.0):
    srt, context = write_inputs(tmp_path)
    client = MockClient(text=json.dumps(data), config=config, cost_per_call=cost_per_call)
    patch(monkeypatch, client, config=config)
    argv = [str(srt), "--context", str(context), "--out-dir", str(tmp_path / "out")]
    if extra:
        argv.extend(extra)
    return metadata.main(argv), tmp_path / "out" / "metadata.md"


def test_end_to_end(tmp_path, monkeypatch):
    code, out = run_main(
        tmp_path,
        monkeypatch,
        FULL,
        extra=["--previous-url", "https://prev", "--repo-url", "https://repo"],
    )
    assert code == 0
    text = out.read_text(encoding="utf-8")

    assert text.count(MEMBERS_RO) == 1
    assert text.count(MEMBERS_EN) == 1
    assert text.count(CLOSING_RO) == 1
    assert text.count(CLOSING_EN) == 1

    ro_start = text.index("## Description RO")
    en_start = text.index("## Description EN")
    assert ro_start < en_start
    ro_block = text[ro_start:en_start]
    order = [
        ro_block.index("Intro RO"),
        ro_block.index("GitHub RO"),
        ro_block.index(MEMBERS_RO),
        ro_block.index("Previous RO"),
        ro_block.index("Subscribe RO"),
        ro_block.index("[affiliate] Promo RO"),
        ro_block.index(CLOSING_RO),
    ]
    assert order == sorted(order)
    assert MEMBERS_EN not in ro_block
    assert CLOSING_EN not in ro_block

    en_block = text[en_start:]
    assert MEMBERS_EN in en_block
    assert CLOSING_EN in en_block
    assert MEMBERS_RO not in en_block
    assert CLOSING_RO not in en_block

    assert "1. Titlu unu" in text
    assert "2. Titlu doi" in text
    assert "3. Titlu trei" in text
    assert "00:00:00 Intro" in text
    assert "00:00:02 Lume" in text
    assert "tag1, tag2, tag3" in text
    assert "Ce întrebare?" in text


def test_null_github_line_omits_paragraph(tmp_path, monkeypatch):
    data = dict(FULL, github_line=None)
    code, out = run_main(tmp_path, monkeypatch, data)
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "GitHub RO" not in text
    assert "GitHub EN" not in text


def test_no_previous_url_drops_previous_line(tmp_path, monkeypatch):
    code, out = run_main(tmp_path, monkeypatch, FULL)
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "Previous RO" not in text
    assert "Previous EN" not in text


def test_missing_closing_en_omits_en_closing(tmp_path, monkeypatch):
    config = copy.deepcopy(CONFIG)
    del config["channel"]["fixed"]["closing"]["en"]
    code, out = run_main(tmp_path, monkeypatch, FULL, config=config)
    assert code == 0
    text = out.read_text(encoding="utf-8")
    ro_start = text.index("## Description RO")
    en_start = text.index("## Description EN")
    assert CLOSING_RO in text[ro_start:en_start]
    assert CLOSING_EN not in text[en_start:]


def test_missing_members_sentence_omits_paragraph(tmp_path, monkeypatch):
    config = copy.deepcopy(CONFIG)
    del config["channel"]["fixed"]["members_sentence"]
    code, out = run_main(tmp_path, monkeypatch, FULL, config=config)
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert MEMBERS_RO not in text
    assert MEMBERS_EN not in text


def test_chapter_title_en_in_json_and_markdown(tmp_path, monkeypatch):
    data = dict(
        FULL,
        chapters=[
            {"timestamp": "00:00:00", "title": "Intro", "title_en": "Intro"},
            {"timestamp": "00:00:02", "title": "Lume", "title_en": "World"},
        ],
    )
    code, out = run_main(tmp_path, monkeypatch, data)
    assert code == 0
    payload = json.loads((tmp_path / "out" / "metadata.json").read_text(encoding="utf-8"))
    assert payload["chapters"] == [
        {"timestamp": "00:00:00", "title": "Intro", "title_en": "Intro"},
        {"timestamp": "00:00:02", "title": "Lume", "title_en": "World"},
    ]
    text = out.read_text(encoding="utf-8")
    assert "## Chapters (EN)" in text
    assert "00:00:02 World" in text


def test_missing_channel_members_exits_2(tmp_path, monkeypatch, capsys):
    config = {
        "models": {"metadata": "deepseek/deepseek-v4.1-flash"},
        "llm_budget_usd": None,
        "llm_backend": "mock",
        "channel": {
            "github_url": "https://example.invalid/channel",
            "fixed": {
                "members_sentence": {
                    "ro": "Mulțumesc {members} pentru susținere.",
                    "en": "Thank you {members} for the support.",
                },
            },
        },
    }
    code, _ = run_main(tmp_path, monkeypatch, FULL, config=config)
    assert code == 2
    err = capsys.readouterr().err
    assert "channel.members" in err
    assert "Traceback" not in err


def test_no_members_placeholder_runs_without_members(tmp_path, monkeypatch):
    config = {
        "models": {"metadata": "deepseek/deepseek-v4.1-flash"},
        "llm_budget_usd": None,
        "llm_backend": "mock",
        "channel": {
            "github_url": "https://example.invalid/channel",
            "fixed": {
                "closing": {
                    "ro": "Pe curând.",
                    "en": "See you soon.",
                },
            },
        },
    }
    code, out = run_main(tmp_path, monkeypatch, FULL, config=config)
    assert code == EXIT_OK
    text = out.read_text(encoding="utf-8")
    assert MEMBERS_RO not in text
    assert MEMBERS_EN not in text
    assert CLOSING_RO in text
    assert CLOSING_EN in text


def test_members_sentence_without_placeholder_runs_without_members(tmp_path, monkeypatch):
    config = {
        "models": {"metadata": "deepseek/deepseek-v4.1-flash"},
        "llm_budget_usd": None,
        "llm_backend": "mock",
        "channel": {
            "github_url": "https://example.invalid/channel",
            "fixed": {
                "members_sentence": {
                    "ro": "Mulțumesc tuturor pentru susținere.",
                    "en": "Thank you all for the support.",
                },
            },
        },
    }
    code, out = run_main(tmp_path, monkeypatch, FULL, config=config)
    assert code == EXIT_OK
    text = out.read_text(encoding="utf-8")
    assert "Mulțumesc tuturor pentru susținere." in text
    assert "Thank you all for the support." in text


def test_empty_title_exits_2(tmp_path, monkeypatch, capsys):
    code, _ = run_main(tmp_path, monkeypatch, FULL, extra=["--title", ""])
    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "--title" in err
    assert "Traceback" not in err


def test_en_chapters_fall_back_to_ro_title(tmp_path, monkeypatch):
    data = dict(
        FULL,
        chapters=[
            {"timestamp": "00:00:00", "title": "Intro", "title_en": "Intro"},
            {"timestamp": "00:00:02", "title": "Lume"},
        ],
    )
    code, out = run_main(tmp_path, monkeypatch, data)
    assert code == 0
    text = out.read_text(encoding="utf-8")
    en_section = text.split("## Chapters (EN)", 1)[1]
    assert "00:00:02 Lume" in en_section


def test_missing_channel_github_url_exits_2(tmp_path, monkeypatch, capsys):
    config = {
        "models": {"metadata": "deepseek/deepseek-v4.1-flash"},
        "llm_budget_usd": None,
        "llm_backend": "mock",
        "channel": {"members": "Andrei, Mihai și Maria"},
    }
    code, _ = run_main(tmp_path, monkeypatch, FULL, config=config)
    assert code == 2
    err = capsys.readouterr().err
    assert "channel.github_url" in err
    assert "Traceback" not in err


class RecordingClient(MockClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.messages = []

    def complete(self, role, messages, model=None, **kwargs):
        self.messages.append(messages)
        return super().complete(role, messages, model=model, **kwargs)


def test_channel_url_sent_to_model(tmp_path, monkeypatch):
    srt, context = write_inputs(tmp_path)
    client = RecordingClient(text=json.dumps(FULL), config=CONFIG)
    patch(monkeypatch, client)
    code = metadata.main(
        [str(srt), "--context", str(context), "--out-dir", str(tmp_path / "out")]
    )
    assert code == 0
    message = client.messages[0][0]["content"]
    assert "## Links" in message
    assert f"channel_url: {CHANNEL_URL}" in message


def test_title_flag_puts_chosen_title_in_message(tmp_path, monkeypatch):
    srt, context = write_inputs(tmp_path)
    client = RecordingClient(text=json.dumps(FULL), config=CONFIG)
    patch(monkeypatch, client)
    code = metadata.main(
        [
            str(srt),
            "--context",
            str(context),
            "--title",
            "Titlul ales",
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )
    assert code == 0
    message = client.messages[0][0]["content"]
    assert "## Chosen title" in message
    assert "Titlul ales" in message
    assert (
        message.index("\n\n## Context\n\n")
        < message.index("\n\n## Chosen title\n\n")
        < message.index("\n\n## Transcript\n\n")
    )


def test_missing_json_key_exits_3(tmp_path, monkeypatch, capsys):
    data = dict(FULL)
    del data["subscribe_line"]
    code, _ = run_main(tmp_path, monkeypatch, data)
    assert code == EXIT_RUNTIME
    err = capsys.readouterr().err
    assert "subscribe_line" in err
    assert "Traceback" not in err


class RetryClient(MockClient):
    def __init__(self, answers, **kwargs):
        super().__init__(**kwargs)
        self.answers = list(answers)
        self.calls = 0

    def complete(self, role, messages, model=None, **kwargs):
        self._default_text = self.answers[self.calls]
        self.calls += 1
        return super().complete(role, messages, model=model, **kwargs)


def test_empty_first_answer_is_retried(tmp_path, monkeypatch):
    srt, context = write_inputs(tmp_path)
    client = RetryClient(["", json.dumps(FULL)], config=CONFIG)
    patch(monkeypatch, client)
    code = metadata.main(
        [str(srt), "--context", str(context), "--out-dir", str(tmp_path / "out")]
    )
    assert code == EXIT_OK
    assert (tmp_path / "out" / "metadata.md").is_file()
    assert client.calls == 2


BAD_CHAPTERS = [
    ("beyond", [{"timestamp": "99:99:99", "title": "Bad"}]),
    ("decreasing", [
        {"timestamp": "00:00:00", "title": "Intro"},
        {"timestamp": "00:00:01", "title": "Back"},
        {"timestamp": "00:00:00", "title": "Again"},
    ]),
    ("first_not_zero", [{"timestamp": "00:00:02", "title": "Late"}]),
]


@pytest.mark.parametrize("name,chapters", BAD_CHAPTERS, ids=[case[0] for case in BAD_CHAPTERS])
def test_bad_chapter_exits_2(tmp_path, monkeypatch, capsys, name, chapters):
    data = dict(FULL, chapters=chapters)
    code, _ = run_main(tmp_path, monkeypatch, data)
    assert code == 2
    err = capsys.readouterr().err
    assert chapters[-1]["timestamp"] in err
    assert "Traceback" not in err


def test_json_summary(tmp_path, monkeypatch, capsys):
    code, _ = run_main(tmp_path, monkeypatch, FULL, extra=["--json"], cost_per_call=0.005)
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["titles"] == 3
    assert summary["chapters"] == 2
    assert summary["tags"] == 3
    assert summary["llm_cost_usd"] == 0.005
    assert summary["wall_time_s"] >= 0
    assert summary["output"].endswith("metadata.md")


def test_metadata_json_side_output(tmp_path, monkeypatch):
    code, out = run_main(
        tmp_path,
        monkeypatch,
        FULL,
        extra=["--previous-url", "https://prev", "--repo-url", "https://repo"],
    )
    assert code == 0
    data = json.loads((tmp_path / "out" / "metadata.json").read_text(encoding="utf-8"))
    assert set(data) == {
        "name",
        "titles",
        "description_ro",
        "description_en",
        "chapters",
        "tags",
        "pinned_comment",
    }
    assert data["name"] == "clip"
    assert data["titles"] == FULL["titles"]
    assert data["tags"] == FULL["tags"]
    assert data["pinned_comment"] == FULL["pinned_comment"]
    assert data["chapters"] == [
        {"timestamp": "00:00:00", "title": "Intro"},
        {"timestamp": "00:00:02", "title": "Lume"},
    ]

    text = out.read_text(encoding="utf-8")
    ro_section = text.split("## Description RO\n\n", 1)[1].split("\n\n## Description EN", 1)[0]
    en_section = text.split("## Description EN\n\n", 1)[1].split("\n\n## Chapters", 1)[0]
    assert data["description_ro"] == ro_section
    assert data["description_en"] == en_section


def test_fenced_json_is_parsed(tmp_path, monkeypatch):
    srt, context = write_inputs(tmp_path)
    client = MockClient(text="```json\n" + json.dumps(FULL) + "\n```", config=CONFIG)
    patch(monkeypatch, client)
    code = metadata.main([str(srt), "--context", str(context), "--out-dir", str(tmp_path / "out")])
    assert code == 0
    assert (tmp_path / "out" / "metadata.md").is_file()


def test_missing_srt_exits_2(tmp_path, monkeypatch, capsys):
    context = tmp_path / "context.md"
    context.write_text("Angle: test", encoding="utf-8")
    client = MockClient(text=json.dumps(FULL), config=CONFIG)
    patch(monkeypatch, client)
    code = metadata.main([str(tmp_path / "nope.srt"), "--context", str(context)])
    assert code == 2
    assert "not found" in capsys.readouterr().err


def write_chapters(tmp_path, chapters, name="chapters.json"):
    document = {
        "schema_version": "0.1",
        "generated_at": "2026-10-21T00:00:00+00:00",
        "tool": "scripts/chapters.py",
        "chapters": chapters,
    }
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


FILE_CHAPTERS = [
    {"start_s": 0.0, "title": "FileOne", "summary": "one"},
    {"start_s": 1.0, "title": "FileTwo", "summary": "two"},
    {"start_s": 2.0, "title": "FileThree", "summary": "three"},
]


def test_chapters_file_overrides_model(tmp_path, monkeypatch):
    chapters_file = write_chapters(tmp_path, FILE_CHAPTERS)
    data = dict(
        FULL,
        chapters=[
            {"timestamp": "00:00:00", "title": "ModelIntro"},
            {"timestamp": "00:00:03", "title": "ModelEnd"},
        ],
    )
    code, out = run_main(tmp_path, monkeypatch, data, extra=["--chapters", str(chapters_file)])
    assert code == 0
    payload = json.loads((tmp_path / "out" / "metadata.json").read_text(encoding="utf-8"))
    assert payload["chapters"] == [
        {"timestamp": "00:00:00", "title": "FileOne"},
        {"timestamp": "00:00:01", "title": "FileTwo"},
        {"timestamp": "00:00:02", "title": "FileThree"},
    ]
    text = out.read_text(encoding="utf-8")
    assert "00:00:00 FileOne" in text
    assert "00:00:01 FileTwo" in text
    assert "00:00:02 FileThree" in text
    assert "ModelIntro" not in text
    assert "ModelEnd" not in text


def test_chapters_file_model_may_omit_chapters(tmp_path, monkeypatch):
    chapters_file = write_chapters(tmp_path, FILE_CHAPTERS)
    data = dict(FULL)
    del data["chapters"]
    code, _ = run_main(tmp_path, monkeypatch, data, extra=["--chapters", str(chapters_file)])
    assert code == 0
    payload = json.loads((tmp_path / "out" / "metadata.json").read_text(encoding="utf-8"))
    assert [chapter["title"] for chapter in payload["chapters"]] == [
        "FileOne",
        "FileTwo",
        "FileThree",
    ]


def test_without_flag_model_chapters_used(tmp_path, monkeypatch):
    code, _ = run_main(tmp_path, monkeypatch, FULL)
    assert code == 0
    payload = json.loads((tmp_path / "out" / "metadata.json").read_text(encoding="utf-8"))
    assert payload["chapters"] == [
        {"timestamp": "00:00:00", "title": "Intro"},
        {"timestamp": "00:00:02", "title": "Lume"},
    ]


def test_without_flag_missing_chapters_key_exits_3(tmp_path, monkeypatch, capsys):
    data = dict(FULL)
    del data["chapters"]
    code, _ = run_main(tmp_path, monkeypatch, data)
    assert code == EXIT_RUNTIME
    err = capsys.readouterr().err
    assert "chapters" in err
    assert "Traceback" not in err


def test_chapters_file_first_not_zero_exits_2(tmp_path, monkeypatch, capsys):
    chapters_file = write_chapters(
        tmp_path, [{"start_s": 1.0, "title": "Late", "summary": "late"}]
    )
    code, _ = run_main(tmp_path, monkeypatch, FULL, extra=["--chapters", str(chapters_file)])
    assert code == 2
    err = capsys.readouterr().err
    assert "chapter 1" in err
    assert "00:00:01" in err
    assert "Traceback" not in err


def test_chapters_file_beyond_srt_end_exits_2(tmp_path, monkeypatch, capsys):
    chapters_file = write_chapters(
        tmp_path,
        [
            {"start_s": 0.0, "title": "Start", "summary": "start"},
            {"start_s": 10.0, "title": "Far", "summary": "far"},
        ],
    )
    code, _ = run_main(tmp_path, monkeypatch, FULL, extra=["--chapters", str(chapters_file)])
    assert code == 2
    err = capsys.readouterr().err
    assert "beyond the transcript end" in err
    assert "Traceback" not in err


def test_chapters_file_invalid_schema_exits_2(tmp_path, monkeypatch, capsys):
    path = tmp_path / "chapters.json"
    path.write_text(
        json.dumps({"chapters": [{"start_s": 0.0, "title": "NoSummary"}]}), encoding="utf-8"
    )
    code, _ = run_main(tmp_path, monkeypatch, FULL, extra=["--chapters", str(path)])
    assert code == 2
    err = capsys.readouterr().err
    assert str(path) in err
    assert "Traceback" not in err


def test_chapters_file_missing_exits_2(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "nope.json"
    code, _ = run_main(tmp_path, monkeypatch, FULL, extra=["--chapters", str(missing)])
    assert code == 2
    err = capsys.readouterr().err
    assert "not found" in err
    assert "nope.json" in err


def test_chapters_file_json_summary(tmp_path, monkeypatch, capsys):
    chapters_file = write_chapters(tmp_path, FILE_CHAPTERS)
    code, _ = run_main(
        tmp_path,
        monkeypatch,
        FULL,
        extra=["--chapters", str(chapters_file), "--json"],
        cost_per_call=0.005,
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["chapters"] == 3
    assert summary["wall_time_s"] >= 0
    assert summary["llm_cost_usd"] == 0.005
