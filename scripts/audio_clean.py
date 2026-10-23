#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measure and clean a clip's ambient sound: wind, noise and loudness.

Usage:
    python scripts/audio_clean.py INPUT.mp4 --out cleaned.wav
    python scripts/audio_clean.py INPUT.mp4 --out cleaned.m4a --denoiser afftdn --target-lufs -16
    python scripts/audio_clean.py INPUT.mp4 --report audio_report.json --analyse-only --json

The tool decodes the clip's audio to mono 48 kHz PCM in a temporary folder and
measures per-2-second RMS, the low-frequency (wind) share of the energy, a
stationary noise floor and an SNR, then writes a one-word verdict. Unless
``--analyse-only`` is given it cleans the audio with one ffmpeg pass: an optional
wind high-pass, ``arnndn`` (RNNoise) or ``afftdn`` noise reduction and
``loudnorm``. The source is read only; cleaned audio and the JSON report are
written atomically. No model service is called; ``llm_cost_usd`` is always 0.0.
"""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from common.ambience import (
    DEFAULT_MODEL,
    DEFAULT_TARGET_LUFS,
    DEFAULT_WIND_HZ,
    AmbienceError,
    build_filters,
    resolve_denoiser,
)
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.errors import report_error
from common.ffmpeg_tools import ffmpeg_path

ANALYSIS_RATE = 48000
WINDOW_S = 2.0
WIND_BAND_LOW_HZ = 20.0
NOISE_BAND_HIGH_HZ = 8000.0
WIND_RATIO_THRESHOLD = 0.55
NOISE_SNR_THRESHOLD_DB = 12.0
QUIET_DBFS = -45.0
SILENCE_FLOOR_DBFS = -120.0

CODECS = {
    ".wav": ["-c:a", "pcm_s16le"],
    ".m4a": ["-c:a", "aac", "-b:a", "192k"],
    ".mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
    ".flac": ["-c:a", "flac"],
}


class AudioCleanError(RuntimeError):
    """Raised when a clip's audio cannot be measured or cleaned."""


