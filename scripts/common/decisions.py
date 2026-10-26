# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the edit.json ``decisions`` list: every candidate used or rejected.

The directors call :func:`build_decisions` with the shotlog, the slots they
used and the pool drops they know about; the result is one row per candidate
id (``0071/s2``) with a plain-word reason, which ``scripts/explain.py`` renders
and feedback refers to. Sources are read only; nothing here writes files.
"""

from common.intent import is_deliberate_shot
from common.scoring import counts_as_defect

ROLE_REJECT_REASONS = {
    "setup": "setup/teardown",
    "teardown": "setup/teardown",
    "accidental": "accidental",
}

REASON_GROUPS = (
    ("near-duplicate", "near duplicate"),
    ("setup/teardown", "setup/teardown"),
    ("accidental", "accidental"),
    ("shake", "shaky"),
    ("blur", "blurry"),
    ("defect", "defect"),
    ("overlapping", "overlapping window"),
    ("low score", "low score"),
    ("did not fit", "did not fit the length"),
    ("not eligible", "not eligible"),
)


def used_shot_keys(edit_document):
    """The shot keys edit.json marks as used (status == 'used'), or an empty set."""
    if not isinstance(edit_document, dict):
        return set()
    rows = edit_document.get("decisions")
    if not isinstance(rows, list):
        return set()
    return {
        str(row["id"])
        for row in rows
        if isinstance(row, dict) and row.get("status") == "used" and row.get("id")
    }


def _reject_reason(shot, in_pool, used_floor, used_ids=()):
    """The plain-word reason a shotlog shot was not used, from its own state."""
    suppressed = shot.get("suppressed_by")
    if suppressed:
        return f"near-duplicate of {suppressed}"
    role = shot.get("role") or ""
    if role in ROLE_REJECT_REASONS:
        return ROLE_REJECT_REASONS[role]
    if not shot.get("eligible", True):
        return ROLE_REJECT_REASONS.get(role, "not eligible")
    for other in shot.get("similar_to") or ():
        if other in used_ids:
            return f"near-duplicate of {other}"
    deliberate = is_deliberate_shot(shot.get("motion_label"), shot.get("semantic_motion"))
    defects = [
        name
        for name in (shot.get("defects") or [])
        if counts_as_defect(name, deliberate)
    ]
    minor_photo_flaw = shot.get("media_type") == "photo" and shot.get("eligible", True)
    if defects and not minor_photo_flaw:
        return f"defect: {', '.join(defects)}"
    note = f" (minor flaw: {', '.join(defects)})" if defects and minor_photo_flaw else ""
    score = shot.get("score")
    if used_floor is not None and score is not None and score < used_floor:
        return "low score" + note
    if in_pool:
        return "did not fit the length" + note
    return "low score" + note


def build_decisions(shotlog, used_rows, pool_keys=None, drop_reasons=None):
    """Return the ``decisions`` rows for one edit, sorted by candidate id.

    ``used_rows`` is an iterable of dicts with ``id`` (the shot key), ``slot``,
    ``reason`` and optionally ``score``/``clip``/``window_in_s``/``window_out_s``
    plus ``extra_ids`` (a phrase's landing hold, marked used with the same
    slot). ``pool_keys`` are the ids that entered the director's candidate
    pool; ``drop_reasons`` maps pool ids dropped before picking to a reason
    (near-duplicate, overlapping window). Every id in the shotlog, the used
    rows, ``pool_keys`` or ``drop_reasons`` gets exactly one row.
    """
    shotlog = shotlog or {}
    used_rows = list(used_rows or [])
    pool_keys = set(pool_keys or ())
    drop_reasons = dict(drop_reasons or {})
    used_by_id = {}
    for row in used_rows:
        used_by_id[row["id"]] = row
        for extra in row.get("extra_ids") or ():
            used_by_id.setdefault(extra, row)
    scores = [row["score"] for row in used_rows if row.get("score") is not None]
    used_floor = min(scores) if scores else None
    universe = set(shotlog) | set(used_by_id) | pool_keys | set(drop_reasons)
    decisions = []
    for key in universe:
        shot = shotlog.get(key) or {}
        row = used_by_id.get(key)
        if row is not None:
            reason = row["reason"]
            if key != row["id"]:
                reason = f"kept with {row['id']} (move and its landing)"
            decisions.append(
                {
                    "id": key,
                    "status": "used",
                    "reason": reason,
                    "slot": row.get("slot"),
                    "score": row.get("score", shot.get("score")),
                    "clip": row.get("clip", shot.get("clip")),
                    "window_in_s": row.get("window_in_s", shot.get("start_s")),
                    "window_out_s": row.get("window_out_s", shot.get("end_s")),
                }
            )
            continue
        reason = drop_reasons.get(key) or _reject_reason(shot, key in pool_keys, used_floor, set(used_by_id))
        decisions.append(
            {
                "id": key,
                "status": "rejected",
                "reason": reason,
                "slot": None,
                "score": shot.get("score"),
                "clip": shot.get("clip"),
                "window_in_s": shot.get("start_s"),
                "window_out_s": shot.get("end_s"),
            }
        )
    decisions.sort(key=lambda item: item["id"])
    return decisions


def flow_decisions(plan, report, shotlog, facts):
    """The ``decisions[]`` rows of the film-crew flow: one used row per plan shot plus the editor's could-not-do items."""
    report_by_id = {row.get("id"): row for row in (report or {}).get("shots") or []}
    used_keys = []
    rows = []
    for shot in plan.get("shots") or []:
        key = shot.get("shot_key")
        used_keys.append(key)
        fact = facts.get(key) or {}
        entry = shotlog.get(key) or {}
        row = report_by_id.get(shot.get("id")) or {}
        reason = f"{shot.get('purpose')} — {shot.get('why')}"
        asked = row.get("technique_asked")
        done = row.get("technique_done")
        why_not = row.get("why_not")
        if done == asked and asked != "none":
            reason += f" (technique {asked} done)"
        elif done is not None and done != asked:
            reason += f" (technique {asked} done as {done})"
        elif done is None and why_not:
            reason += f" (technique {asked} not done: {why_not})"
        rows.append(
            {
                "id": key,
                "status": "used",
                "reason": reason,
                "slot": shot.get("id"),
                "score": entry.get("score"),
                "clip": entry.get("clip"),
                "window_in_s": fact.get("window_in_s"),
                "window_out_s": fact.get("window_out_s"),
            }
        )
    for item in (report or {}).get("could_not_do") or []:
        named = next((key for key in used_keys if key and key in item), None)
        rows.append(
            {
                "id": named or "editor",
                "status": "rejected",
                "reason": item,
                "slot": None,
                "score": None,
                "clip": None,
                "window_in_s": None,
                "window_out_s": None,
            }
        )
    return rows
