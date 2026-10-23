import json

import pytest

import thumbnail_concepts
from common.llm import MockClient
from common.schema import validate
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME

SRT = (
    "1\n00:00:00,000 --> 00:00:02,000\nBună ziua\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\nLume\n\n"
)

CONFIG = {
    "models": {"metadata": "deepseek/deepseek-v4.1-flash"},
    "llm_budget_usd": None,
    "llm_backend": "mock",
}

TITLE = "AI-UL MEU"

FULL = {
    "concepts": [
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
    monkeypatch.setattr(thumbnail_concepts, "get_client", lambda: client)
    monkeypatch.setattr(thumbnail_concepts, "load_config", lambda: config)


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
    argv = [
        str(srt), "--context", str(context), "--title", TITLE,
        "--out-dir", str(tmp_path / "out"),
    ]
    if extra:
        argv.extend(extra)
    return thumbnail_concepts.main(argv), tmp_path / "out"


def test_end_to_end(tmp_path, monkeypatch):
    code, out = run_main(tmp_path, monkeypatch, FULL)
    assert code == EXIT_OK

    document = json.loads((out / "thumbnail_concepts.json").read_text(encoding="utf-8"))
    assert validate(document, "thumbnail_concepts") == []
    assert document["title"] == TITLE
    assert document["tool"] == "scripts/thumbnail_concepts.py"
    assert len(document["concepts"]) == 2
    assert document["concepts"][0]["source"]["timecode"] == "00:00:02"

    text = (out / "thumbnail_concepts.md").read_text(encoding="utf-8")
    assert "Nume: Bucurie" in text
    assert "Poză: mâinile ridicate, cadru strâns" in text
    assert "Emoție: bucurie reală" in text
    assert "Text: titlu \"AI-UL MEU\"" in text
    assert "Temă: tech" in text
    assert "Sursă: cadru din clip 00:00:02" in text
    assert "Sursă: poză nouă" in text
    assert "thumbnail.py --concepts thumbnail_concepts.json --pick 1" in text
    assert "thumbnail.py --concepts thumbnail_concepts.json --pick 2" in text


def test_timecode_beyond_transcript_exits_2(tmp_path, monkeypatch, capsys):
    data = dict(FULL)
    data["concepts"] = [
        dict(FULL["concepts"][0], source={"kind": "clip_frame", "timecode": "00:00:12"}),
        FULL["concepts"][1],
    ]
    code, _ = run_main(tmp_path, monkeypatch, data)
    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "Bucurie" in err
    assert "beyond the transcript end" in err
    assert "Traceback" not in err


def test_bad_theme_exits_2(tmp_path, monkeypatch, capsys):
    data = dict(FULL)
    data["concepts"] = [
        dict(FULL["concepts"][0], theme="dark"),
        FULL["concepts"][1],
    ]
    code, _ = run_main(tmp_path, monkeypatch, data)
    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "theme" in err
    assert "Traceback" not in err


BAD_COUNTS = [
    ("one", [0]),
    ("four", [0, 1, 0, 1]),
]


@pytest.mark.parametrize("name,picks", BAD_COUNTS, ids=[case[0] for case in BAD_COUNTS])
def test_bad_concept_count_exits_3(tmp_path, monkeypatch, capsys, name, picks):
    data = {"concepts": [FULL["concepts"][i] for i in picks]}
    code, _ = run_main(tmp_path, monkeypatch, data)
    assert code == EXIT_RUNTIME
    err = capsys.readouterr().err
    assert "concepts" in err
    assert "Traceback" not in err


def test_missing_concepts_key_exits_3(tmp_path, monkeypatch, capsys):
    code, _ = run_main(tmp_path, monkeypatch, {"titles": ["a"]})
    assert code == EXIT_RUNTIME
    err = capsys.readouterr().err
    assert "concepts" in err
    assert "Traceback" not in err


def test_missing_srt_exits_2(tmp_path, monkeypatch, capsys):
    context = tmp_path / "context.md"
    context.write_text("Angle: test", encoding="utf-8")
    client = MockClient(text=json.dumps(FULL), config=CONFIG)
    patch(monkeypatch, client)
    code = thumbnail_concepts.main([
        str(tmp_path / "nope.srt"), "--context", str(context), "--title", TITLE,
    ])
    assert code == EXIT_USAGE
    assert "not found" in capsys.readouterr().err


def test_missing_title_is_argparse_error(tmp_path):
    srt, context = write_inputs(tmp_path)
    with pytest.raises(SystemExit) as exc:
        thumbnail_concepts.main([str(srt), "--context", str(context)])
    assert exc.value.code == 2


def test_json_summary(tmp_path, monkeypatch, capsys):
    code, _ = run_main(tmp_path, monkeypatch, FULL, extra=["--json"], cost_per_call=0.005)
    assert code == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["ok"] is True
    assert summary["concepts"] == 2
    assert summary["title"] == TITLE
    assert summary["llm_cost_usd"] == 0.005
    assert summary["wall_time_s"] >= 0
    assert summary["outputs"]["json"].endswith("thumbnail_concepts.json")
    assert summary["outputs"]["markdown"].endswith("thumbnail_concepts.md")
