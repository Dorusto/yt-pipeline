# SPDX-License-Identifier: AGPL-3.0-or-later
"""The timeline math shared by the web app and the thumbnail station.

``edit_summary`` turns an ``edit.json`` document into the proportional blocks
of the edit and its totals, so ``serve.py`` (the strip) and ``thumbs.py`` (the
frame of each block) agree on where every slot starts and ends.
"""

from common.moves import slot_screen_length
from common.timeline_words import fade_for_join


def edit_summary(document):
    """The edit's timeline blocks and totals from an edit.json document, or None.

    Returns {"blocks": [{"start", "end", "kind", "label"}], "total", "shots", "photos"}
    or None when the document has no usable video list.
    """
    slots = document.get("video") if isinstance(document, dict) else None
    if not isinstance(slots, list):
        return None
    blocks = []
    start = 0.0
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        try:
            if slot.get("kind") == "photo" or slot.get("photo"):
                length = float(slot.get("duration_s") or 0.0)
                kind = "photo"
            else:
                length = slot_screen_length(
                    {**slot, "in_s": float(slot.get("in_s", 0.0)), "out_s": float(slot.get("out_s", 0.0))}
                )
                kind = "clip"
        except (TypeError, ValueError):
            continue
        if length <= 0:
            continue
        label = slot.get("shot_key") or slot.get("photo") or slot.get("clip") or slot.get("id") or ""
        if blocks:
            start -= fade_for_join(slot, blocks[-1]["end"] - blocks[-1]["start"], length)
            blocks[-1]["end"] = start
        blocks.append({"start": start, "end": start + length, "kind": kind, "label": str(label)})
        start += length
    if not blocks:
        return None
    photos = sum(1 for block in blocks if block["kind"] == "photo")
    return {"blocks": blocks, "total": start, "shots": len(blocks), "photos": photos}
