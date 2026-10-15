"""Numbered snapshots of a workspace's edit and its renders.

Each re-edit overwrites ``edit.json`` and the recap files in the workspace.
``snapshot`` keeps the current state under ``versions/vNNN/`` so older edits
stay playable and comparable. Files are hard-linked when the file system
allows it (renders replace their output atomically, so a link never changes
afterwards); the small JSON and HTML files are copied. Only files that belong to the edit are kept
in one version: a render older than ``edit.json`` is from an earlier edit and
is left out.
"""

import json
import os
import shutil
import time
from pathlib import Path

VERSIONS_DIR = "versions"
META_NAME = "version.json"
EDIT_NAME = "edit.json"
RENDER_NAMES = (
    "recap_720p.mp4",
    "recap_720p_9x16.mp4",
    "recap_final.mp4",
    "recap_final_9x16.mp4",
    "explain.html",
)


def version_dirs(workspace):
    """The version folders of ``workspace``, oldest first."""
    root = Path(workspace) / VERSIONS_DIR
    if not root.is_dir():
        return []
    found = []
    for child in root.iterdir():
        if child.is_dir() and child.name.startswith("v") and child.name[1:].isdigit():
            found.append(child)
    return sorted(found, key=lambda path: int(path.name[1:]))


def read_meta(directory):
    """The ``version.json`` of one version folder, or an empty dict."""
    try:
        data = json.loads((Path(directory) / META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def list_versions(workspace):
    """One dict per version, oldest first: number, folder, files and metadata."""
    versions = []
    for directory in version_dirs(workspace):
        files = [name for name in (EDIT_NAME, *RENDER_NAMES) if (directory / name).is_file()]
        versions.append(
            {
                "number": int(directory.name[1:]),
                "name": directory.name,
                "path": directory,
                "files": files,
                "meta": read_meta(directory),
            }
        )
    return versions


def matching_files(workspace, keep_renders=False):
    """The edit and the renders that were made from that same edit.

    With ``keep_renders`` every render is kept and the edit only when it is
    not newer than all of them: used once, to save the state of a workspace
    that existed before versions did.
    """
    workspace = Path(workspace)
    edit = workspace / EDIT_NAME
    edit_time = edit.stat().st_mtime if edit.is_file() else None
    renders = [name for name in RENDER_NAMES if (workspace / name).is_file()]
    if keep_renders:
        render_times = [(workspace / name).stat().st_mtime for name in renders if name.endswith(".mp4")]
        names = list(renders)
        if edit_time is not None and render_times and edit_time <= max(render_times):
            names.insert(0, EDIT_NAME)
        return names
    names = [EDIT_NAME] if edit_time is not None else []
    for name in renders:
        if edit_time is None or (workspace / name).stat().st_mtime >= edit_time:
            names.append(name)
    return names


def keep_file(source, target):
    """Hard-link a render (renders are replaced atomically), copy everything else."""
    if source.suffix == ".mp4":
        try:
            os.link(source, target)
            return
        except OSError:
            pass
    shutil.copy2(source, target)


def same_file(kept, current):
    if not kept.is_file():
        return False
    if os.path.samefile(kept, current):
        return True
    if kept.suffix == ".mp4":
        return False
    return kept.read_bytes() == current.read_bytes()


def same_as_latest(workspace, names):
    """True when the latest version already holds exactly these files."""
    versions = version_dirs(workspace)
    if not versions:
        return False
    latest = versions[-1]
    return all(same_file(latest / name, Path(workspace) / name) for name in names)


def snapshot(workspace, meta=None, keep_renders=False):
    """Keep the current edit and its renders as the next version; return its folder.

    Returns None when there is nothing to keep or the latest version already
    holds the same files.
    """
    workspace = Path(workspace)
    names = matching_files(workspace, keep_renders=keep_renders)
    renders = [name for name in names if name.endswith(".mp4")]
    if not renders and EDIT_NAME not in names:
        return None
    if same_as_latest(workspace, names):
        return None
    existing = version_dirs(workspace)
    number = int(existing[-1].name[1:]) + 1 if existing else 1
    directory = workspace / VERSIONS_DIR / f"v{number:03d}"
    directory.mkdir(parents=True, exist_ok=False)
    for name in names:
        keep_file(workspace / name, directory / name)
    record = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "files": names}
    record.update(meta or {})
    (directory / META_NAME).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return directory
