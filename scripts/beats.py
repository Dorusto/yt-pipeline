#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Detect a music file's tempo, beats, downbeats and energy, and plan a cut grid.

Usage:
    python scripts/beats.py song.mp3 --json
    python scripts/beats.py song.mp3 --out beats.json
    python scripts/beats.py song.mp3 --out beats.json --target-duration-s 90 --slots 15

The music is decoded to mono 22.05 kHz PCM in a temporary folder and analysed in
numpy only: a spectral-flux onset envelope, autocorrelation for the beat period,
a least-squares phase fit for the beat grid, four-four downbeats and a per-0.5 s
RMS energy curve. With ``--target-duration-s`` the tool also returns a cut grid:
edit-time boundaries snapped to the music's beats so an edit's cuts land on the
music. The source is read only; ``--out`` is written atomically. No model service
is called, so ``llm_cost_usd`` is always 0.0.
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

from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.ffmpeg_tools import ffmpeg_path

ANALYSIS_RATE = 22050
STFT_WINDOW = 1024
STFT_HOP = 256
PEAK_HALF_WINDOW = 10
DOWNBEAT_HALF_WINDOW = 3
ENERGY_WINDOW_S = 0.5
MIN_BPM = 40.0
MAX_BPM = 220.0
SILENCE_FLOOR_DBFS = -120.0
TEMPO_PEAK_RATIO = 0.9
BEATS_PER_BAR = 4
DEFAULT_SLOTS_MIN = 4
DEFAULT_SLOTS_MAX = 24
DEFAULT_SLOT_SECONDS = 6.0


class BeatsError(RuntimeError):
    """Raised when a music file cannot be read or analysed."""


