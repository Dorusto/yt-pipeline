---
title: yt-pipeline — Editing craft research
date: 2026-10-25
tags: [yt-pipeline, research, editing]
---

# yt-pipeline — Editing craft research

Short notes on editing craft, one section per source, then the rules we adopt.
The Murch priorities are the backbone: **emotion 51, story 23, rhythm 10, eye
trace 7, 2D plane 5, 3D space 4**.

## 1. No Film School — 6 rules for good cutting (Walter Murch)

Murch's "Rule of Six": an ideal cut satisfies emotion, story, rhythm, eye trace,
2D plane of screen and 3D space at once. They are a priority list, not equals:
when you must give something up, sacrifice from the bottom and never give up
emotion before story, story before rhythm, rhythm before eye trace, eye trace
before planarity, planarity before spatial continuity. Emotion alone is worth
more than the other five combined, because a right emotion masks the other
deficiencies. For us: the director ranks shots by what they make the viewer feel
first, and treats the geometric rules as the first thing to drop.

## 2. UT Austin RTF 318 — Murch's Rule of Six

The same six criteria with the weights spelled out: emotion 51 %, story 23 %,
rhythm 10 %, eye trace 7 %, 2D plane of screen 5 %, 3D space 4 %. Each item
helps hide problems in the items below it, but not the other way round. For us:
the numbers are the tie-breaker when two candidate shots are close — the one
that carries more emotion or story wins over the one that is merely cleaner
geometrically.

## 3. Frame.io — Stages of the editing process

The edit moves through assembly, rough cut, intermediate cuts and picture lock.
The assembly is long and close to the script; the rough cut is where the story
takes shape and is shown for feedback; picture lock freezes the cut so colour,
sound and finishing can start. For us: the director's plan is the assembly, the
editor's `edit.json` is the rough cut, and the owner's review is the feedback
before lock — a change after lock means a new version, never an edit of the old
one.

## 4. Wikipedia — Rough cut

The rough cut is the second of three offline-editing stages and the first in
which the film resembles the final product: shots are in approximate order with
approximate trimming, sound and colour are unfinished, and it is usually much
longer than the final running time. It is followed by refinement up to picture
lock. For us: the first cut may be rough and long; the director's review rounds
are the refinement, and only the approved cut is rendered.

## 5. Wikipedia — Jump cut

A jump cut breaks one continuous shot into two with a piece of footage removed,
jumping forward in time; it violates classical continuity and draws attention to
the construction. The 30-degree rule exists to avoid the accidental version: two
consecutive shots of the same subject need at least 30 degrees of camera change
(or a framing change) to read as a new point of view rather than a jump. For us:
two near-identical shots of one take never sit back to back unless the owner
intends it, and a cut inside one take must land on a real change.

## 6. University of Utah CS 5964 — Editing lecture (PDF)

Covers the two philosophies (continuity editing vs montage editing), the types
of cut (match on action, jump cut, cross/parallel cut, graphic match, magic
edits) and the editing rules: the 180° rule, change shot size/angle (30° rule),
shot/reverse shot, and clean entrances/exits — let the subject fully enter or
leave the frame. Pacing: more action, more shots; less action, fewer shots; know
when to hold. For us: "clean entry and exit" and "cut on action" become explicit
techniques in the director's playbook, and pacing follows the action, not a
fixed table.

## 7. Netspar — Exit-entrance cuts and visual attention (eye tracking)

An eye-tracking study of the exit-entrance cut: when a subject leaves the frame
in one direction, the viewer's attention is primed to expect the next shot to
continue that direction. Icons placed congruently with the exit direction were
looked at longer and recognised more often than incongruent ones, though the
effect was weaker than hypothesised. For us: when a shot ends with a subject
leaving frame, the next shot should continue the direction of motion; the
director can use this as a technique, not a hard rule.

## 8. Wikipedia — Kuleshov effect

Viewers derive more meaning from two sequential shots than from either alone:
the same neutral face reads as hunger, grief or desire depending on what it is
cut against. The effect is the basis of montage — meaning comes from the
juxtaposition, not the individual image. For us: the order of shots is itself a
storytelling tool; the director's structure beats exist to control what a shot
means by what sits next to it.

## 9. Reduct — Paper edits for documentary filmmakers

A paper edit lays out the story from transcripts before touching the timeline:
review the material, pull the quotes, arrange them in order, each with its
source and timecode. It makes iteration cheap and lets others review and comment
before the visual edit. The classic flaw is that what works on paper may not
work as video — the delivery, the face or a defect can kill a quote. For us: the
director's plan is a paper edit — idea, beats and shots in words — and the
editor is the step that checks it against the actual footage.

## 10. Frame.io — Stringouts

A stringout is a large selection of footage laid out in order to see all the
options at once: dailies stringouts, dialogue stringouts (every angle on one
line), documentary stringouts driven by field notes, VFX stringouts and
alternates. It front-loads effort so the creative session can compare takes
quickly. For us: `shotlog.json` is our stringout — every candidate in order with
its score and reason — and the director picks from it instead of re-watching.

## 11. Splice — Vacation recap montage for travel vloggers

A practical recap recipe: define the purpose and audience, plan a simple
three-scene storyboard (hook/arrival, core activity, reflection/outro), organise
and de-duplicate footage, open with a short sequence of strong shots, alternate
slow and fast sequences to keep the pace, sync music to the cuts, and use
effects sparingly. For us: the three-beat structure is a good default for a
vacation recap, and the hook-first opening matches the director's structure
beats.

## What we take

- **Emotion first.** Rank shots by what they make the viewer feel; drop geometry
  before emotion, story, rhythm or eye trace (Murch: 51/23/10/7/5/4).
- **Story before rhythm.** A shot that advances the story beats a prettier shot
  that does not.
- **No accidental jump cuts.** Two near-identical shots of one take never sit
  back to back unless the owner intends it; a cut inside one take lands on a
  real change (30-degree rule).
- **Cut on motion.** Prefer a cut during an action or on the beat; a match on
  action hides the edit.
- **Clean entry and exit.** Let a subject fully enter or leave the frame before
  cutting, and continue the direction of motion into the next shot.
- **Order is meaning.** What a shot means depends on what it is cut against
  (Kuleshov); the structure beats control that.
- **Plan in words first.** The director writes a paper edit (idea, beats, shots)
  and the editor checks it against the footage before any timecode is fixed.
- **The shotlog is the stringout.** Every candidate in order with score and
  reason; the director picks from it.
- **Pace follows the action.** More action, more shots; less action, fewer
  shots; know when to hold.
- **A recap has a shape.** Hook/arrival, core activity, reflection/outro, with a
  strong opening sequence.
