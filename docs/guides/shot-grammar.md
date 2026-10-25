---
title: Shot grammar — what yt-pipeline knows about filming and editing
date: 2026-10-12
tags: [yt-pipeline, guide, filming, editing]
---

# Shot grammar

Industry-standard filming and editing techniques, written once for two readers:

- **The person filming** — how to shoot so the edit looks professional (section 4).
- **The software** — what each tool must detect and how the director may use it (sections 1–3).

Every technique has three columns: how it is filmed, how `motion` / `quality` / `describe` detect it, how the director uses it. When a technique is added here, the tools that should know it get a task.

## 1. Core principle: label, never decide alone

A segment is never thrown away by the analysis tools. They **label** it; the director (and Doru in the review UI) decides.

- **Intent and defect are separate fields.** Motion blur during a whip pan is intent; motion blur on a static shot is a defect. A fast rotation at the end of a take may be the first half of a transition, not junk. A fast move between short holds on people is a filming mistake (marked `whip: "short_hold"`, scores low), while a fast move between two long holds is a deliberate move (marked `whip: "deliberate"`) and its motion blur is not a defect; a deliberate move (orbit, pan, tilt, dolly, travel, truck) is not punished for shake the way a static hold is.
- Each segment gets a **role** with a confidence: `move`, `hold`, `transition_out`, `transition_in`, `setup`, `teardown`, `accidental`, plus a list of **defects** (`blur`, `shake`, `exposure`, `obstruction`, `horizon`), each with a measured value.
- **One file can hold several intentional shots.** Segmentation follows motion phases and content changes, not file boundaries.
- `setup` / `teardown` are *likely* roles (short, irregular motion right after record starts or right before it stops), not a verdict. If that motion is a clean fast rotation, it is a `transition_in` / `transition_out` candidate instead.

## 2. Catalog

### 2.1 Camera moves (vocabulary from CameraBench)

| Move | Filmed as | Detected by | Director uses it for |
|---|---|---|---|
| Static / locked-off | camera still, scene moves | `motion`: near-zero global flow | establishing, breathing room, text over picture |
| Pan left/right | rotate horizontally | `motion`: dominant horizontal rotation | revealing a place, following action |
| Tilt up/down | rotate vertically | `motion`: dominant vertical rotation | revealing height, sky/ground transitions |
| Roll CW/CCW | rotate around the lens axis | `motion`: transform rotation | energy, roll transitions |
| Dolly / push in, pull out | move toward / away | `motion`: scale change; vs zoom → `describe` (parallax present = dolly) | emphasis (in), context or ending (out) |
| Truck / pedestal | move sideways / up-down | `motion`: translation with parallax | parallax shots, foreground reveals |
| Zoom in/out | lens zoom, no movement | `motion`: scale change without parallax | rare on action cams |
| Orbit / arc | circle around a subject | `describe` (semantic) + `motion`: pan opposite to translation | hero shot of a subject (the boat, a person) |
| Tracking (follow / lead / side) | move with a moving subject | `describe` + `motion`: subject stable while background flows | action, travel progress |
| Reveal | move past a foreground object to uncover the scene | `describe` + `quality`: obstruction clears | openers, scene changes |
| Parallax | foreground and background move at different speeds | `motion`: flow variance by depth | depth, cinematic feel |
| POV / FPV | camera is the rider's eyes | `describe` (helmet, handlebars, bow of the boat) | immersion |
| Timelapse / hyperlapse | metadata or very fast scene change | `ingest` (frame-rate / mode metadata) + `motion` | passage of time |
| Slow motion source | high frame rate (50–120 fps) | `ingest` | speed ramps, emphasis |

Each move also carries **speed** (slow / regular / fast) and **steadiness** (static … very unsteady).

### 2.2 In-camera transitions (filmed in pairs)