class BeatsRuntimeError(BeatsError):
    """Raised when ffmpeg or ffprobe fails after the run started."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="beats.py",
        description=(
            "Detect a music file's tempo, beat and downbeat times and energy curve, "
            "and plan a beat-aligned cut grid for an edit of a given length."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/beats.py song.mp3 --json\n"
            "  python scripts/beats.py song.mp3 --out beats.json\n"
            "  python scripts/beats.py song.mp3 --out beats.json"
            " --target-duration-s 90 --slots 15\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("music", help="music file to read (read-only): .mp3, .wav, .m4a, .flac, .ogg")
    parser.add_argument("--out", default=None, help="write the full result as JSON")
    parser.add_argument(
        "--target-duration-s",
        type=float,
        default=None,
        help="edit length in seconds; also compute the beat-aligned cut grid",
    )
    parser.add_argument(
        "--slots",
        type=int,
        default=None,
        help=(
            "number of slots the cut grid should provide"
            f" (default: clamp(round(target/{DEFAULT_SLOT_SECONDS:g}), {DEFAULT_SLOTS_MIN}, {DEFAULT_SLOTS_MAX}))"
        ),
    )
    parser.add_argument(
        "--min-beat-s",
        type=float,
        default=0.25,
        help="shortest plausible beat period in seconds (default: 0.25)",
    )
    parser.add_argument(
        "--max-beat-s",
        type=float,
        default=2.0,
        help="longest plausible beat period in seconds (default: 2.0)",
    )
    parser.add_argument("--force", action="store_true", help="rewrite --out when it exists")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def _dbfs(rms):
    if rms <= 0:
        return SILENCE_FLOOR_DBFS
    return max(SILENCE_FLOOR_DBFS, 20.0 * math.log10(rms))


def _ffprobe_path():
    return shutil.which("ffprobe") or "ffprobe"


def has_audio_stream(path):
    """Return True when ffprobe reports at least one audio stream for ``path``."""
    command = [
        _ffprobe_path(),
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=index",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise BeatsRuntimeError(f"could not run ffprobe: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise BeatsRuntimeError(f"ffprobe failed for {path}: {detail[-1] if detail else 'unknown error'}")
    try:
        streams = json.loads(result.stdout).get("streams") or []
    except json.JSONDecodeError as exc:
        raise BeatsRuntimeError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc
    return bool(streams)


def decode_mono(path, directory):
    """Decode ``path`` to mono 22.05 kHz PCM in ``directory`` and return float samples."""
    target = Path(directory) / "beat_analysis.pcm"
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
        raise BeatsRuntimeError(f"could not run ffmpeg: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise BeatsRuntimeError(f"ffmpeg could not decode {path}: {detail[-1] if detail else 'unknown error'}")
    if not target.is_file() or target.stat().st_size == 0:
        raise BeatsError(f"no audio stream in {path}")
    data = np.fromfile(target, dtype="<i2")
    return data.astype(np.float64) / 32768.0


def onset_envelope(samples, rate=ANALYSIS_RATE, window=STFT_WINDOW, hop=STFT_HOP):
    """Return the spectral-flux onset envelope of mono samples."""
    padded = np.concatenate([np.zeros(window), samples])
    if len(padded) < window:
        return np.zeros(0)
    frames = np.lib.stride_tricks.sliding_window_view(padded, window)[::hop]
    spectra = np.abs(np.fft.rfft(frames * np.hanning(window), axis=1))
    log_magnitude = np.log1p(spectra)
    difference = np.diff(log_magnitude, axis=0)
    return np.maximum(difference, 0.0).sum(axis=1)


def pick_onsets(envelope, rate=ANALYSIS_RATE, hop=STFT_HOP, min_beat_s=0.25, half=PEAK_HALF_WINDOW):
    """Peak-pick the onset envelope with a local adaptive threshold and return onset times."""
    count = len(envelope)
    if count == 0:
        return np.zeros(0)
    kernel = np.ones(2 * half + 1) / (2 * half + 1)
    local_mean = np.convolve(envelope, kernel, mode="same")
    local_square = np.convolve(envelope**2, kernel, mode="same")
    local_std = np.sqrt(np.maximum(local_square - local_mean**2, 0.0))
    threshold = local_mean + 0.5 * local_std
    candidates = [i for i in np.where(envelope > threshold)[0] if _is_local_max(envelope, i, half)]
    min_gap = max(1, int(round(min_beat_s * rate / hop)))
    selected = []
    for index in sorted(candidates, key=lambda i: -envelope[i]):
        if all(abs(index - other) >= min_gap for other in selected):
            selected.append(index)
    selected.sort()
    return np.array([(index + 1) * hop / rate for index in selected])


def _is_local_max(values, index, half):
    low = max(0, index - half)
    high = min(len(values), index + half + 1)
    return values[index] >= values[low:high].max() - 1e-12


def autocorrelation(signal):
    """Return the normalized autocorrelation of a real signal."""
    centred = signal - signal.mean()
    count = len(centred)
    if count == 0:
        return np.zeros(1)
    spectrum = np.fft.rfft(centred, 2 * count)
    correlation = np.fft.irfft(np.abs(spectrum) ** 2)[:count]
    if correlation[0] > 0:
        correlation = correlation / correlation[0]
    return correlation


def estimate_beat_period(envelope, rate=ANALYSIS_RATE, hop=STFT_HOP, min_beat_s=0.25, max_beat_s=2.0):
    """Return ``(period_s, bpm)`` from the onset envelope's autocorrelation."""
    min_lag = max(1, int(round(min_beat_s * rate / hop)))
    max_lag = max(min_lag + 1, int(round(max_beat_s * rate / hop)))
    correlation = autocorrelation(envelope)
    max_lag = min(max_lag, len(correlation) - 1)
    if max_lag <= min_lag:
        return min_beat_s, 60.0 / min_beat_s
    strongest = min_lag + int(np.argmax(correlation[min_lag : max_lag + 1]))
    peaks = [
        lag
        for lag in range(min_lag, max_lag + 1)
        if correlation[lag] >= correlation[lag - 1]
        and correlation[lag] >= correlation[min(lag + 1, max_lag)]
    ]
    strong = [lag for lag in peaks if correlation[lag] >= TEMPO_PEAK_RATIO * correlation[strongest]]
    chosen = min(strong) if strong else strongest
    period = chosen * hop / rate
    bpm = 60.0 / period
    return period, min(MAX_BPM, max(MIN_BPM, bpm))


def snap_beats(onset_times, period, duration_s):
    """Fit a grid to the onsets (least squares) and return ``(beat_times, period)``."""
    if len(onset_times) == 0 or period <= 0:
        return np.zeros(0), period
    indices = np.round(onset_times / period).astype(int)
    phase = float(np.mean(onset_times - indices * period))
    indices = np.round((onset_times - phase) / period).astype(int)
    if len(np.unique(indices)) >= 2:
        slope, intercept = np.polyfit(indices, onset_times, 1)
    else:
        slope, intercept = period, phase
    if slope <= 0:
        slope, intercept = period, phase
    first = int(np.floor((0.0 - intercept) / slope))
    last = int(np.floor((duration_s - intercept) / slope + 1e-9))
    beats = intercept + slope * np.arange(first, last + 1)
    beats = beats[(beats >= -1e-9) & (beats <= duration_s + 1e-6)]
    return beats, float(slope)


def find_downbeats(beat_s, envelope, rate=ANALYSIS_RATE, hop=STFT_HOP, half=DOWNBEAT_HALF_WINDOW):
    """Return the downbeat times: every fourth beat from the strongest bar phase."""
    if len(beat_s) == 0:
        return np.zeros(0)
    strengths = np.array([_onsets_near(envelope, beat, rate, hop, half) for beat in beat_s])
    totals = [strengths[phase::BEATS_PER_BAR].sum() for phase in range(BEATS_PER_BAR)]
    best_phase = int(np.argmax(totals))
    return beat_s[best_phase::BEATS_PER_BAR]