class AudioCleanRuntimeError(AudioCleanError):
    """Raised when ffmpeg or ffprobe fails after the run started."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="audio_clean.py",
        description=(
            "Measure a clip's ambient sound (per-segment RMS, wind, noise) and "
            "clean it with ffmpeg: wind high-pass, RNNoise/afftdn and loudnorm."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/audio_clean.py INPUT.mp4 --out cleaned.wav\n"
            "  python scripts/audio_clean.py INPUT.mp4 --out cleaned.m4a"
            " --denoiser afftdn --target-lufs -16\n"
            "  python scripts/audio_clean.py INPUT.mp4 --report audio_report.json"
            " --analyse-only --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="video or audio file to read (read-only)")
    parser.add_argument("--out", default=None, help="cleaned audio file to write (.wav, .m4a, .mp3, .flac)")
    parser.add_argument("--report", default=None, help="write the measurements as JSON")
    parser.add_argument("--analyse-only", action="store_true", help="measure and report without writing cleaned audio")
    parser.add_argument(
        "--denoiser",
        choices=["arnndn", "afftdn", "none"],
        default=None,
        help="noise reduction backend (default: arnndn with a model, else afftdn)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"RNNoise model for arnndn (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--wind-hz",
        type=float,
        default=None,
        help=f"high-pass corner for wind rumble, 0 disables it (default: {DEFAULT_WIND_HZ:g})",
    )
    parser.add_argument(
        "--target-lufs",
        type=float,
        default=DEFAULT_TARGET_LUFS,
        help=f"loudnorm integrated loudness target (default: {DEFAULT_TARGET_LUFS:g})",
    )
    parser.add_argument("--no-loudnorm", action="store_true", help="disable the loudness pass")
    parser.add_argument("--force", action="store_true", help="rewrite --out and --report when they exist")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def _dbfs(rms):
    if rms <= 0:
        return SILENCE_FLOOR_DBFS
    return max(SILENCE_FLOOR_DBFS, 20.0 * math.log10(rms))


def _ffprobe_path():
    return shutil.which("ffprobe") or "ffprobe"


def probe_audio_info(path):
    """Return ``{"sample_rate", "channels"}`` for the first audio stream, or None."""
    command = [
        _ffprobe_path(),
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=sample_rate,channels",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise AudioCleanRuntimeError(f"could not run ffprobe: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise AudioCleanRuntimeError(f"ffprobe failed for {path}: {detail[-1] if detail else 'unknown error'}")
    try:
        streams = json.loads(result.stdout).get("streams") or []
    except json.JSONDecodeError as exc:
        raise AudioCleanRuntimeError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc
    if not streams:
        return None
    stream = streams[0]
    return {
        "sample_rate": int(stream.get("sample_rate") or ANALYSIS_RATE),
        "channels": int(stream.get("channels") or 1),
    }


def decode_mono(path, directory):
    """Decode ``path`` to mono 48 kHz PCM in ``directory`` and return float samples."""
    target = Path(directory) / "analysis.pcm"
    command = [
        ffmpeg_path(),
        "-y",
        "-v",
        "error",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(ANALYSIS_RATE),
        "-f",
        "s16le",
        str(target),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise AudioCleanRuntimeError(f"could not run ffmpeg: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise AudioCleanRuntimeError(f"ffmpeg could not decode {path}: {detail[-1] if detail else 'unknown error'}")
    data = np.fromfile(target, dtype="<i2")
    return data.astype(np.float64) / 32768.0


def _wind_energies(chunk, wind_hz):
    count = len(chunk)
    if count < 2 or wind_hz <= WIND_BAND_LOW_HZ:
        return 0.0, 0.0
    tapered = chunk * np.hanning(count)
    power = np.abs(np.fft.rfft(tapered)) ** 2
    freqs = np.fft.rfftfreq(count, d=1.0 / ANALYSIS_RATE)
    total = float(power[(freqs >= WIND_BAND_LOW_HZ) & (freqs <= NOISE_BAND_HIGH_HZ)].sum())
    low = float(power[(freqs >= WIND_BAND_LOW_HZ) & (freqs <= wind_hz)].sum())
    return low, total


def _verdict(overall_rms_dbfs, wind_detected, noise_detected):
    if overall_rms_dbfs < QUIET_DBFS:
        return "quiet"
    parts = []
    if wind_detected:
        parts.append("windy")
    if noise_detected:
        parts.append("noisy")
    return "+".join(parts) if parts else "clean"


def measure(samples, wind_hz):
    """Measure the ambient sound of mono samples and return the measurement object."""
    count = len(samples)
    duration_s = count / ANALYSIS_RATE
    overall_rms = float(np.sqrt(np.mean(np.square(samples)))) if count else 0.0
    window_samples = int(round(WINDOW_S * ANALYSIS_RATE))
    segments = []
    window_rms = []
    total_low = 0.0
    total_band = 0.0
    windy_windows = 0
    if count and window_samples > 0:
        window_count = math.ceil(count / window_samples)
        for index in range(window_count):
            start = index * window_samples
            end = min(start + window_samples, count)
            chunk = samples[start:end]
            rms = float(np.sqrt(np.mean(np.square(chunk)))) if len(chunk) else 0.0
            low, band = _wind_energies(chunk, wind_hz)
            total_low += low
            total_band += band
            ratio = low / band if band > 0 else 0.0
            if ratio >= WIND_RATIO_THRESHOLD:
                windy_windows += 1
            window_rms.append(rms)
            segments.append(
                {
                    "start_s": round(start / ANALYSIS_RATE, 3),
                    "end_s": round(end / ANALYSIS_RATE, 3),
                    "rms_dbfs": round(_dbfs(rms), 3),
                    "wind_ratio": round(ratio, 4),
                }
            )
    window_db = [_dbfs(rms) for rms in window_rms]
    noise_floor_dbfs = float(np.percentile(window_db, 10)) if window_db else SILENCE_FLOOR_DBFS
    mean_window_rms = float(np.mean(window_rms)) if window_rms else 0.0
    snr_db = _dbfs(mean_window_rms) - noise_floor_dbfs
    wind_ratio = total_low / total_band if total_band > 0 else 0.0
    wind_detected = bool(window_rms) and 2 * windy_windows >= len(window_rms)
    noise_detected = snr_db < NOISE_SNR_THRESHOLD_DB
    overall_rms_dbfs = _dbfs(overall_rms)
    return {
        "duration_s": round(duration_s, 3),
        "sample_rate": ANALYSIS_RATE,
        "rms_dbfs": round(overall_rms_dbfs, 3),
        "segments": segments,
        "wind": {"wind_ratio": round(wind_ratio, 4), "wind_detected": wind_detected},
        "noise": {
            "noise_floor_dbfs": round(noise_floor_dbfs, 3),
            "snr_db": round(snr_db, 3),
            "noise_detected": noise_detected,
        },
        "verdict": _verdict(overall_rms_dbfs, wind_detected, noise_detected),
    }


def encode_clean(input_path, out_path, info, filters):
    """Run the audio-only cleanup pass and replace ``out_path`` atomically."""
    extension = out_path.suffix.lower()
    if extension not in CODECS:
        raise AudioCleanError(
            f"unsupported output container {extension or '(none)'}; use .wav, .m4a, .mp3 or .flac"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_name(out_path.name + ".tmp" + out_path.suffix)
    command = [ffmpeg_path(), "-y", "-v", "error", "-i", str(input_path), "-vn"]
    if filters:
        command += ["-af", ",".join(filters)]
    command += [
        "-ar",
        str(info["sample_rate"]),
        "-ac",
        str(info["channels"]),
        *CODECS[extension],
        str(temporary),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            raise AudioCleanRuntimeError(f"ffmpeg failed: {detail[-1] if detail else 'unknown error'}")
        os.replace(temporary, out_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _outputs_current(input_path, targets):
    input_mtime = input_path.stat().st_mtime_ns
    if not targets:
        return False
    for target in targets:
        if not target.exists() or target.stat().st_mtime_ns < input_mtime:
            return False
    return True


def _summary(status, input_path, out_path, report_path, denoiser, before, after, applied, warnings, started):
    return {
        "status": status,
        "out": str(out_path) if out_path is not None else None,
        "report": str(report_path) if report_path is not None else None,
        "input": str(input_path),
        "denoiser": denoiser,
        "before": before,
        "after": after,
        "applied": applied,
        "warnings": warnings,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def run(
    input_path,
    out_path=None,
    report_path=None,
    analyse_only=False,
    denoiser="__default__",
    model=None,
    wind_hz=None,
    target_lufs=DEFAULT_TARGET_LUFS,
    no_loudnorm=False,
    force=False,
):
    """Measure a clip's audio, clean it unless ``analyse_only``, and return a summary."""
    started = time.monotonic()
    input_path = Path(input_path).expanduser()
    if not input_path.is_file():
        raise AudioCleanError(f"input not found: {input_path}")
    out_path = Path(out_path).expanduser() if out_path is not None else None
    report_path = Path(report_path).expanduser() if report_path is not None else None
    if analyse_only:
        out_path = None
    if out_path is None and not analyse_only:
        raise AudioCleanError("--out is required unless --analyse-only")

    requested = None if denoiser == "__default__" else denoiser
    model_path = Path(model).expanduser() if model is not None else DEFAULT_MODEL
    effective_wind_hz = DEFAULT_WIND_HZ if wind_hz is None else float(wind_hz)
    explicit_wind = wind_hz is not None

    targets = []
    if not analyse_only and out_path is not None:
        targets.append(out_path)
    if report_path is not None:
        targets.append(report_path)
    if not force and _outputs_current(input_path, targets):
        return _summary("skipped", input_path, out_path, report_path, requested, None, None, [], [], started)

    try:
        resolved_denoiser, warnings = resolve_denoiser(requested, model_path)
    except AmbienceError as exc:
        raise AudioCleanError(str(exc)) from exc
    if analyse_only:
        warnings = []
    info = probe_audio_info(input_path)

    if info is None:
        if not analyse_only and out_path is not None:
            raise AudioCleanError(f"no audio stream in {input_path}")
        warnings = warnings + [f"no audio stream in {input_path}"]
        summary = _summary(
            "ok", input_path, out_path, report_path, resolved_denoiser, None, None, [], warnings, started
        )
        if report_path is not None:
            _atomic_write_json(report_path, summary)
        return summary

    with tempfile.TemporaryDirectory(prefix="audio_clean_") as temporary_dir:
        before = measure(decode_mono(input_path, temporary_dir), effective_wind_hz)

        if analyse_only:
            summary = _summary(
                "ok", input_path, out_path, report_path, resolved_denoiser, before, None, [], warnings, started
            )
            if report_path is not None:
                _atomic_write_json(report_path, summary)
            return summary

        apply_highpass = effective_wind_hz > 0 and (before["wind"]["wind_detected"] or explicit_wind)
        apply_loudnorm = not no_loudnorm
        filters, applied = build_filters(
            resolved_denoiser,
            model_path,
            effective_wind_hz,
            apply_highpass,
            apply_loudnorm,
            target_lufs,
        )
        encode_clean(input_path, out_path, info, filters)
        after = measure(decode_mono(out_path, temporary_dir), effective_wind_hz)

    summary = _summary(
        "written",
        input_path,
        out_path,
        report_path,
        resolved_denoiser,
        before,
        after,
        applied,
        warnings,
        started,
    )
    if report_path is not None:
        _atomic_write_json(report_path, summary)
    return summary


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = run(
            args.input,
            out_path=args.out,
            report_path=args.report,
            analyse_only=args.analyse_only,
            denoiser=args.denoiser if args.denoiser is not None else "__default__",
            model=args.model,
            wind_hz=args.wind_hz,
            target_lufs=args.target_lufs,
            no_loudnorm=args.no_loudnorm,
            force=args.force,
        )
    except AudioCleanRuntimeError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except AudioCleanError as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    if args.json:
        print(json.dumps(summary))
    else:
        if summary["status"] == "skipped":
            print(f"skipped {summary['out'] or summary['report']} (up to date)")
        else:
            verdict = summary["before"]["verdict"] if summary["before"] else "no audio"
            print(f"{verdict}: {summary['input']}")
            if summary["out"]:
                print(f"wrote {summary['out']} ({', '.join(summary['applied']) or 'no filters'})")
            if summary["report"]:
                print(f"wrote {summary['report']}")
        for warning in summary["warnings"]:
            print(f"warning: {warning}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