| Transition | End of clip A | Start of clip B | Detected by |
|---|---|---|---|
| Whip pan | fast pan, motion blur | fast pan in the same direction | `motion`: velocity spike at take tail / head, same sign |
| Roll / spin | fast roll | fast roll, same direction | `motion`: rotation spike |
| Mask / object wipe | something covers the lens (hand, wall, person) | starts covered, uncovers | `quality`: obstruction ramp at tail / head |
| Push-in punch | fast push toward a dark or uniform surface | pull out from one | `motion`: scale spike + `quality`: low-detail frames |
| Sky / ground tilt | tilt up into sky (or down to ground) | tilt down from sky | `motion`: tilt + `quality`: uniform frames |
| Match cut | ends on a shape, colour or motion | starts on a similar one | `describe` + embeddings similarity (later) |
| Jump in/out of frame | subject leaves frame | subject enters frame | `describe` |

The director pairs `transition_out` with `transition_in` of matching type and direction and cuts at the peak of the blur.

### 2.3 Sequence and continuity rules

- **Coverage sequence:** wide (where) → medium (who / what) → close (detail) → reaction. A good place has at least one of each.
- **Screen direction:** keep movement direction consistent across cuts (boat moving right keeps moving right), unless the change is the point.
- **Cut on motion:** cut inside the steady phase of a move; a pan right into a pan right reads as one movement.
- **Handles:** never start or end a used segment inside an acceleration ramp; keep a few frames of steady motion on each side.
- **Shot length follows pacing:** relaxed 4–8 s or longer, energetic 1–3 s; with music, lengths in whole beats or bars.
- **Audio:** ambient sound is kept under picture; J/L cuts (sound of the next shot starts early) smooth scene changes.

### 2.4 Defects (always measured, never assumed)

blur (Laplacian variance vs the clip's median), shake (trajectory residual), exposure (clipped highs / crushed lows), obstruction (water drops, finger, pocket), horizon tilt (roll offset on a static shot), face_cut (a face box touching the frame edge), accidental recording (pocket, ground, sky for minutes).

## 3. What professional software does today (October 2026)

| Software | What it detects | Camera move type | Usable-take trimming | Transition pairing |
|---|---|---|---|---|
| Premiere Pro (Media Intelligence) | objects, locations, camera angles; natural-language search | no — requested by users as a feature | no, manual | no |
| DaVinci Resolve 21 | IntelliSearch for people/objects, SlateFinder, Motion Deblur, UltraSharpen | no | no | no |
| DJI LightCut (maintenance ended Sep 2026) | scene type, smiles, people; prefers smooth moves | implicit only | partial, opaque | templates |
| DJI Mimo / GoPro Quik | highlights, beat sync, templates | no | opaque | templates |
| CameraBench models (research) | full camera-motion vocabulary | yes | no | no |

Professional editors search footage by *what* is in it; none classify *how* the camera moved, split a take into usable moves with reasons, or pair in-camera transitions. That combination is yt-pipeline's differentiator.

## 4. Filming for yt-pipeline — checklist

1. **Hold 2 seconds still** before and after every move. These become handles and make the move easy to find.
2. **One intention per move.** Pan *or* tilt, not both wandering; if you combine, do it on purpose and steadily.
3. **Film transitions in pairs**: the end of one shot and the start of the next with the same motion and direction.
4. **Shoot coverage**: for every place, a wide, a medium and a detail; 10–20 s each.
5. **Keep the originals from the SD card**, not only Mimo exports. With RockSteady off and FOV Wide the Osmo Action 4 records gyro data, which gives `motion` the exact camera movement and lets Gyroflow stabilise later. With RockSteady on, keep the originals anyway (full quality, metadata).
6. **Record ambient sound**; wind noise is detected and flagged, but clean ambience is gold for J/L cuts.
7. **Say it on camera** when something matters ("this is the hero shot") — `describe` and the transcript will find it.

## Sources

- CameraBench — https://arxiv.org/abs/2504.15376
- In-camera transitions — https://spliceapp.com/blog/mastering-trip-recap-transitions-for-short-form-videos/, https://nofilmschool.com/how-to-do-a-whip-pan, https://wolfcrow.com/the-3-important-in-camera-transitions-in-filmmaking/
- Premiere Pro Media Intelligence — https://nofilmschool.com/adobe-media-intelligence
- DaVinci Resolve 21 — https://www.newsshooter.com/2026/06/02/davinci-resolve-21-final-release/
- DJI LightCut — https://www.dji.com/lightcut
- Gyroflow with DJI cameras — https://gurubase.io/g/gyroflow/using-dji-cameras-with-gyroflow-for-stabilization
