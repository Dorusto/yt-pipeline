"""Resolve the repository root and the interpreter for the yt entry point."""

import os
from pathlib import Path


class EntryError(RuntimeError):
    """Raised when the entry point cannot resolve the root or the interpreter."""


def resolve_repo_root():
    """Return the repository root, from ``YT_PIPELINE_HOME`` or by walking up from this script."""
    home = os.environ.get("YT_PIPELINE_HOME")
    if home:
        root = Path(home).expanduser().resolve()
        if not (root / "scripts").is_dir() or not (root / "requirements.txt").is_file():
            raise EntryError(
                f"YT_PIPELINE_HOME does not hold scripts/ and requirements.txt: {root}"
            )
        return root
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts").is_dir() and (parent / "requirements.txt").is_file():
            return parent
    raise EntryError(
        "could not find the repository root; set YT_PIPELINE_HOME to the folder"
        " holding scripts/ and requirements.txt"
    )


def resolve_python(root):
    """Return the interpreter to run the tools with.

    After the local ``.venv`` it looks in the plugin data directory
    (``~/.claude/plugins/data/``) for the virtual environment the plugin's
    SessionStart hook creates.
    """
    venv = os.environ.get("YT_PIPELINE_VENV")
    if venv:
        base = Path(venv).expanduser().resolve()
        python = base / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.is_file():
            raise EntryError(f"YT_PIPELINE_VENV has no interpreter: {python}")
        return str(python)
    local = root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    if local.is_file():
        return str(local)
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        plugin_python = Path(plugin_data) / (
            "venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python"
        )
        if plugin_python.is_file():
            return str(plugin_python)
    for marker in sorted(Path.home().glob(".claude/plugins/data/*/yt-pipeline-venv")):
        try:
            recorded = marker.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not recorded:
            continue
        python = Path(recorded) / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        if python.is_file():
            return str(python)
    return "python3"
