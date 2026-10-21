import os

os.environ.setdefault("OPENROUTER_API_KEY", "test")

import translate_srt


def test_short_cue_stays_on_one_line():
    assert translate_srt.wrap_subtitle("I'd ended up with dozens of tasks") == "I'd ended up with dozens of tasks"


def test_long_cue_splits_into_two_balanced_lines():
    text = "It was becoming pretty overwhelming, so in my conversation with Claude I tried to"
    lines = translate_srt.wrap_subtitle(text).split("\n")
    assert len(lines) == 2
    assert all(len(line) <= 42 for line in lines)
    assert " ".join(lines) == text
