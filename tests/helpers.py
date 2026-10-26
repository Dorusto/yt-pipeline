import os
import subprocess
from pathlib import Path

from common.llm import MockClient

REFERENCE_DIR_ENV = "YT_PIPELINE_REFERENCE_DIR"
REFERENCE_DIR_PLACEHOLDER = Path("/nonexistent/reference")


def reference_dir():
    """The host's reference-material folder, from ``YT_PIPELINE_REFERENCE_DIR``.

    Unset means the reference tests skip; the repo never carries a personal path.
    """
    value = os.environ.get(REFERENCE_DIR_ENV)
    return Path(value) if value else REFERENCE_DIR_PLACEHOLDER


def make_click(path, duration=8.0, bpm=120.0, accent=False):
    """Write a synthetic click track (a short 1 kHz pulse every beat) to ``path``."""
    period = 60.0 / bpm
    pulse = f"lt(mod(t,{period}),0.02)"
    amplitude = f"(0.5+0.9*eq(mod(floor(t/{period}),4),0))" if accent else "0.9"
    expression = f"{amplitude}*sin(2*PI*1000*t)*{pulse}"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"aevalsrc='{expression}':s=22050:d={duration}",
         "-ac", "1", "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True,
    )


class SequenceClient(MockClient):
    """A mock client returning a different canned answer per call, for the retry tests."""

    def __init__(self, answers, cost_per_call=0.0, **kwargs):
        super().__init__(cost_per_call=cost_per_call, **kwargs)
        self.answers = list(answers)
        self.calls = 0

    def complete(self, role, messages, model=None, **kwargs):
        index = min(self.calls, len(self.answers) - 1)
        self._default_text = self.answers[index]
        self.calls += 1
        return super().complete(role, messages, model=model, **kwargs)
