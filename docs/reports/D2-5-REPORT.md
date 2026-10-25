# D2-5 report — cross-platform

Branch `d2-5-cross-platform`, 2026-10-14. Delivery work on top of D2 so the demo
clip can show the tool on a normal PC (Windows, macOS, Linux).

## Works

- **Master merged in** (`merge: master into D2-5`): D2-4 phone flow (`inbox.py`, `serve.py`, `recap.py --notify`) plus the D2-6/D2-7 scope docs landed on the branch; the one conflict in `docs/DECISIONS.md` resolved keeping both sides (the D2-5 lock entry and the D2-4 + demo-scope entries).
- **Portable spend-ledger lock** (D2-5-01): `fcntl.flock` → `filelock.FileLock` on the `<ledger>.lock` sidecar in `scripts/common/llm.py`, atomic `Path.replace` swap. No `fcntl` left in the repo. Verified by `tests/test_llm.py` (73 tests green with the watchdog/hook/paths suites).
- **Portable process check** (D2-5-02): `pgrep` → `psutil.process_iter` first in `scripts/agent_watchdog.py`, `pgrep -x` / `tasklist` kept only as a fallback when `psutil` is missing. Verified by `tests/test_agent_watchdog.py`.
- **Python pre-commit public guard** (D2-5-03): `.githooks/pre-commit` runs `scripts/check_public.py`; it fired on every commit of this phase ("ok (N files)"). Verified by `tests/test_precommit_hook.py`.
- **`pathlib`-only paths** (D2-5-04) in the legacy scripts. Verified by `tests/test_legacy_paths.py`.
- **Encoder per platform** (`scripts/common/ffmpeg_tools.py`): `h264_nvenc` with an NVIDIA GPU, `h264_videotoolbox` on macOS (`-c:v h264_videotoolbox -b:v 8M`), else `libx264`; a missing hardware encoder falls through to `libx264` and an ffmpeg build with none of the three fails loudly (it used to fall back to any video encoder). `proxy.py` now builds its codec args through the shared `video_encode_args` (it had its own copy). Verified by `tests/test_ffmpeg_tools.py` (16 tests: preference order, missing-encoder fallback, mocked per-platform auto-detect, args per encoder) and a smoke run: synthetic 3 s clip → `proxy.py` auto-picked `libx264` on the GPU-less LXC and wrote a 1280x720@25 H.264 proxy (`ffprobe` checked).
- **CI matrix** (`.github/workflows/tests.yml`): `ubuntu-latest` + `macos-latest` + `windows-latest`, `fail-fast: false`, ffmpeg per OS (`apt` + `melt` on Linux, `brew` on macOS, `choco` on Windows) and a loud `ffmpeg -version` check. Ubuntu runs the full suite; macOS/Windows run `-m "not needs_melt and not needs_footage"` (markers registered in `pytest.ini`, applied to the melt end-to-end test in `tests/test_edit_to_fragments.py`; no test in the suite reads real footage, so `needs_footage` is registered and used in the expression but nothing carries it today). The workflow pip line gained `filelock psutil` and still avoids `requirements.txt` (no torch/whisperx).
- **Full suite on the branch**: `742 passed` (LXC, ubuntu, CPU-only). With the macOS/Windows expression: `741 passed, 1 deselected` — exactly the melt test.
- Install notes per OS in the quickstart (`README.md`) and the encoder story synced into `yt-shorts`, README and `docs/RULES.md`.

## Mocked / not verified

- **The GitHub Actions matrix itself** — no macOS or Windows runner here. `brew install ffmpeg`, `choco install ffmpeg -y` and Windows PATH visibility of the choco ffmpeg are unverified until the PR's CI runs.
- **`h264_videotoolbox` encode** — no macOS here. The flags come from FFmpeg n6.1.1 `libavcodec/videotoolboxenc.c`: `-b:v` works on every macOS, `-q:v` constant quality only on Apple Silicon (`vtenc_qscale_enabled()` = ARM64), which is why the basic flags use a bitrate.
- **`h264_nvenc` encode** — no GPU on the build machine (RULES), only the selection logic is tested.
- **Windows symlink farms** in `inbox.py` (phone flow) — untouched; a Windows machine without symlink privilege will fail that path. Noted for the demo machine, out of D2-5 scope.

## the owner, check this

- The PR's three CI jobs (macOS/Windows were never run before): `gh pr checks` on the D2-5 PR, or the Checks tab of the PR.
- `.venv/bin/python -m pytest tests/ -q` → `742 passed` on this machine.
- Encoder pick on any machine: `cd scripts && ../.venv/bin/python -c "from common.ffmpeg_tools import preferred_video_encoder; print(preferred_video_encoder())"` (expect `libx264` on the LXC, `h264_videotoolbox` on a Mac).
- Per-OS install notes in the quickstart: `README.md` (top) — are they the level of detail you want in the demo?
- One real-footage smoke on a demo PC before the clip: `python scripts/proxy.py <raw folder>` → proxies in `<parent>/proxy`, originals untouched.
