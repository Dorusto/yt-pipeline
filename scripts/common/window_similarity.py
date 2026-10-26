# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compare the frames inside two slots' windows, not the whole segments.

The director's neighbour check embeds the frames inside each slot's
``in_s..out_s`` with the same backend and interior spacing ``similarity.py``
uses and caches the vectors per window key in
``<workspace>/similarity_windows.json`` (atomic write), so a rerun is cheap.
Whole-segment similarity stays in ``similarity.json`` for the menu the model
sees; only the neighbour check uses window scores.
"""

from pathlib import Path

import numpy as np

from common.embed import (
    DEFAULT_BACKEND,
    DEFAULT_MODEL_PATH,
    DEFAULT_MODEL_URL,
    DEFAULT_THRESHOLD,
    EmbedError,
    embed_frames,
    frame_times,
    load_mobilenet,
    read_frames,
)
from common.jsonio import load_json, write_json

WINDOW_CACHE_NAME = "similarity_windows.json"


def window_key(clip, in_s, out_s):
    """The cache key of one slot window: ``<clip>|<in_s>|<out_s>`` rounded to 3 decimals."""
    return f"{clip}|{round(float(in_s), 3)}|{round(float(out_s), 3)}"


class WindowEmbedder:
    """Embed the frames inside a slot's in_s..out_s and compare two windows.

    The clip is read from ``<workspace>/proxy/<clip>`` when it exists, else
    ``<workspace>/<clip>``. Embeddings are cached per window key in
    ``<workspace>/similarity_windows.json`` (atomic write) so a rerun is cheap.
    """

    def __init__(
        self,
        workspace,
        backend=DEFAULT_BACKEND,
        model_path=DEFAULT_MODEL_PATH,
        model_url=DEFAULT_MODEL_URL,
        threshold=DEFAULT_THRESHOLD,
    ):
        self.workspace = Path(workspace)
        self.backend = backend
        self.model_path = model_path
        self.model_url = model_url
        self.threshold = threshold
        self._net = None
        self._cache = self._load_cache()
        self._dirty = False

    def embedding(self, clip, in_s, out_s):
        """The cached embedding of the window, computed and cached on first use."""
        key = window_key(clip, in_s, out_s)
        if key not in self._cache:
            path = self._clip_path(clip)
            frames = read_frames(path, frame_times(in_s, out_s), error=EmbedError)
            vector = embed_frames(frames, self.backend, self._net_or_load())
            self._cache[key] = [round(float(value), 4) for value in vector]
            self._dirty = True
        return np.asarray(self._cache[key], dtype=np.float64)

    def similarity(self, first, second):
        """The cosine similarity of two slot windows."""
        vector_a = self.embedding(first["clip"], first["in_s"], first["out_s"])
        vector_b = self.embedding(second["clip"], second["in_s"], second["out_s"])
        return float(np.dot(vector_a, vector_b))

    def similar(self, first, second):
        """True when both slots have a window and their similarity reaches the threshold.

        A slot without ``in_s``/``out_s`` (a photo) returns ``False``; a clip that
        cannot be read raises ``EmbedError`` and the caller decides.
        """
        if not _has_window(first) or not _has_window(second):
            return False
        return self.similarity(first, second) >= self.threshold

    def save(self):
        """Write the cache atomically when it changed."""
        if not self._dirty:
            return
        write_json(
            self.workspace / WINDOW_CACHE_NAME,
            {
                "backend": self.backend,
                "model": self._model_name(),
                "threshold": self.threshold,
                "windows": self._cache,
            },
        )
        self._dirty = False

    def _net_or_load(self):
        if self.backend != "mobilenet":
            return None
        if self._net is None:
            self._net = load_mobilenet(self.model_path, self.model_url)
        return self._net

    def _model_name(self):
        return Path(self.model_path).name if self.backend == "mobilenet" else "none"

    def _clip_path(self, clip):
        proxy = self.workspace / "proxy" / clip
        if proxy.exists():
            return proxy
        return self.workspace / clip

    def _load_cache(self):
        data = load_json(self.workspace / WINDOW_CACHE_NAME)
        if not isinstance(data, dict):
            return {}
        if data.get("backend") != self.backend or data.get("model") != self._model_name():
            return {}
        windows = data.get("windows")
        if not isinstance(windows, dict):
            return {}
        return {key: value for key, value in windows.items() if isinstance(value, list)}


def _has_window(slot):
    return slot.get("in_s") is not None and slot.get("out_s") is not None
