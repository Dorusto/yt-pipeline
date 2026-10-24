import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import subs_from_edit
import transcribe
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME

EXPECTED_TEXT = "Bună ziua. Aceasta este o înregistrare de test."
EXPECTED_WORDS = [
    {"word": "Bună", "start": 0.0, "end": 0.4, "probability": 0.9},
    {"word": "ziua.", "start": 0.5, "end": 1.0, "probability": 0.9},
    {"word": "Aceasta", "start": 1.2, "end": 1.7, "probability": 0.9},
    {"word": "este", "start": 1.8, "end": 2.1, "probability": 0.9},
    {"word": "o", "start": 2.2, "end": 2.3, "probability": 0.9},
    {"word": "înregistrare", "start": 2.4, "end": 3.2, "probability": 0.9},
    {"word": "de", "start": 3.3, "end": 3.5, "probability": 0.9},
    {"word": "test.", "start": 3.6, "end": 4.2, "probability": 0.9},
]
EXPECTED_SRT = "1\n00:00:00,000 --> 00:00:04,200\nBună ziua. Aceasta este o înregistrare de test.\n"


def run_mock(tmp_path, capsys, name=None, extra=None):
    media = tmp_path / "camera A.mp4"
    media.write_bytes(b"stand-in media for the mock backend")
    out = tmp_path / "out"
    argv = [str(media), "--backend", "mock", "--out-dir", str(out), "--json"]
    if name is not None:
        argv += ["--name", name]
    argv += extra or []
    rc = transcribe.main(argv)
    summary = json.loads(capsys.readouterr().out)
    return rc, out, summary


def test_mock_end_to_end_srt(tmp_path, capsys):
    rc, out, _ = run_mock(tmp_path, capsys)
    assert rc == 0
    assert (out / "camera A.srt").read_text(encoding="utf-8") == EXPECTED_SRT


def test_mock_end_to_end_words_json(tmp_path, capsys):
    rc, out, _ = run_mock(tmp_path, capsys)
    assert rc == 0
    data = json.loads((out / "camera A_words.json").read_text(encoding="utf-8"))
    assert data["text"] == EXPECTED_TEXT
    assert data["language"] == "ro"
    assert len(data["segments"]) == 1
    segment = data["segments"][0]
    assert segment["id"] == 0 and segment["start"] == 0.0 and segment["end"] == 4.2
    assert segment["words"] == EXPECTED_WORDS


def test_json_summary_fields(tmp_path, capsys):
    rc, out, summary = run_mock(tmp_path, capsys)
    assert rc == 0
    assert summary["backend"] == "mock"
    assert summary["model"] == "tiny"
    assert summary["device"] == "mock"
    assert summary["language"] == "ro"
    assert summary["segments"] == 1
    assert summary["words"] == 8
    assert summary["llm_cost_usd"] == 0.0
    assert summary["wall_time_s"] >= 0.0
    assert Path(summary["outputs"]["srt"]) == out / "camera A.srt"
    assert Path(summary["outputs"]["words_json"]) == out / "camera A_words.json"


def test_default_model_is_language_and_device_aware():
    assert transcribe.default_model("cuda", "ro") == "turbo"
    assert transcribe.default_model("cpu", "ro") == "medium"
    assert transcribe.default_model("cpu", "en") == "small"
    assert transcribe.default_model("cpu", None) == "medium"
    assert transcribe.default_model("cpu", "auto") == "medium"


def test_model_flag_overrides_the_default(tmp_path, capsys):
    rc, out, summary = run_mock(tmp_path, capsys, extra=["--model", "large-v3"])
    assert rc == 0
    assert summary["model"] == "large-v3"


def test_env_var_overrides_the_default(monkeypatch):
    monkeypatch.setenv(transcribe.WHISPER_MODEL_ENV, "large-v2")
    assert transcribe.resolve_model(None, "ro", "cpu", config={}) == "large-v2"


