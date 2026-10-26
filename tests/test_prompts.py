import re
from pathlib import Path

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
    assert "director_editor" in listed
