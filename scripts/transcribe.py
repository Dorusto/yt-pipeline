#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Transcribe a media file in one Whisper run and write a raw ``.srt`` plus a word JSON.

The word JSON is the Whisper result shape (``segments[].words[]`` with ``word``,
``start``, ``end``), exactly what ``subs_from_edit.py --words-dir`` reads. This
is the fallback transcription step when no edit project or word JSONs exist yet.

Usage:
    python3 transcribe.py INPUT --out-dir DIR [--name NAME] [--model MODEL]
    python3 transcribe.py INPUT --backend mock --out-dir DIR --json

The ``whisper`` (and ``torch``) imports happen lazily inside the whisper backend
only, so the tool runs and the test suite passes with neither installed. The mock
backend is deterministic and offline. No model service is called; ``llm_cost_usd``
is always 0.0.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from common.config import ConfigError, load_config
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error

MISSING_WHISPER = (
    "openai-whisper is not installed (pip install openai-whisper); "
    "use --backend mock for offline runs"
)
EXTRACTION_RATE = 16000
MOCK_MODEL = "tiny"
GPU_MODEL = "turbo"
CPU_ENGLISH_MODEL = "small"
CPU_NON_ENGLISH_MODEL = "medium"
WHISPER_MODEL_ENV = "YT_PIPELINE_WHISPER_MODEL"
BACKENDS = ("whisper", "mock")

MOCK_TEXT = "Bună ziua. Aceasta este o înregistrare de test."
MOCK_WORD_TIMES = (
    ("Bună", 0.0, 0.4),
    ("ziua.", 0.5, 1.0),
    ("Aceasta", 1.2, 1.7),
    ("este", 1.8, 2.1),
    ("o", 2.2, 2.3),
    ("înregistrare", 2.4, 3.2),
    ("de", 3.3, 3.5),
    ("test.", 3.6, 4.2),
)


class TranscribeRuntimeError(Exception):
    """Raised when transcription fails after it started (whisper, ffmpeg)."""


class TranscribeError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


class Transcriber:
    """Backend interface: transcribe a media file and return the Whisper result dict."""

    name = "base"
    device = "cpu"
    model_name = MOCK_MODEL

    def transcribe(self, media_path, language):
        raise NotImplementedError


class MockTranscriber(Transcriber):
    """Deterministic offline backend: any input returns the same short result."""

    name = "mock"
    device = "mock"

    def __init__(self, model=None):
        self.model_name = model or MOCK_MODEL

    def transcribe(self, media_path, language):
        words = [
            {"word": word, "start": start, "end": end, "probability": 0.9}
            for word, start, end in MOCK_WORD_TIMES
        ]
        return {
            "text": MOCK_TEXT,
            "language": "ro",
            "segments": [
                {"id": 0, "start": 0.0, "end": 4.2, "text": MOCK_TEXT, "words": words}
            ],
        }


def _cuda_available():
    try:
        import torch
    except ImportError:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def default_model(device, language):
    """The Whisper model for a device and language: turbo on GPU, medium/small on CPU."""
    if device == "cuda":
        return GPU_MODEL
    if language == "en":
        return CPU_ENGLISH_MODEL
    return CPU_NON_ENGLISH_MODEL


def resolve_model(cli_model=None, language="ro", device="cpu", config=None):
    """The Whisper model: --model, else the env var, else transcribe.model, else the default."""
    if cli_model:
        return cli_model
    from_env = os.environ.get(WHISPER_MODEL_ENV)
    if from_env:
        return from_env
    data = config if config is not None else load_config()
    section = data.get("transcribe")
    if isinstance(section, dict):
        value = section.get("model")
        if value:
            return value
    return default_model(device, language)


class WhisperTranscriber(Transcriber):
    """Real backend: one ``openai-whisper`` run with word timestamps."""

    name = "whisper"

    def __init__(self, model=None, language="ro"):
        try:
            import whisper
        except ImportError:
            raise TranscribeRuntimeError(MISSING_WHISPER)
        self._whisper = whisper
        self.device = "cuda" if _cuda_available() else "cpu"
        self.model_name = model or resolve_model(None, language, self.device)
        self.model = None

    def transcribe(self, media_path, language):
        if self.model is None:
            try:
                self.model = self._whisper.load_model(self.model_name, device=self.device)
            except Exception as exc:
                raise TranscribeRuntimeError(
                    f"failed to load Whisper model '{self.model_name}': {exc}"
                )
        language_arg = None if language in (None, "auto") else language
        try:
            return self.model.transcribe(
                str(media_path), language=language_arg, word_timestamps=True, verbose=False
            )
        except Exception as exc:
            raise TranscribeRuntimeError(f"Whisper failed on '{media_path}': {exc}")


def make_transcriber(backend, model, language="ro"):
    if backend == "mock":
        return MockTranscriber(model)
    if backend == "whisper":
        return WhisperTranscriber(model, language)
    raise TranscribeError(f"unknown --backend '{backend}'; use one of {', '.join(BACKENDS)}")


