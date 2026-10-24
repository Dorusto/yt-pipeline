import json
import sys

import pytest

import correct_srt
from common.corrections import Correction, CorrectionsError, apply_corrections, load_corrections
from common.exit_codes import EXIT_USAGE

SRT = (
    "1\n00:00:00,200 --> 00:00:03,800\nwrongword here.\n\n"
    "2\n00:00:04,200 --> 00:00:06,600\nAlfa beta gama.\n"
)


def run_main(monkeypatch, input_path, output_path, extra=None):
    argv = ["correct_srt.py", str(input_path), str(output_path)]
    if extra:
        argv += extra
    monkeypatch.setattr(sys, "argv", argv)
    correct_srt.main()


def test_missing_corrections_file_falls_back(tmp_path, monkeypatch, capsys):
    input_path = tmp_path / "in.srt"
    output_path = tmp_path / "out.srt"
    input_path.write_text(SRT, encoding="utf-8")
    missing = tmp_path / "corrections.txt"
    monkeypatch.setattr(correct_srt, "CORRECTIONS_FILE", missing)

    run_main(monkeypatch, input_path, output_path)

    assert output_path.is_file()
    assert output_path.read_text(encoding="utf-8") == SRT
    captured = capsys.readouterr()
    assert str(missing) in captured.err
    assert "Corecturi încărcate: 0" in captured.out


def test_existing_corrections_file_applies_replacements(tmp_path, monkeypatch, capsys):
    input_path = tmp_path / "in.srt"
    output_path = tmp_path / "out.srt"
    input_path.write_text(SRT, encoding="utf-8")
    corrections = tmp_path / "corrections.txt"
    corrections.write_text("wrongword|correctword\n", encoding="utf-8")
    monkeypatch.setattr(correct_srt, "CORRECTIONS_FILE", corrections)

    run_main(monkeypatch, input_path, output_path)

    assert output_path.read_text(encoding="utf-8") == SRT.replace(
        "wrongword", "correctword"
    )
    captured = capsys.readouterr()
    assert "Corecturi încărcate: 1" in captured.out
    assert "Termeni înlocuiți: 1" in captured.out


def test_missing_corrections_and_example_still_writes(tmp_path, monkeypatch, capsys):
    input_path = tmp_path / "in.srt"
    output_path = tmp_path / "out.srt"
    input_path.write_text(SRT, encoding="utf-8")
    missing = tmp_path / "corrections.txt"
    missing_example = tmp_path / "corrections_example.txt"
    monkeypatch.setattr(correct_srt, "CORRECTIONS_FILE", missing)
    monkeypatch.setattr(correct_srt, "CORRECTIONS_EXAMPLE_FILE", missing_example)

    run_main(monkeypatch, input_path, output_path)

    assert output_path.is_file()
    assert output_path.read_text(encoding="utf-8") == SRT
    captured = capsys.readouterr()
    assert str(missing) in captured.err
    assert "Corecturi încărcate: 0" in captured.out


def test_json_summary_reports_wall_time_and_cost(tmp_path, monkeypatch, capsys):
    input_path = tmp_path / "in.srt"
    output_path = tmp_path / "out.srt"
    input_path.write_text(SRT, encoding="utf-8")
    missing = tmp_path / "corrections.txt"
    monkeypatch.setattr(correct_srt, "CORRECTIONS_FILE", missing)
    monkeypatch.setattr(
        sys, "argv", ["correct_srt.py", str(input_path), str(output_path), "--json"]
    )

    correct_srt.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["output"] == str(output_path)
    assert payload["corrections"] == 0
    assert payload["changes"] == 0
    assert payload["wall_time_s"] >= 0.0
    assert payload["llm_cost_usd"] == 0.0


def test_load_corrections_parses_the_star_marker(tmp_path):
    path = tmp_path / "corrections.txt"
    path.write_text("*wrongword|correctword\nplain|fixed\n", encoding="utf-8")

    corrections = load_corrections(path)

    assert corrections == [
        Correction("wrongword", "correctword", True),
        Correction("plain", "fixed", False),
    ]


