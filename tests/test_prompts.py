import re
from pathlib import Path

import llm_director

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_TOOLS = (
    "quality",
    "motion",
    "beats",
    "loudness",
    "similarity",
    "eval_edit",
    "eval_labels",
    "doctor",
    "ingest",
    "describe",
    "index",
    "director",
    "llm_director",
    "music_pick",
    "render",
    "recap_meta",
    "metadata",
    "thumbnail",
    "graphics_plan",
    "graphics",
    "explain",
    "thumbs",
    "edit_drop",
    "router",
    "audio_clean",
    "transcribe",
    "publish",
    "shorts_rank",
    "shorts_generator",
    "reframe",
    "mode_b",
    "rough_cut",
)


def test_playbook_names_the_tools():
    text = (ROOT / "prompts" / "playbook.md").read_text(encoding="utf-8")
    missing = [name for name in REQUIRED_TOOLS if f"`yt {name}`" not in text]
    assert missing == [], f"playbook does not name: {missing}"


def test_playbook_tools_exist():
    text = (ROOT / "prompts" / "playbook.md").read_text(encoding="utf-8")
    names = re.findall(r"`yt ([a-z_]+)`", text)
    missing = [name for name in names if not (ROOT / "scripts" / f"{name}.py").exists()]
    assert missing == [], f"playbook names tools that do not exist: {missing}"


def test_role_prompts_include_the_playbook():
    prompt = llm_director.read_role_prompt("screenwriter.md")
    assert "# Playbook" in prompt
    assert "# Screenwriter" in prompt
    assert prompt.index("# Playbook") < prompt.index("# Screenwriter")