def test_config_model_overrides_the_default(tmp_path, monkeypatch):
    monkeypatch.delenv(transcribe.WHISPER_MODEL_ENV, raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "models": {"coder": "x"},
                "llm_budget_usd": 1.0,
                "llm_backend": "mock",
                "transcribe": {"model": "large-v3"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("YT_CONFIG", str(config_path))
    assert transcribe.resolve_model(None, "ro", "cpu") == "large-v3"


def test_name_overrides_input_stem(tmp_path, capsys):
    rc, out, summary = run_mock(tmp_path, capsys, name="take 2")
    assert rc == 0
    assert (out / "take 2.srt").is_file()
    assert (out / "take 2_words.json").is_file()
    assert summary["outputs"]["srt"].endswith("take 2.srt")


def test_missing_input_exits_2_and_writes_nothing(tmp_path, capsys):
    out = tmp_path / "out"
    rc = transcribe.main([str(tmp_path / "nope.mp4"), "--backend", "mock", "--out-dir", str(out), "--json"])
    assert rc == 2
    assert "nope.mp4" in capsys.readouterr().err
    assert not out.exists()


def test_hidden_whisper_module_is_a_clear_error(tmp_path, monkeypatch, capsys):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "whisper", None)
    out = tmp_path / "out"
    rc = transcribe.main([str(media), "--backend", "whisper", "--out-dir", str(out), "--json"])
    assert rc == EXIT_RUNTIME
    err = capsys.readouterr().err
    assert "openai-whisper is not installed" in err
    assert "pip install openai-whisper" in err
    assert "Traceback" not in err
    assert not (out / "clip.srt").exists()


def test_unknown_backend_exits_2(tmp_path, capsys):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    rc = transcribe.main([str(media), "--backend", "bogus", "--out-dir", str(tmp_path / "out"), "--json"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "bogus" in err and "mock" in err


def test_words_json_is_accepted_by_subs_from_edit(tmp_path, capsys):
    rc, out, _ = run_mock(tmp_path, capsys)
    assert rc == 0

    words_dir = tmp_path / "words"
    words_dir.mkdir()
    shutil.copyfile(out / "camera A_words.json", words_dir / "camera A.json")

    edit = {
        "sources": [{"file": "camera A.mp4", "path": "/footage/camera A.mp4"}],
        "video": [
            {"id": "v1", "clip": "camera A.mp4", "in_s": 0.0, "out_s": 4.2, "score": 1.0, "reason": "x"}
        ],
    }
    edit_path = tmp_path / "edit.json"
    edit_path.write_text(json.dumps(edit), encoding="utf-8")

    subs_out = tmp_path / "subs"
    rc2 = subs_from_edit.main([
        "--from-edit", str(edit_path), "--words-dir", str(words_dir),
        "--out-dir", str(subs_out), "--json",
    ])
    assert rc2 == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["words_kept"] == 8
    assert summary["words_dropped"] == 0
    words = json.loads((subs_out / "edit_words.json").read_text(encoding="utf-8"))
    assert words[0] == {"word": "Bună", "start": 0.0, "end": 0.4}


def test_words_json_shifted_by_a_leading_photo_slot(tmp_path, capsys):
    rc, out, _ = run_mock(tmp_path, capsys)
    assert rc == 0

    words_dir = tmp_path / "words"
    words_dir.mkdir()
    shutil.copyfile(out / "camera A_words.json", words_dir / "camera A.json")

    edit = {
        "sources": [
            {"file": "camera A.mp4", "path": "/footage/camera A.mp4"},
            {"file": "pic.jpg", "path": "/footage/pic.jpg", "media_type": "photo"},
        ],
        "video": [
            {"id": "p1", "kind": "photo", "photo": "pic.jpg", "duration_s": 1.0,
             "ken_burns": {"move": "zoom_in", "amount": 0.08, "focus_x": 0.5, "focus_y": 0.5},
             "score": 1.0, "reason": "x"},
            {"id": "v1", "clip": "camera A.mp4", "in_s": 0.0, "out_s": 4.2, "score": 1.0, "reason": "x"},
        ],
    }
    edit_path = tmp_path / "edit.json"
    edit_path.write_text(json.dumps(edit), encoding="utf-8")

    subs_out = tmp_path / "subs"
    rc2 = subs_from_edit.main([
        "--from-edit", str(edit_path), "--words-dir", str(words_dir),
        "--out-dir", str(subs_out), "--json",
    ])
    assert rc2 == 0
    words = json.loads((subs_out / "edit_words.json").read_text(encoding="utf-8"))
    assert words[0] == {"word": "Bună", "start": 1.0, "end": 1.4}
    assert words[-1] == {"word": "test.", "start": 4.6, "end": 5.2}


def make_sine_wav(path):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", "-ar", "48000", "-ac", "2", str(path)],
        check=True,
    )


def test_extract_audio_writes_mono_16k_wav(tmp_path):
    source = tmp_path / "tone.wav"
    make_sine_wav(source)
    target = tmp_path / "extracted.wav"
    transcribe.extract_audio(source, target)
    assert target.is_file() and target.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels", "-of", "json", str(target)],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["sample_rate"] == "16000"
    assert stream["channels"] == 1


def test_extract_audio_error_names_the_input(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_text("definitely not audio")
    with pytest.raises(transcribe.TranscribeRuntimeError) as excinfo:
        transcribe.extract_audio(bad, tmp_path / "out.wav")
    assert "bad.wav" in str(excinfo.value)


def test_build_srt_uses_rounded_milliseconds():
    result = {
        "segments": [
            {"id": 0, "start": 0.0, "end": 4.2, "text": " hello "},
            {"id": 1, "start": 65.4321, "end": 3661.9999, "text": "world"},
        ]
    }
    assert transcribe.build_srt(result) == (
        "1\n00:00:00,000 --> 00:00:04,200\nhello\n\n"
        "2\n00:01:05,432 --> 01:01:02,000\nworld\n"
    )
