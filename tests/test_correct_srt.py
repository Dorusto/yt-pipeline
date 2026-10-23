import json
import sys

import correct_srt

SRT = (
    "1\n00:00:00,200 --> 00:00:03,800\nwrongword here.\n\n"
    "2\n00:00:04,200 --> 00:00:06,600\nAlfa beta gama.\n"
)


def run_main(monkeypatch, input_path, output_path):
    monkeypatch.setattr(
        sys, "argv", ["correct_srt.py", str(input_path), str(output_path)]
    )
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
