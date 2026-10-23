import importlib
import io
import json
import sys

import pytest

import analyze_srt
import daily_summary_srt
import translate_srt
from common.llm import MockClient
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME

SAMPLE_SRT = "1\n00:00:00,000 --> 00:00:02,000\nBună\n\n2\n00:00:02,000 --> 00:00:04,000\nLume\n\n"
ANALYZE_JSON = (
    '{"video": {"title": "T", "description": "d", "tags": ["t"]}, '
    '"chapters": [], "shorts": []}'
)


def patch_client(monkeypatch, module, client):
    monkeypatch.setattr(module, "get_client", lambda: client)


def test_translate_batch_records_translate_role(monkeypatch):
    client = MockClient(text="[1] Hello\n[2] World")
    patch_client(monkeypatch, translate_srt, client)
    result = translate_srt.translate_batch(["Bună", "Lume"])
    assert result == {1: "Hello", 2: "World"}
    assert client._entries[0]["role"] == "translate"


def test_translate_main_end_to_end(tmp_path, monkeypatch, capsys):
    srt = tmp_path / "clip.srt"
    srt.write_text(SAMPLE_SRT, encoding="utf-8")
    client = MockClient(text="[1] Hello\n[2] World")
    patch_client(monkeypatch, translate_srt, client)
    monkeypatch.setattr(sys, "argv", ["translate_srt.py", str(srt)])

    translate_srt.main()

    out = tmp_path / "clip_EN.srt"
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "Hello" in text and "World" in text
    assert "LLM cost: $0.0000" in capsys.readouterr().out


def test_analyze_with_deepseek_records_analyze_role(monkeypatch):
    client = MockClient(text=ANALYZE_JSON)
    patch_client(monkeypatch, analyze_srt, client)
    result = analyze_srt.analyze_with_deepseek("transcript")
    assert result["video"]["title"] == "T"
    assert client._entries[0]["role"] == "analyze"


def test_summarize_day_records_summary_role(monkeypatch):
    client = MockClient(text="rezumat")
    patch_client(monkeypatch, daily_summary_srt, client)
    assert daily_summary_srt.summarize_day("2026-01-01", "text") == "rezumat"
    assert client._entries[0]["role"] == "summary"


def test_missing_role_exits_2_with_clean_message(tmp_path, monkeypatch, capsys):
    srt = tmp_path / "clip.srt"
    srt.write_text(SAMPLE_SRT, encoding="utf-8")
    client = MockClient(config={"models": {}, "llm_budget_usd": None, "llm_backend": "mock"})
    patch_client(monkeypatch, translate_srt, client)
    monkeypatch.setattr(sys, "argv", ["translate_srt.py", str(srt)])

    with pytest.raises(SystemExit) as excinfo:
        translate_srt.main()

    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "unknown model role" in err
    assert "Traceback" not in err


def test_budget_exceeded_exits_2_with_clean_message(tmp_path, monkeypatch, capsys):
    srt = tmp_path / "clip.srt"
    srt.write_text(SAMPLE_SRT, encoding="utf-8")
    client = MockClient(budget_usd=0.0, cost_per_call=0.5)
    patch_client(monkeypatch, translate_srt, client)
    monkeypatch.setattr(sys, "argv", ["translate_srt.py", str(srt)])

    with pytest.raises(SystemExit) as excinfo:
        translate_srt.main()

    assert excinfo.value.code == EXIT_RUNTIME
    err = capsys.readouterr().err
    assert "budget" in err.lower()
    assert "Traceback" not in err


def test_import_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    for name in ("translate_srt", "analyze_srt", "daily_summary_srt"):
        importlib.reload(importlib.import_module(name))


def test_parse_selection_reads_flag_values():
    shorts = [{"i": 1}, {"i": 2}, {"i": 3}]
    assert analyze_srt.parse_selection("all", shorts) == shorts
    assert analyze_srt.parse_selection("none", shorts) == []
    assert analyze_srt.parse_selection("2", shorts) == [shorts[1]]
    assert analyze_srt.parse_selection("1,3", shorts) == [shorts[0], shorts[2]]


def test_translate_main_json_summary_reports_cost(tmp_path, monkeypatch, capsys):
    srt = tmp_path / "clip.srt"
    srt.write_text(SAMPLE_SRT, encoding="utf-8")
    client = MockClient(text="[1] Hello\n[2] World", cost_per_call=0.25)
    patch_client(monkeypatch, translate_srt, client)
    monkeypatch.setattr(translate_srt, "_client", client)
    monkeypatch.setattr(sys, "argv", ["translate_srt.py", str(srt), "--json"])

    translate_srt.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["segments"] == 2
    assert payload["wall_time_s"] >= 0.0
    assert payload["llm_cost_usd"] == 0.25


def test_daily_summary_main_json_summary_reports_cost(tmp_path, monkeypatch, capsys):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "DJI_20260101120000_0001_D.srt").write_text(SAMPLE_SRT, encoding="utf-8")
    client = MockClient(text="rezumat", cost_per_call=0.5)
    patch_client(monkeypatch, daily_summary_srt, client)
    monkeypatch.setattr(daily_summary_srt, "_client", client)
    monkeypatch.setattr(
        sys, "argv", ["daily_summary_srt.py", str(raw), str(tmp_path / "out.md"), "--json"]
    )

    daily_summary_srt.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["days"] == ["2026-01-01"]
    assert payload["skipped"] == []
    assert payload["wall_time_s"] >= 0.0
    assert payload["llm_cost_usd"] == 0.5


def test_analyze_main_json_summary_with_select_none(tmp_path, monkeypatch, capsys):
    srt = tmp_path / "clip.srt"
    video = tmp_path / "clip.mp4"
    srt.write_text(SAMPLE_SRT, encoding="utf-8")
    video.write_bytes(b"x")
    client = MockClient(text=ANALYZE_JSON)
    patch_client(monkeypatch, analyze_srt, client)
    monkeypatch.setattr(analyze_srt, "_client", client)
    monkeypatch.setattr(
        sys, "argv", ["analyze_srt.py", str(srt), str(video), "--select", "none", "--json"]
    )

    analyze_srt.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "metadata"
    assert payload["candidates"] == 0
    assert payload["selected"] == 0
    assert payload["wall_time_s"] >= 0.0
    assert payload["llm_cost_usd"] == 0.0


def test_analyze_without_select_and_no_tty_is_a_usage_error(tmp_path, monkeypatch, capsys):
    srt = tmp_path / "clip.srt"
    video = tmp_path / "clip.mp4"
    srt.write_text(SAMPLE_SRT, encoding="utf-8")
    video.write_bytes(b"x")
    client = MockClient(text=ANALYZE_JSON)
    patch_client(monkeypatch, analyze_srt, client)
    monkeypatch.setattr(analyze_srt, "_client", client)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr(sys, "argv", ["analyze_srt.py", str(srt), str(video)])

    with pytest.raises(SystemExit) as excinfo:
        analyze_srt.main()

    assert excinfo.value.code == 2
    assert "--select" in capsys.readouterr().err
