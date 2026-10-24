# Licences

Every third-party dependency of yt-pipeline, with its licence and how it is used.
The credits and the required notices are in [`NOTICE.md`](../NOTICE.md).

## Python packages

| Name | Licence | How it is used |
|:---|:---|:---|
| opencv-python-headless | Apache-2.0 | linked library — optical flow, frame sampling, `cv2.dnn` inference, YuNet face detection |
| openai | Apache-2.0 | linked library — OpenRouter/OpenAI-compatible client (`common/llm.py`, `common/openrouter.py`) |
| openai-whisper | MIT | linked library — transcription (`transcribe.py`) |
| pillow | MIT-CMU (HPND) | linked library — image handling (photos, thumbnails) |
| pillow-heif | BSD-3-Clause | linked library — HEIC photo reading |
| psd-tools | MIT | linked library — thumbnail PSD export |
| pysubs2 | MIT | linked library — SRT/ASS subtitle handling |
| pyyaml | MIT | linked library — YAML config and fragments |
| jsonschema | MIT | linked library — schema validation |
| filelock | MIT | linked library — costs ledger lock |
| mcp | MIT | linked library — MCP server (`yt_mcp.py`) |
| psutil | BSD-3-Clause | linked library — process checks (watchdog, `serve.py`) |
| rembg | MIT | linked library — thumbnail cut-out (`birefnet-portrait`) |
| torch | BSD-3-Clause | linked library — WhisperX, RVM, matte |
| torchaudio | BSD-2-Clause | linked library — WhisperX audio |
| torchvision | BSD-3-Clause | linked library — WhisperX / model helpers |
| whisperx | BSD-2-Clause | linked library — forced alignment (word timestamps) |
| pytest | MIT | dev dependency — test runner |
| pytest-timeout | MIT | dev dependency — per-test timeout |

## External programs

| Name | Licence | How it is used |
|:---|:---|:---|
| ffmpeg / ffprobe | LGPL-2.1-or-later or GPL-2.0-or-later (build-dependent) | called program — decode, encode, filters, loudness, noise reduction |
| melt | MLT, LGPL-2.1-or-later | called program — optional Kdenlive project validation |
| Kdenlive | GPL-3.0-or-later | optional external editor, the `.kdenlive` export target |
| filmcraft-cli | FilmCraft, MIT OR Apache-2.0 | called program — colour and loudness recipes, PiP finishing |
| node / npx | HyperFrames CLI, Apache-2.0 | called program — graphics engine |
| git | GPL-2.0-only | called program — `check_public.py`, `common/git.py` |
| Gyroflow | GPL-3.0-or-later | not called by the code, named in the guides as the stabiliser for DJI gyro footage |
| vidstab | GPL-2.0-or-later | ffmpeg filter, part of a GPL ffmpeg build |

## Models and weights

| Name | Licence | How it is used |
|:---|:---|:---|
| Whisper model weights | MIT | downloaded at first run, never committed — transcription |
| YuNet face detection ONNX (OpenCV Zoo) | Apache-2.0 | downloaded at first run, never committed — face detection for reframing |
| MobileNetV2 ONNX (ONNX model zoo) | Apache-2.0 | downloaded at first run, never committed — image embeddings for similarity |
| RNNoise model `arnndn.rnnn` | BSD-3-Clause | downloaded at first run, never committed — audio noise reduction |
| BiRefNet portrait model (rembg) | MIT | downloaded at first run, never committed — thumbnail cut-out |
| RobustVideoMatting (RVM) | GPL-3.0 | fetched through `torch.hub` at first run, never vendored — background removal |
| MatAnyone2 | NTU S-Lab 1.0, non-commercial | optional personal backend only, never shipped, never the default |
| YuE2 / ACE-Step 1.5 / Stable Audio Open | see the notes below | planned music models (D6, not used yet) |

## Fonts and assets

| Name | Licence | How it is used |
|:---|:---|:---|
| Montserrat Black | SIL OFL 1.1 | bundled font, `assets/fonts/Montserrat-Black.ttf` |
| JetBrains Mono | SIL OFL 1.1 | bundled font, `assets/fonts/JetBrainsMono-Regular.ttf` |
| app logo SVGs | AGPL-3.0-or-later | own work, `assets/brand/option-*.svg` |
| graphics templates | AGPL-3.0-or-later | own work, `assets/graphics/*.html` |

## Check before commercial use

- **RobustVideoMatting (GPL-3.0):** fetched at first run through `torch.hub`,
  never vendored; the videos it produces are not GPL, but redistributing the RVM
  code with a closed-source product would carry GPL-3.0 obligations.
  AGPL-3.0-or-later is compatible with GPL-3.0.
- **MatAnyone2 (NTU S-Lab 1.0, non-commercial):** never the default, never used
  for paid work without written permission from its authors, not shipped in the
  repo.
- **ffmpeg:** a build with GPL components (`libx264`, `vidstab`) is GPL; calling
  it as a separate program is fine, but shipping a GPL ffmpeg build inside a
  proprietary product needs care. AGPL-3.0 is compatible with GPL-3.0.
- **Kdenlive (GPL-3.0) and Gyroflow (GPL-3.0):** external programs, not shipped;
  the user installs them.
- **Stable Audio Open:** free only under $1M yearly revenue (planned, D6).
- **YuE2:** outputs are publishable/monetisable per the vault check, but re-check
  the model licence before shipping.
- **Fonts (SIL OFL 1.1):** fine to bundle; keep the OFL notice files.
