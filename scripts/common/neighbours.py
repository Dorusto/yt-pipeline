"""Two neighbouring slots that are beats of one segment or a similar pair are a pointless jump cut.

The director keeps only the better one.
"""


def segment_id_of(slot):
    """The slot's segment id: the ``segment`` dict's ``id`` or the ``segment_id`` key."""
    segment = slot.get("segment")
    if isinstance(segment, dict):
        return segment.get("id")
    return slot.get("segment_id")


def same_segment_beats(first, second):
    """True when two video slots are beats of one segment (same clip and segment id, different shot key)."""
    first_clip = first.get("clip")
    second_clip = second.get("clip")
    if first_clip is None or second_clip is None or first_clip != second_clip:
        return False
    first_segment = segment_id_of(first)
    second_segment = segment_id_of(second)
    if first_segment is None or second_segment is None or first_segment != second_segment:
        return False
    if first.get("shot_key") == second.get("shot_key"):
        return False
    return True


def pointless_neighbour(first, second, similar):
    """True when two neighbouring slots are beats of one segment or a similar pair.

    ``similar`` is a callable ``(first, second) -> bool``.
    """
    return same_segment_beats(first, second) or bool(similar(first, second))


def pointless_reason(kept, dropped):
    """The plain-word reason ``dropped`` left the edit next to ``kept``."""
    if same_segment_beats(kept, dropped):
        return f"pointless jump cut: another beat of {kept.get('shot_key') or kept.get('clip')}"
    return f"pointless jump cut: similar to {kept.get('shot_key') or kept.get('clip')}"


def drop_pointless_neighbours(slots, similar, keep_keys=frozenset()):
    """Drop the weaker of every pointless neighbouring pair; return ``(kept, dropped)``.

    ``dropped`` is a list of ``(slot, reason)``. A pair whose two shot keys are
    both in ``keep_keys`` (the owner asked for both) is kept. The higher score
    wins; a tie keeps the earlier slot. Repeat until no pointless neighbour is
    left.
    """
    kept = list(slots)
    dropped = []
    while True:
        removed = False
        for index in range(len(kept) - 1):
            first = kept[index]
            second = kept[index + 1]
            if not pointless_neighbour(first, second, similar):
                continue
            first_key = first.get("shot_key")
            second_key = second.get("shot_key")
            if first_key in keep_keys and second_key in keep_keys:
                continue
            first_score = first.get("score") or 0.0
            second_score = second.get("score") or 0.0
            if first_score >= second_score:
                kept_slot, dropped_slot, drop_index = first, second, index + 1
            else:
                kept_slot, dropped_slot, drop_index = second, first, index
            dropped.append((dropped_slot, pointless_reason(kept_slot, dropped_slot)))
            del kept[drop_index]
            removed = True
            break
        if not removed:
            break
    return kept, dropped