def ffmpeg_binary():
    return shutil.which("ffmpeg") or "ffmpeg"


def extract_audio(media_path, out_path):
    """Extract the media file's audio to a mono 16 kHz wav with ffmpeg."""
    command = [
        ffmpeg_binary(),
        "-y",
        "-v",
        "error",
        "-i",
        str(media_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(EXTRACTION_RATE),
        "-f",
        "wav",
        str(out_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise TranscribeRuntimeError(f"could not run ffmpeg: {exc}")
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()
        detail = tail[-1] if tail else "unknown error"
        raise TranscribeRuntimeError(f"ffmpeg could not extract audio from {media_path}: {detail}")
    return Path(out_path)


def format_timestamp(seconds):
    total_ms = max(0, int(round(float(seconds) * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_srt(result):
    """Build a standard SRT from the Whisper result segments."""
    blocks = []
    for index, segment in enumerate(result.get("segments") or [], 1):
        start = format_timestamp(segment.get("start", 0.0))
        end = format_timestamp(segment.get("end", 0.0))
        text = str(segment.get("text", "")).strip()
        blocks.append(f"{index}\n{start} --> {end}\n{text}")
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n"


def count_words(result):
    return sum(len(segment.get("words") or []) for segment in result.get("segments") or [])


def resolve_output_stem(input_path, name):
    return name or input_path.stem


def run(input_path, out_dir=None, name=None, model=None, language="ro", backend="whisper"):
    """Transcribe ``input_path`` and write ``<name>.srt`` and ``<name>_words.json``."""
    started = time.monotonic()
    if backend not in BACKENDS:
        raise TranscribeError(f"unknown --backend '{backend}'; use one of {', '.join(BACKENDS)}")

    input_path = Path(input_path).expanduser()
    if not input_path.is_file():
        raise TranscribeError(f"input file not found: {input_path}")
    if not os.access(input_path, os.R_OK):
        raise TranscribeError(f"input file is not readable: {input_path}")

    try:
        transcriber = make_transcriber(backend, model, language)
    except ConfigError as exc:
        raise TranscribeError(str(exc))
    out_path = Path(out_dir).expanduser() if out_dir else input_path.parent
    output_name = resolve_output_stem(input_path, name)

    if backend == "whisper":
        with tempfile.TemporaryDirectory(prefix="transcribe_") as temporary_dir:
            extracted = Path(temporary_dir) / "audio.wav"
            extract_audio(input_path, extracted)
            result = transcriber.transcribe(extracted, language)
    else:
        result = transcriber.transcribe(input_path, language)

    srt_path = out_path / f"{output_name}.srt"
    words_path = out_path / f"{output_name}_words.json"
    out_path.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(build_srt(result), encoding="utf-8")
    words_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    detected_language = result.get("language") or (None if language in (None, "auto") else language) or "auto"
    return {
        "outputs": {"srt": str(srt_path), "words_json": str(words_path)},
        "backend": transcriber.name,
        "model": transcriber.model_name,
        "device": transcriber.device,
        "language": detected_language,
        "segments": len(result.get("segments") or []),
        "words": count_words(result),
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def build_parser():
    parser = argparse.ArgumentParser(
        prog="transcribe.py",
        description=(
            "Transcribe a media file in one Whisper run and write a raw .srt plus a "
            "Whisper word JSON for subs_from_edit.py."
        ),
        epilog=(
            "example:\n"
            "  python3 scripts/transcribe.py clip.mp4 --out-dir out/ --json\n"
            "  python3 scripts/transcribe.py clip.mp4 --backend mock --out-dir out/\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="audio or video file to transcribe (read-only)")
    parser.add_argument("--out-dir", default=None, help="output folder (default: the input file's parent)")
    parser.add_argument("--name", default=None, help="output stem (default: the input file stem)")
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Whisper model id (default: "
            f"{GPU_MODEL} with a CUDA GPU, else {CPU_NON_ENGLISH_MODEL} on CPU for a "
            f"non-English language (including auto or no language), {CPU_ENGLISH_MODEL} "
            f"only for an explicit en; or {WHISPER_MODEL_ENV}, or "
            "transcribe.model in the config)"
        ),
    )
    parser.add_argument(
        "--language",
        default="ro",
        help="Whisper language code; 'auto' lets Whisper detect (default: ro)",
    )
    parser.add_argument(
        "--backend",
        default="whisper",
        metavar="{whisper,mock}",
        help="whisper (real model) or mock (deterministic, offline); default: whisper",
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = run(
            args.input,
            out_dir=args.out_dir,
            name=args.name,
            model=args.model,
            language=args.language,
            backend=args.backend,
        )
    except TranscribeRuntimeError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except TranscribeError as exc:
        return report_error(exc, EXIT_USAGE, args.json)

    if args.json:
        print(json.dumps(summary))
    else:
        print(
            f"written: {summary['outputs']['srt']} and {summary['outputs']['words_json']} "
            f"({summary['segments']} segments, {summary['words']} words)"
        )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