def _onsets_near(envelope, time_s, rate, hop, half):
    centre = int(round(time_s * rate / hop))
    low = max(0, centre - half)
    high = min(len(envelope), centre + half + 1)
    if high <= low:
        return 0.0
    return float(envelope[low:high].sum())


def energy_curve(samples, rate=ANALYSIS_RATE, window_s=ENERGY_WINDOW_S):
    """Return the per-window RMS energy in dBFS as ``[{t_s, dbfs}]``."""
    size = max(1, int(round(window_s * rate)))
    curve = []
    for start in range(0, len(samples), size):
        chunk = samples[start : start + size]
        rms = float(np.sqrt(np.mean(np.square(chunk)))) if len(chunk) else 0.0
        curve.append({"t_s": round(start / rate, 3), "dbfs": round(_dbfs(rms), 3)})
    return curve


def measure(samples, min_beat_s=0.25, max_beat_s=2.0):
    """Measure tempo, beats, downbeats and energy of mono samples."""
    duration_s = len(samples) / ANALYSIS_RATE
    envelope = onset_envelope(samples)
    onsets = pick_onsets(envelope, min_beat_s=min_beat_s)
    warnings = []
    if envelope.size and envelope.max() > 0 and len(onsets):
        period, _ = estimate_beat_period(envelope, min_beat_s=min_beat_s, max_beat_s=max_beat_s)
        beat_s, period = snap_beats(onsets, period, duration_s)
        bpm = min(MAX_BPM, max(MIN_BPM, 60.0 / period)) if period > 0 else 0.0
    else:
        period, bpm, beat_s = 0.0, 0.0, np.zeros(0)
        warnings.append("no onsets detected; tempo is not reported")
    downbeat_s = find_downbeats(beat_s, envelope)
    curve = energy_curve(samples)
    db_values = [window["dbfs"] for window in curve]
    return {
        "duration_s": round(duration_s, 3),
        "bpm": round(bpm, 3),
        "beat_period_s": round(period, 4),
        "beats": int(len(beat_s)),
        "downbeats": int(len(downbeat_s)),
        "beat_s": [round(float(value), 4) for value in beat_s],
        "downbeat_s": [round(float(value), 4) for value in downbeat_s],
        "energy": curve,
        "energy_mean_dbfs": round(float(np.mean(db_values)), 3) if db_values else SILENCE_FLOOR_DBFS,
        "energy_peak_dbfs": round(float(np.max(db_values)), 3) if db_values else SILENCE_FLOOR_DBFS,
        "warnings": warnings,
    }


def _distribute(total, parts):
    base = total // parts
    remainder = total % parts
    return [base + (1 if index < remainder else 0) for index in range(parts)]


def _nearest_beat(value, beat_s):
    beats = np.asarray(beat_s, dtype=float)
    if beats.size == 0:
        return value
    return float(beats[int(np.argmin(np.abs(beats - value)))])


def default_slots(target_duration_s):
    """Return the default slot count for a target edit length."""
    return min(
        DEFAULT_SLOTS_MAX,
        max(DEFAULT_SLOTS_MIN, int(round(target_duration_s / DEFAULT_SLOT_SECONDS))),
    )


def build_cut_grid(beat_s, beat_period_s, duration_s, target_duration_s, slots):
    """Return ``(rows, music_shorter, warnings)`` for a beat-aligned cut grid."""
    warnings = []
    if target_duration_s is None:
        return None, None, warnings
    target = float(target_duration_s)
    if target <= 0:
        raise BeatsError("--target-duration-s must be positive")
    count = int(slots) if slots is not None else default_slots(target)
    if count <= 0:
        raise BeatsError("--slots must be positive")
    period = float(beat_period_s)
    if period <= 0 or len(beat_s) == 0:
        warnings.append("no beat grid; a beat-aligned cut grid cannot be built")
        return None, None, warnings
    beat_grid = np.asarray(beat_s, dtype=float)

    music_shorter = duration_s < target
    if music_shorter:
        usable = int(math.floor(duration_s / period))
        limit = usable + 1
        if limit < count:
            warnings.append(f"music holds {usable} beats; using {limit} slots instead of {count}")
            count = limit
        if count <= 1:
            rows = [
                {
                    "index": 0,
                    "start_s": 0.0,
                    "end_s": round(target, 4),
                    "beats": int(round(target / period)),
                }
            ]
            return rows, True, warnings
        counts = _distribute(usable, count - 1)
        boundaries = [0.0]
        cumulative = 0
        for beat_count in counts:
            cumulative += beat_count
            boundaries.append(_nearest_beat(cumulative * period, beat_grid))
        boundaries.append(target)
    else:
        total_beats = int(round(target / period))
        if total_beats < count:
            warnings.append(f"target holds {total_beats} beats; using {total_beats} slots instead of {count}")
            count = total_beats
        if count <= 0:
            warnings.append("cut grid not built: the target is shorter than one beat")
            return None, None, warnings
        counts = _distribute(total_beats, count)
        boundaries = [0.0]
        cumulative = 0
        for index in range(count - 1):
            cumulative += counts[index]
            boundaries.append(_nearest_beat(cumulative * period, beat_grid))
        boundaries.append(target)

    unique = [boundaries[0]]
    for value in boundaries[1:]:
        if value > unique[-1] + 1e-9:
            unique.append(value)
    rows = []
    for index in range(len(unique) - 1):
        start = unique[index]
        end = unique[index + 1]
        rows.append(
            {
                "index": index,
                "start_s": round(start, 4),
                "end_s": round(end, 4),
                "beats": int(round((end - start) / period)),
            }
        )
    return rows, music_shorter, warnings


