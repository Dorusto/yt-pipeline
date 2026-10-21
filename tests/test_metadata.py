import json

import pytest

import metadata
from common.llm import MockClient

SRT = (
    "1\n00:00:00,000 --> 00:00:02,000\nBună ziua\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\nLume\n\n"
)

CONFIG = {
    "models": {"metadata": "deepseek/deepseek-v4.1-flash"},
    "llm_budget_usd": None,
    "llm_backend": "mock",
    "channel": {"members": "Andrei, Mihai și Maria"},
}

MEMBERS = metadata.MEMBERS_SENTENCE.replace("{members}", "Andrei, Mihai și Maria")

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
    "thumbnail_concepts": [
        {
            "name": "Bucurie",
            "pose": "mâinile ridicate, cadru strâns",
            "emotion": "bucurie reală",
            "text": {"title": "AI-UL MEU", "keyword": "RULEAZĂ ACASĂ", "kicker": "// homelab"},
            "theme": "tech",
            "source": {"kind": "clip_frame", "timecode": "00:00:02"},
        },
        {
            "name": "Surpriză",
            "pose": "privire în cameră, zâmbet larg",
            "emotion": "surpriză",
            "text": {"title": "MERGE SINGUR", "keyword": "ACASĂ", "kicker": ""},
            "theme": "creator",
            "source": {"kind": "new_photo"},
        },
    ],
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

    assert text.count(MEMBERS) == 2
    assert text.count(metadata.CLOSING) == 2

    ro_start = text.index("## Description RO")
    en_start = text.index("## Description EN")
    assert ro_start < en_start
    ro_block = text[ro_start:en_start]
    order = [
        ro_block.index("Intro RO"),
        ro_block.index("GitHub RO"),
        ro_block.index(MEMBERS),
        ro_block.index("Previous RO"),
        ro_block.index("Subscribe RO"),
        ro_block.index("[affiliate] Promo RO"),
        ro_block.index(metadata.CLOSING),
    ]
    assert order == sorted(order)

    en_block = text[en_start:]
    assert MEMBERS in en_block
    assert metadata.CLOSING in en_block

    assert "1. Titlu unu" in text
    assert "2. Titlu doi" in text
    assert "3. Titlu trei" in text
    assert "00:00:00 Intro" in text
    assert "00:00:02 Lume" in text
    assert "tag1, tag2, tag3" in text
    assert "## Thumbnail" in text
    assert text.index("## Titles") < text.index("## Thumbnail") < ro_start
    assert "### 1. Bucurie" in text
    assert "### 2. Surpriză" in text
    assert "Emotion: bucurie reală" in text
    assert "clip frame 00:00:02" in text
    assert "new photo (shoot it for --photo)" in text
    assert "thumbnail.py --photo <source>" in text
    assert '--title "AI-UL MEU" --keyword "RULEAZĂ ACASĂ"' in text
    assert "--theme tech" in text and "--theme creator" in text
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


def test_missing_channel_members_exits_2(tmp_path, monkeypatch, capsys):
    config = {
        "models": {"metadata": "deepseek/deepseek-v4.1-flash"},
        "llm_budget_usd": None,
        "llm_backend": "mock",
    }
    code, _ = run_main(tmp_path, monkeypatch, FULL, config=config)
    assert code == 2
    err = capsys.readouterr().err
    assert "channel.members" in err
    assert "Traceback" not in err


def test_missing_json_key_exits_2(tmp_path, monkeypatch, capsys):
    data = dict(FULL)
    del data["subscribe_line"]
    code, _ = run_main(tmp_path, monkeypatch, data)
    assert code == 2
    err = capsys.readouterr().err
    assert "subscribe_line" in err
    assert "Traceback" not in err


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


def _concept(**overrides):
    concept = json.loads(json.dumps(FULL["thumbnail_concepts"][0]))
    concept.update(overrides)
    return concept


BAD_CONCEPT_COUNTS = [
    ("one", [0]),
    ("four", [0, 1, 0, 1]),
]


@pytest.mark.parametrize("name,picks", BAD_CONCEPT_COUNTS, ids=[case[0] for case in BAD_CONCEPT_COUNTS])
def test_bad_thumbnail_concept_count_exits_2(tmp_path, monkeypatch, capsys, name, picks):
    data = dict(FULL, thumbnail_concepts=[FULL["thumbnail_concepts"][i] for i in picks])
    code, _ = run_main(tmp_path, monkeypatch, data)
    assert code == 2
    err = capsys.readouterr().err
    assert "thumbnail_concepts" in err
    assert "Traceback" not in err


def test_bad_thumbnail_theme_exits_2(tmp_path, monkeypatch, capsys):
    data = dict(FULL, thumbnail_concepts=[_concept(theme="dark"), FULL["thumbnail_concepts"][1]])
    code, _ = run_main(tmp_path, monkeypatch, data)
    assert code == 2
    err = capsys.readouterr().err
    assert "theme" in err
    assert "Traceback" not in err


def test_thumbnail_frame_without_timecode_exits_2(tmp_path, monkeypatch, capsys):
    concept = _concept()
    del concept["source"]["timecode"]
    data = dict(FULL, thumbnail_concepts=[concept, FULL["thumbnail_concepts"][1]])
    code, _ = run_main(tmp_path, monkeypatch, data)
    assert code == 2
    err = capsys.readouterr().err
    assert "timecode" in err
    assert "Traceback" not in err


def test_thumbnail_timecode_beyond_transcript_exits_2(tmp_path, monkeypatch, capsys):
    data = dict(
        FULL,
        thumbnail_concepts=[
            _concept(source={"kind": "clip_frame", "timecode": "00:00:12"}),
            FULL["thumbnail_concepts"][1],
        ],
    )
    code, _ = run_main(tmp_path, monkeypatch, data)
    assert code == 2
    err = capsys.readouterr().err
    assert "beyond the transcript end" in err
    assert "Traceback" not in err


def test_neutral_emotion_is_still_valid_json(tmp_path, monkeypatch):
    data = dict(
        FULL,
        thumbnail_concepts=[
            _concept(emotion="neutral"),
            FULL["thumbnail_concepts"][1],
        ],
    )
    code, out = run_main(tmp_path, monkeypatch, data)
    assert code == 0
    assert "Emotion: neutral" in out.read_text(encoding="utf-8")


def test_json_summary(tmp_path, monkeypatch, capsys):
    code, _ = run_main(tmp_path, monkeypatch, FULL, extra=["--json"], cost_per_call=0.005)
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["titles"] == 3
    assert summary["chapters"] == 2
    assert summary["tags"] == 3
    assert summary["thumbnail_concepts"] == 2
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
        "thumbnail_concepts",
    }
    assert data["name"] == "clip"
    assert data["titles"] == FULL["titles"]
    assert data["tags"] == FULL["tags"]
    assert data["pinned_comment"] == FULL["pinned_comment"]
    assert data["chapters"] == [
        {"timestamp": "00:00:00", "title": "Intro"},
        {"timestamp": "00:00:02", "title": "Lume"},
    ]
    assert data["thumbnail_concepts"][0]["source"]["timecode"] == "00:00:02"

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
