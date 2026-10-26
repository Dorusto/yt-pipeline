# Licences

Every third-party dependency of yt-pipeline, with its licence and how it is used.
The credits and the required notices are in [`NOTICE.md`](../NOTICE.md).

## Python packages

| Name | Licence | How it is used |
|:---|:---|:---|
| opencv-python-headless | Apache-2.0 | linked library — optical flow, frame sampling, `cv2.dnn` inference, YuNet face detection |
| numpy | BSD-3-Clause | linked library — numeric arrays (motion, quality, beats, similarity, colour, photos) |
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
| beat-this | MIT | optional linked library — Beat This! downbeat engine (`beats.py --engine beat_this`), not in `requirements.txt` |
| pytest | MIT | dev dependency — test runner |
| pytest-timeout | MIT | dev dependency — per-test timeout |

## External programs

| Name | Licence | How it is used | Source |
|:---|:---|:---|:---|
| ffmpeg / ffprobe | LGPL-2.1-or-later or GPL-2.0-or-later (build-dependent) | called program — decode, encode, filters, loudness, noise reduction | https://ffmpeg.org/ |
| melt | MLT, LGPL-2.1-or-later | called program — optional Kdenlive project validation | https://github.com/mltframework/mlt |
| Kdenlive | GPL-3.0-or-later | optional external editor, the `.kdenlive` export target | https://kdenlive.org/ |
| filmcraft-cli | FilmCraft, MIT OR Apache-2.0 | called program — colour and loudness recipes, PiP finishing | https://github.com/storytold/filmcraft |
| node / npx | HyperFrames CLI, Apache-2.0 | called program — graphics engine | https://github.com/heygen-com/hyperframes |
| git | GPL-2.0-only | called program — `check_public.py`, `common/git.py` | https://git-scm.com/ |
| Gyroflow | GPL-3.0-or-later | not called by the code, named in the guides as the stabiliser for DJI gyro footage | https://github.com/gyroflow/gyroflow |
| vidstab | GPL-2.0-or-later | ffmpeg filter, part of a GPL ffmpeg build | https://github.com/georgmartius/vid.stab |

## Models and weights

| Name | Licence | How it is used | Source |
|:---|:---|:---|:---|
| Whisper model weights | MIT | downloaded at first run, never committed — transcription | https://github.com/openai/whisper |
| YuNet face detection ONNX (OpenCV Zoo) | MIT | downloaded at first run, never committed — face detection for reframing | https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/LICENSE |
| MobileNetV2 ONNX (ONNX model zoo) | Apache-2.0 | downloaded at first run, never committed — image embeddings for similarity | https://github.com/onnx/models/blob/main/validated/vision/classification/mobilenet/README.md |
| RNNoise model `arnndn.rnnn` | not subject to copyright | downloaded at first run, never committed — audio noise reduction | https://github.com/GregorR/rnnoise-models |
| BiRefNet portrait model (rembg) | MIT | downloaded at first run, never committed — thumbnail cut-out | https://github.com/ZhengPeng7/BiRefNet |
| RobustVideoMatting (RVM) | GPL-3.0 | fetched through `torch.hub` at first run, never vendored — background removal | https://github.com/PeterL1n/RobustVideoMatting |
| Beat This! model weights (`final0`) | MIT | downloaded at first run, never committed — optional downbeat engine (`beats.py --engine beat_this`) | https://github.com/CPJKU/beat_this |
| MatAnyone2 | NTU S-Lab 1.0, non-commercial | optional personal backend only, never shipped, never the default | https://github.com/pq-yang/MatAnyone |
| YuE2 / ACE-Step 1.5 / Stable Audio Open | see the notes below | planned music models (D6, not used yet) | https://github.com/multimodal-art-projection/YuE https://github.com/ace-step/ACE-Step https://huggingface.co/stabilityai/stable-audio-open-1.0 |

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
- **Beat This! (MIT):** the code and the published weights are MIT, but the
  upstream README warns that some *training* files are copyrighted or under
  limited CC licences; it is the user's responsibility to assess their use case.
  The package is optional and not in `requirements.txt`.
- **ffmpeg:** a build with GPL components (`libx264`, `vidstab`) is GPL; calling
  it as a separate program is fine, but shipping a GPL ffmpeg build inside a
  proprietary product needs care. AGPL-3.0 is compatible with GPL-3.0.
- **Kdenlive (GPL-3.0) and Gyroflow (GPL-3.0):** external programs, not shipped;
  the user installs them.
- **Stable Audio Open:** free only under $1M yearly revenue (planned, D6).
- **YuE2:** outputs are publishable/monetisable per the vault check, but re-check
  the model licence before shipping.
- **Fonts (SIL OFL 1.1):** fine to bundle; keep the OFL notice files.
