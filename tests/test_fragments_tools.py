import json
from pathlib import Path

import pytest

import kdenlive_from_fragments as kf
import srt_from_fragments as sf
from common.schema import SchemaError
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME

FIXTURES = Path(__file__).parent / "fixtures"


def test_sec_to_tc():
    assert kf.sec_to_tc(3661.5) == "01:01:01.500"
    assert kf.sec_to_tc(0) == "00:00:00.000"


def test_tc_to_frames_uses_project_fps():
    assert kf.tc_to_frames("00:00:01.000") == 25
    assert kf.tc_to_frames("00:01:00.000") == 1500


def test_kdenlive_load_config_valid_fixture():
    cfg = kf.load_config(FIXTURES / "fragments_valid.json")
    assert len(cfg["fragments"]) == 2


def test_kdenlive_load_config_requires_output(tmp_path):
    cfg = tmp_path / "f.yaml"
    cfg.write_text(
        "source_dir: /x\nfragments:\n  - file: a.MP4\n    in: '00:00:00.000'\n    out: '00:00:01.000'\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaError):
        kf.load_config(cfg)


def test_kdenlive_load_config_rejects_missing_timecode(tmp_path):
    cfg = tmp_path / "f.yaml"
    cfg.write_text(
        "source_dir: /x\noutput: /x/p.kdenlive\nfragments:\n  - file: a.MP4\n    in: '00:00:00.000'\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaError):
        kf.load_config(cfg)


def test_kdenlive_main_invalid_returns_2(tmp_path, capsys):
    cfg = tmp_path / "f.yaml"
    cfg.write_text("fragments: []\n", encoding="utf-8")
    assert kf.main([str(cfg), "--json"]) == EXIT_USAGE
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_srt_load_config_valid_fixture():
    cfg = sf.load_config(FIXTURES / "fragments_valid.json")
    assert cfg["fragments"][0]["file"].endswith(".MP4")


def _write_raw_srt(path):
    path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nintro line\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nsecond line\n\n"
        "3\n00:00:10,000 --> 00:00:12,000\nlater line\n\n",
        encoding="utf-8",
    )


def test_srt_build_retimes_fragments(tmp_path):
    transcripts = tmp_path / "raw"
    transcripts.mkdir()
    _write_raw_srt(transcripts / "clipA.srt")

    fragments = tmp_path / "f.yaml"
    fragments.write_text(
        "fragments:\n"
        "  - file: clipA.MP4\n    in: '00:00:00.000'\n    out: '00:00:05.000'\n"
        "  - file: clipA.MP4\n    in: '00:00:10.000'\n    out: '00:00:13.000'\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.srt"
    summary = sf.build(fragments, str(transcripts), str(out), quiet=True)

    assert summary["lines"] == 3
    assert summary["duration"] == pytest.approx(8.0)
    text = out.read_text(encoding="utf-8")
    assert "intro line" in text and "later line" in text
    later_block = [b for b in text.strip().split("\n\n") if "later line" in b][0]
    assert "00:00:05,000 --> 00:00:07,000" in later_block


def test_srt_main_invalid_fragments_returns_2(tmp_path, capsys):
    fragments = tmp_path / "f.yaml"
    fragments.write_text("fragments: []\n", encoding="utf-8")
    rc = sf.main([str(fragments), str(tmp_path), str(tmp_path / "o.srt"), "--json"])
    assert rc == EXIT_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["wall_time_s"] >= 0.0
    assert payload["llm_cost_usd"] == 0.0


def test_srt_main_json_reports_wall_time_and_cost(tmp_path, capsys):
    transcripts = tmp_path / "raw"
    transcripts.mkdir()
    _write_raw_srt(transcripts / "clipA.srt")
    fragments = tmp_path / "f.yaml"
    fragments.write_text(
        "fragments:\n"
        "  - file: clipA.MP4\n    in: '00:00:00.000'\n    out: '00:00:05.000'\n",
        encoding="utf-8",
    )
    rc = sf.main([str(fragments), str(transcripts), str(tmp_path / "o.srt"), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["lines"] == 2
    assert payload["wall_time_s"] >= 0.0
    assert payload["llm_cost_usd"] == 0.0


def test_srt_build_clips_cues_at_fragment_bounds(tmp_path):
    transcripts = tmp_path / "raw"
    transcripts.mkdir()
    (transcripts / "clipA.srt").write_text(
        "1\n00:00:01,000 --> 00:00:06,000\nstraddles the head\n\n"
        "2\n00:00:07,000 --> 00:00:12,000\nstraddles the tail\n\n",
        encoding="utf-8",
    )
    fragments = tmp_path / "f.yaml"
    fragments.write_text(
        "fragments:\n"
        "  - file: clipA.MP4\n    in: '00:00:03.000'\n    out: '00:00:10.000'\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.srt"
    summary = sf.build(fragments, str(transcripts), str(out), quiet=True)

    assert summary["lines"] == 2
    text = out.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:03,000" in text
    assert "00:00:04,000 --> 00:00:07,000" in text


def test_kdenlive_main_json_reports_wall_time_and_cost(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "f.yaml"
    cfg.write_text(
        "source_dir: /footage/dji\n"
        "output: " + str(tmp_path / "Project.kdenlive") + "\n"
        "fragments:\n"
        "  - file: DJI_20260816120000_0001_D.MP4\n"
        "    in: '00:00:05.000'\n"
        "    out: '00:00:12.500'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(kf, "probe_duration", lambda path: 30.0)
    rc = kf.main([str(cfg), "--skip-melt", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["output"].endswith("Project.kdenlive")
    assert payload["wall_time_s"] >= 0.0
    assert payload["llm_cost_usd"] == 0.0


def test_kdenlive_main_failed_probe_returns_3(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "f.yaml"
    cfg.write_text(
        "source_dir: /footage/dji\n"
        "output: " + str(tmp_path / "Project.kdenlive") + "\n"
        "fragments:\n"
        "  - file: DJI_20260816120000_0001_D.MP4\n"
        "    in: '00:00:05.000'\n"
        "    out: '00:00:12.500'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(kf, "probe_duration", lambda path: None)
    rc = kf.main([str(cfg), "--skip-melt", "--json"])
    assert rc == EXIT_RUNTIME
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["exit_code"] == EXIT_RUNTIME