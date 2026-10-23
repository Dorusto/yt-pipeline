# SPDX-License-Identifier: AGPL-3.0-or-later
"""File names a workspace leaves out of the edit, without deleting anything.

The names live in ``<workspace>/excluded.json`` as ``{"excluded": [...]}``.
Every station that lists clips or photos skips them; the files stay on disk.
"""

import json
import os
from pathlib import Path

EXCLUDED_FILE = "excluded.json"


def load_excluded(workspace):
    """The set of excluded file names in a workspace, or an empty set."""
    return load_excluded_path(Path(workspace) / EXCLUDED_FILE)


def load_excluded_path(path):
    """The set of excluded file names in an excluded.json file, or an empty set."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    names = data.get("excluded")
    if not isinstance(names, list):
        return set()
    return {str(name) for name in names if isinstance(name, str) and name}


def write_excluded(workspace, names):
    """Write the excluded names atomically, sorted, as {"excluded": [...]}."""
    path = Path(workspace) / EXCLUDED_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps({"excluded": sorted(names)}, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def exclude_name(workspace, name):
    """Add one name to the excluded set; return the new set."""
    names = load_excluded(workspace)
    names.add(name)
    write_excluded(workspace, names)
    return names


def include_name(workspace, name):
    """Remove one name from the excluded set; return the new set."""
    names = load_excluded(workspace)
    names.discard(name)
    write_excluded(workspace, names)
    return names


def is_excluded(name, excluded):
    """True when a file name or its stem matches an excluded name or its stem."""
    if not excluded:
        return False
    stem = Path(str(name)).stem
    for candidate in excluded:
        candidate_stem = Path(str(candidate)).stem
        if name == candidate or stem == candidate or name == candidate_stem or stem == candidate_stem:
            return True
    return False


def filter_excluded(paths, excluded):
    """Drop the paths whose name or stem is excluded; return a list."""
    if not excluded:
        return list(paths)
    return [path for path in paths if not is_excluded(Path(path).name, excluded)]