def _atomic_write_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _outputs_current(input_path, targets):
    if not targets:
        return False
    input_mtime = input_path.stat().st_mtime_ns
    for target in targets:
        if not target.exists() or target.stat().st_mtime_ns < input_mtime:
            return False
    return True


def _summary(status, input_path, out_path, measurement, cut_grid, music_shorter, warnings, started):
    measurement = measurement or {}
    summary = {
        "status": status,
        "out": str(out_path) if out_path is not None else None,
        "input": str(input_path),
        "duration_s": measurement.get("duration_s"),
        "bpm": measurement.get("bpm"),
        "beat_period_s": measurement.get("beat_period_s"),
        "beats": measurement.get("beats"),
        "downbeats": measurement.get("downbeats"),
        "beat_s": measurement.get("beat_s"),
        "downbeat_s": measurement.get("downbeat_s"),
        "energy": measurement.get("energy"),
        "energy_mean_dbfs": measurement.get("energy_mean_dbfs"),
        "energy_peak_dbfs": measurement.get("energy_peak_dbfs"),
        "cut_grid": cut_grid,
        "music_shorter_than_edit": music_shorter,
        "warnings": warnings,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }
    return summary


def run(
    music,
    out_path=None,
    target_duration_s=None,
    slots=None,
    min_beat_s=0.25,
    max_beat_s=2.0,
    force=False,
):
    """Measure a music file and return the summary, writing ``--out`` when given."""
    started = time.monotonic()
    input_path = Path(music).expanduser()
    if not input_path.is_file():
        raise BeatsError(f"input not found: {input_path}")
    out_path = Path(out_path).expanduser() if out_path is not None else None
    if min_beat_s <= 0 or max_beat_s <= 0 or min_beat_s >= max_beat_s:
        raise BeatsError("--min-beat-s and --max-beat-s must be positive with min < max")

    targets = [out_path] if out_path is not None else []
    if not force and _outputs_current(input_path, targets):
        return _summary("skipped", input_path, out_path, None, None, None, [], started)

    if not has_audio_stream(input_path):
        raise BeatsError(f"no audio stream in {input_path}")

    warnings = []
    with tempfile.TemporaryDirectory(prefix="beats_") as temporary_dir:
        samples = decode_mono(input_path, temporary_dir)
        measurement = measure(samples, min_beat_s=min_beat_s, max_beat_s=max_beat_s)

    warnings.extend(measurement.pop("warnings"))
    cut_grid, music_shorter, grid_warnings = build_cut_grid(
        measurement["beat_s"],
        measurement["beat_period_s"],
        measurement["duration_s"],
        target_duration_s,
        slots,
    )
    warnings.extend(grid_warnings)
    if target_duration_s is None and slots is not None:
        warnings.append("--slots ignored without --target-duration-s")

    summary = _summary(
        "ok", input_path, out_path, measurement, cut_grid, music_shorter, warnings, started
    )
    if out_path is not None:
        _atomic_write_json(out_path, summary)
    return summary


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = run(
            args.music,
            out_path=args.out,
            target_duration_s=args.target_duration_s,
            slots=args.slots,
            min_beat_s=args.min_beat_s,
            max_beat_s=args.max_beat_s,
            force=args.force,
        )
    except BeatsRuntimeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except BeatsError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(summary))
    else:
        if summary["status"] == "skipped":
            print(f"skipped {summary['out']} (up to date)")
        else:
            tempo = summary["bpm"] or 0.0
            print(
                f"{summary['input']}: {tempo:.2f} bpm,"
                f" {summary['beats']} beats, {summary['downbeats']} downbeats"
            )
            if summary["out"]:
                print(f"wrote {summary['out']}")
        for warning in summary["warnings"]:
            print(f"warning: {warning}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
