# SPDX-License-Identifier: AGPL-3.0-or-later
"""The director metadata of an edit: the reviewer's last verdict."""


def director_review(document):
    """``meta.director.review`` from an edit document, or None when it is absent."""
    if not isinstance(document, dict):
        return None
    meta = document.get("meta")
    if not isinstance(meta, dict):
        return None
    director = meta.get("director")
    if not isinstance(director, dict):
        return None
    review = director.get("review")
    return review if isinstance(review, dict) else None
