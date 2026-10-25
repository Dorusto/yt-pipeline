import re
from pathlib import Path

import llm_director
from yt import available_tools

ROOT = Path(__file__).resolve().parents[1]

# Tools that are not part of the director-facing capability list: development,
# evaluation, admin and internal helpers. Every other tool must be listed.
EXCLUDE = {
    "agent_watchdog",
    "analyze_srt",
    "chapters",
    "check_public",
    "check_stable",
    "colour",
    "correct_srt",
    "costs",
    "daily_summary_srt",
    "doctor",
    "edit_to_fragments",
    "eval_labels",
    "eval_rough_cut",
    "export_filmcraft",
    "fcproj_markers",
    "finish_pip",
    "inbox",
    "kdenlive_from_fragments",
    "matte",
    "notify",
    "proxy",
    "publish_kit",
    "serve",
    "srt_from_fragments",
    "subs_from_edit",
    "thumbnail_concepts",
    "translate_srt",
    "yt",
    "yt_mcp",
}


def test_tool_list_matches_available_tools():
    text = (ROOT / "prompts" / "tools.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"`yt ([a-z_]+)`", text))
    available = set(available_tools(ROOT))
    assert listed <= available, f"listed but missing: {sorted(listed - available)}"
    assert available - EXCLUDE <= listed, f"not listed: {sorted((available - EXCLUDE) - listed)}"


def test_role_prompts_include_the_playbook_without_the_tools():
    prompt = llm_director.read_role_prompt("screenwriter.md")
    assert "# Playbook" in prompt
    assert "# Screenwriter" in prompt
    assert prompt.index("# Playbook") < prompt.index("# Screenwriter")
    assert "`yt quality`" not in prompt


def test_chat_prompt_gets_the_tool_list():
    prompt = llm_director.read_chat_prompt()
    assert "`yt quality`" in prompt
    assert "# Interpret feedback" in prompt


def test_length_tolerance_matches_the_code():
    for name in ("editor.md", "reviewer.md"):
        text = (ROOT / "prompts" / name).read_text(encoding="utf-8")
        match = re.search(r"within (\d+(?:\.\d+)?) s of the target duration", text)
        assert match, name
        assert float(match.group(1)) == llm_director.TARGET_SLACK_S
