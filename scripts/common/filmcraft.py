"""Locate the ``filmcraft-cli`` binary shared by the FilmCraft tools."""

import os
import shutil


def cli_path():
    """Return the filmcraft-cli binary from ``$FILMCRAFT_CLI`` or PATH, or None when missing."""
    env = os.environ.get("FILMCRAFT_CLI")
    if env:
        return env
    return shutil.which("filmcraft-cli")
