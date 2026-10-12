---
title: AI editors — feature inventory and open-source equivalents
date: 2026-10-12
tags: [yt-pipeline, research, backlog]
---

# AI editors — what they do, and how we get the same with open source

Second pass on the AI tools yt-pipeline competes with, focused on **raw-footage** features (the first pass, `prior-art.md`, covered highlight clipping). For each feature: who has it, the open-source route, the phase it belongs to, and whether it is in scope.

## 1. Tools reviewed

- **Eddie AI** (assistant editor, NAB 2026): logs footage into a summary, topic list and B-roll log; builds a rough cut beat by beat from a story framework it proposes or the editor gives; places B-roll over the A-roll automatically, real footage only; prompt-driven string-outs across interviews; exports back into Premiere, Resolve, Final Cut.
- **CutBack Selects** (pre-editing for pros): transcription with speaker detection, removes dead air and fillers, labels clips and flags unnecessary ones, splits footage into topic- or take-based chunks, syncs multicam and audio without markers, hands off organised sequences with colour coding and markers.
- **Opus Clip ClipAnything**: clips any moment from any video using visual, audio and sentiment cues, works on low-talk footage; natural-language prompts ("find all the touchdowns"); per-clip heart / thumbs-down feedback.
- **CapCut Auto Cut**: cuts on music beats (sensitivity from every beat to strong beats only), on speech pauses, or from a script; transition style chosen before the run; clip duration range setting; reorder / replace afterwards.
- **Insta360 AI Director (X6)**: edits on the device while it charges — highlight recognition plus frame-composition analysis → a daily vlog by morning; skips clips under a minute and log-profile footage.
- **DJI LightCut / Mimo, GoPro Quik**: scene recognition, smooth-move and smile preference, templates, beat sync (covered in `shot-grammar.md`).
- **Premiere Pro / DaVinci Resolve 21**: natural-language search for objects, people, locations, camera angles; no camera-motion analysis (see `shot-grammar.md`, section 3).

## 2. Feature inventory

| # | Feature | Who has it | Open-source route | Phase | In scope |
|---|---|---|---|---|---|
| 1 | Natural-language search in footage ("boat passing the walls at sunset") | Opus ClipAnything, Premiere, Resolve | `describe` text + SigLIP/CLIP embeddings; Lighthouse or TimeLens2 for moment boundaries | D1b index, D5 UI | yes |
| 2 | Project log: summary, topics, B-roll catalogue | Eddie | LLM over `shotlog.json` + transcripts | D1b | yes |
| 3 | Story framework → rough cut beat by beat | Eddie | director screenwriter step | D2 | yes |
| 4 | Automatic B-roll over talking-head | Eddie, Descript | match transcript topics to shot descriptions by embeddings | D4 | yes |
| 5 | Dead air, fillers, bad takes, retakes | Selects, Descript | existing auto-editor + filler flow; retakes by transcript similarity | D4 | yes |
| 6 | Speaker detection | Selects | pyannote diarization (already used by ClipsAI) | D4 | yes |
| 7 | Topic / take chunking | Selects | LLM over transcript with timestamps | D4 | yes |
| 8 | Multicam + audio sync without markers | Selects | audio cross-correlation (e.g. `audio-offset-finder`, or numpy/scipy) | D5 | yes — A7 III + Osmo + phone on the same scene |
| 9 | Beat sync with sensitivity (every beat / strong beats) | CapCut, Quik, Mimo | librosa or Beat This! for beats and downbeats; director picks the level | D2 | yes |
| 10 | Cut on speech pauses | CapCut | word timestamps (existing) | D4 | yes |
| 11 | Clip duration range, transition style chosen up front | CapCut | brief / config fields for the director; ffmpeg `xfade` transitions for rendered previews, in-camera transitions from `shot-grammar.md` | D2 | yes |
| 12 | Overnight edit ("while it charges") | Insta360 AI Director, Eddie, Quik | scheduled job on the homelab: watch a folder → analyze overnight → draft + notification by morning | D5 | yes — fits the homelab |
| 13 | Daily vlog per day | Insta360 | group by creation date (existing `daily_summary_srt.py` logic) + director | D2 | yes |
| 14 | Scene recognition (outdoor, water, city, food…) | LightCut | `describe` | D1b | yes |
| 15 | Faces, smiles, people preference | LightCut | OpenCV / InsightFace detection + `describe` | D5 | yes |
| 16 | Auto reframe 16:9 → 9:16 following the subject | Opus, Insta360 Auto Frame | ClipsAI resize, or YOLO + ByteTrack | D5 | yes |
| 17 | Animated captions | Opus, CapCut | existing `pysubs2` karaoke | done | yes |
| 18 | Ranking + feedback loop (heart / thumbs-down) | Opus | store approve / reject per segment, adjust score weights per user | D3 + D5 | yes — our feedback trains our own scorer |
| 19 | Export with markers, colours, nested sequences to Premiere / Resolve / FCP | Selects, Eddie | Kdenlive now (existing); OpenTimelineIO for FCPXML / Resolve later | P2 | later |
| 20 | Music library and templates | Quik, CapCut, Mimo | Doru's own music folder; later his local AI music project | D2 | partly |
| 21 | Virality model trained on millions of clips | Opus | not reproducible; replaced by our score + YouTube "Most replayed" on his own channel | — | substitute |
| 22 | Script → generated video, generated B-roll, voice cloning | CapCut, Descript | generative; out of scope by decision | — | no |

## 3. Answer to "can we have everything they have?"

Almost everything that works on **your own footage** has a mature open-source route and fits the tool-per-stage design. What we deliberately do not copy: generative video and voice (out of scope), a licensed music catalogue (own or AI-generated music instead), and a virality model trained on other people's data (replaced by our own feedback and audience signals).

What none of them do, and we will: classify camera moves, split continuous takes into usable moves with reasons, and pair in-camera transitions (`shot-grammar.md`).

## Sources

- Eddie AI at NAB 2026 — https://www.redsharknews.com/eddie-ai-nab-2026-ai-video-editing-rough-cut
- CutBack Selects — https://cutback.video/blog/cutback-selects-ai-rough-cut-tool-for-video-editors-using-davinci-premiere-pro-final-cut
- Opus ClipAnything — https://help.opus.pro/docs/article/how-to-use-clipanything.md
- CapCut Auto Cut — https://www.capcut.com/help/auto-cut-in-capcut
- Insta360 AI Director — https://onlinemanual.insta360.com/x6/en-us/operating-tutorials/highlight-features/ai-director
