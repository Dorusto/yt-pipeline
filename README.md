# shorts-generator

Generează automat Shorts / Reels 9:16 din videouri YouTube 1920×1080.

**Ce face:**
- Detectează automat poziția feței și calculează crop-ul 608×1080
- Rulează Whisper cu word timestamps pe segmentul audio
- Generează subtitrare ASS cu karaoke highlighting (cuvântul curent = galben)
- Randează totul cu ffmpeg + h264_nvenc (GPU)

---

## Instalare

**Cerințe:** Python 3.10+, `uv`, `ffmpeg` cu nvenc, `whisper` (openai-whisper via pipx)

```bash
git clone https://github.com/YOUR_USERNAME/shorts-generator.git
cd shorts-generator
uv venv
uv pip install -r requirements.txt
```

---

## Utilizare

### 1. Configurează segmentele

Copiază `shorts_config_example.yaml` → `shorts_config.yaml` și completează:

```yaml
video: "NumeVideo.mp4"
audio: "NumeVideo.mp3"

segments:
  - name: "Hook"
    start: "00:00:00"
    end: "00:00:54"

  - name: "Reframing"
    start: "00:01:02"
    end: "00:01:52"
```

### 2. Rulează

**Mod interactiv** (te întreabă calea video):
```bash
.venv/bin/python shorts_generator.py
```

**Mod direct** (pentru automatizare):
```bash
.venv/bin/python shorts_generator.py \
  --video /cale/catre/video.mp4 \
  --audio /cale/catre/audio.mp3
```

**Rerandez fără Whisper** (după corecții manuale în `_words.json`):
```bash
.venv/bin/python shorts_generator.py \
  --video /cale/catre/video.mp4 \
  --skip-whisper
```

### 3. Output

Short-urile apar în `shorts/` lângă fișierul video:
```
shorts/Short1-Hook.mp4
shorts/Short2-Reframing.mp4

auto/Hook_words.json       # word timestamps (editabil)
auto/Hook_karaoke.ass      # subtitrare generată
```

---

## Structura fișierelor video

Scriptul se așteaptă la această structură (dar se adaptează automat):

```
Export/
  video/    NumeVideo.mp4
  audio/    NumeVideo.mp3
  subtitles/
  shorts/   ← output generat
  auto/     ← JSON + ASS intermediare
```

---

## Stack tehnic

| Componentă | Tool |
|:---|:---|
| Taiere + crop + burn subtitles | `ffmpeg` cu `h264_nvenc` |
| Word-level timestamps | `whisper turbo --word_timestamps True` |
| Face detection pentru crop | `opencv-python` (Haar cascades) |
| Manipulare ASS | `pysubs2` |

---

## Corecții manuale

Whisper poate greși ocazional. Corectează direct în `auto/NumeVideo_words.json`, apoi rerandez cu `--skip-whisper`.

> **Roadmap:** înlocuire Whisper cu forced alignment pe transcript corectat (WhisperX).