def test_star_marker_fixes_both_cases(tmp_path, monkeypatch):
    input_path = tmp_path / "in.srt"
    output_path = tmp_path / "out.srt"
    content = (
        "1\n00:00:00,200 --> 00:00:03,800\nwrongword and Wrongword here.\n\n"
        "2\n00:00:04,200 --> 00:00:06,600\nwrongWord stays.\n"
    )
    input_path.write_text(content, encoding="utf-8")
    corrections = tmp_path / "corrections.txt"
    corrections.write_text("*wrongword|correctword\n", encoding="utf-8")
    monkeypatch.setattr(correct_srt, "CORRECTIONS_FILE", corrections)

    run_main(monkeypatch, input_path, output_path)

    result = output_path.read_text(encoding="utf-8")
    assert "correctword and Correctword here." in result
    assert "wrongWord stays." in result


def test_no_star_fixes_only_the_lowercase(tmp_path, monkeypatch):
    input_path = tmp_path / "in.srt"
    output_path = tmp_path / "out.srt"
    content = (
        "1\n00:00:00,200 --> 00:00:03,800\nwrongword and Wrongword here.\n"
    )
    input_path.write_text(content, encoding="utf-8")
    corrections = tmp_path / "corrections.txt"
    corrections.write_text("wrongword|correctword\n", encoding="utf-8")
    monkeypatch.setattr(correct_srt, "CORRECTIONS_FILE", corrections)

    run_main(monkeypatch, input_path, output_path)

    result = output_path.read_text(encoding="utf-8")
    assert "correctword and Wrongword here." in result


def test_already_capitalised_pattern_gets_no_variant():
    corrections = [Correction("CloudCode", "Claude Code", True)]

    result = apply_corrections("CloudCode and cloudcode", corrections)

    assert result == "Claude Code and cloudcode"


def test_capitalised_flag_turns_the_variant_on(tmp_path, monkeypatch):
    input_path = tmp_path / "in.srt"
    output_path = tmp_path / "out.srt"
    content = (
        "1\n00:00:00,200 --> 00:00:03,800\nwrongword and Wrongword here.\n"
    )
    input_path.write_text(content, encoding="utf-8")
    corrections = tmp_path / "corrections.txt"
    corrections.write_text("wrongword|correctword\n", encoding="utf-8")
    monkeypatch.setattr(correct_srt, "CORRECTIONS_FILE", corrections)

    run_main(monkeypatch, input_path, output_path, extra=["--capitalised"])

    result = output_path.read_text(encoding="utf-8")
    assert "correctword and Correctword here." in result


def test_change_count_includes_capitalised_variant(tmp_path, monkeypatch, capsys):
    input_path = tmp_path / "in.srt"
    output_path = tmp_path / "out.srt"
    content = "1\n00:00:00,200 --> 00:00:03,800\nWrongword here.\n"
    input_path.write_text(content, encoding="utf-8")
    corrections = tmp_path / "corrections.txt"
    corrections.write_text("*wrongword|correctword\n", encoding="utf-8")
    monkeypatch.setattr(correct_srt, "CORRECTIONS_FILE", corrections)

    run_main(monkeypatch, input_path, output_path, extra=["--json"])

    captured = capsys.readouterr()
    assert "Termeni înlocuiți: 1" in captured.err
    payload = json.loads(captured.out)
    assert payload["changes"] == 1


def test_empty_wrong_part_is_a_usage_error(tmp_path, monkeypatch, capsys):
    corrections = tmp_path / "corrections.txt"
    corrections.write_text("*|x\n", encoding="utf-8")
    with pytest.raises(CorrectionsError):
        load_corrections(corrections)

    input_path = tmp_path / "in.srt"
    output_path = tmp_path / "out.srt"
    input_path.write_text(SRT, encoding="utf-8")
    monkeypatch.setattr(correct_srt, "CORRECTIONS_FILE", corrections)
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, input_path, output_path)
    assert exc.value.code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "Traceback" not in err
