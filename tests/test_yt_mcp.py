import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest
from mcp.types import ImageContent

import yt_mcp
from common.plugins import (
    plugin_names,
    plugin_tools,
    skill_json_contract,
    tool_skill,
)

ROOT = Path(__file__).resolve().parents[1]

STUB_TOOL = 'print("usage: stub_tool")\nprint(\'{"ok": true, "tool": "stub"}\')\n'
FAIL_TOOL = (
    'import sys\n'
    'print(\'{"ok": false, "error": "boom", "exit_code": 2}\')\n'
    'sys.exit(2)\n'
)


def make_stub_root(tmp_path, tools):
    root = tmp_path / "root"
    (root / "scripts").mkdir(parents=True)
    (root / ".claude-plugin").mkdir()
    skill_dir = root / ".claude" / "skills" / "yt-test"
    skill_dir.mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "yt.py", root / "scripts" / "yt.py")
    (root / "scripts" / "common").symlink_to(ROOT / "scripts" / "common")
    (root / "requirements.txt").write_text("", encoding="utf-8")
    (root / "scripts" / "stub_tool.py").write_text(STUB_TOOL, encoding="utf-8")
    (root / "scripts" / "fail_tool.py").write_text(FAIL_TOOL, encoding="utf-8")
    marketplace = {
        "name": "yt-pipeline",
        "plugins": [
            {
                "name": "yt-test",
                "source": "./",
                "skills": ["./.claude/skills/yt-test"],
            }
        ],
    }
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(marketplace), encoding="utf-8"
    )
    (skill_dir / "SKILL.md").write_text(
        "\n".join(f"yt {name}" for name in tools) + "\n", encoding="utf-8"
    )
    return root


def use_stub_env(monkeypatch, root):
    monkeypatch.setenv("YT_PIPELINE_HOME", str(root))
    monkeypatch.setattr(yt_mcp, "resolve_python", lambda root: sys.executable)


def call(server, name, arguments):
    return asyncio.run(server.call_tool(name, arguments))


def text_of(result):
    return result.content[0].text


def json_of(result):
    return json.loads(result.content[0].text)


def test_plugin_names_and_tools():
    assert plugin_names(ROOT) == [
        "yt-director",
        "yt-publish",
        "yt-shorts",
        "yt-editor",
        "yt-admin",
    ]
    tools = plugin_tools(ROOT, "yt-director")
    for name in ("recap", "director", "llm_director", "render"):
        assert name in tools
    for name in tools:
        assert (ROOT / "scripts" / f"{name}.py").is_file()


def test_stub_root_grouping_and_server(tmp_path, monkeypatch):
    root = make_stub_root(tmp_path, ["stub_tool"])
    use_stub_env(monkeypatch, root)
    assert plugin_tools(root, "yt-test") == ["stub_tool"]
    server = yt_mcp.build_server("yt-test", root=root)
    assert "stub_tool" in text_of(call(server, "yt_list", {}))
    described = json_of(call(server, "yt_describe", {"tool": "stub_tool"}))
    assert "stub_tool" in described["help"]


def test_yt_list_one_line_per_tool(tmp_path, monkeypatch):
    root = make_stub_root(tmp_path, ["stub_tool", "fail_tool"])
    use_stub_env(monkeypatch, root)
    server = yt_mcp.build_server("yt-test", root=root)
    lines = text_of(call(server, "yt_list", {})).splitlines()
    assert len(lines) == len(plugin_tools(root, "yt-test"))
    assert lines[0].startswith("fail_tool: ")


def test_yt_describe_doctor(monkeypatch):
    monkeypatch.setattr(yt_mcp, "resolve_python", lambda root: sys.executable)
    server = yt_mcp.build_server("yt-director", root=ROOT)
    described = json_of(call(server, "yt_describe", {"tool": "doctor"}))
    assert described["ok"] is True
    assert "usage" in described["help"]
    assert described["json"] is True


def test_yt_describe_returns_json_contract(monkeypatch):
    monkeypatch.setattr(yt_mcp, "resolve_python", lambda root: sys.executable)
    server = yt_mcp.build_server("yt-director", root=ROOT)
    described = json_of(call(server, "yt_describe", {"tool": "director"}))
    assert described["contract"]["example"].startswith("The `--json` output")
    assert "status" in described["contract"]["example"]
    assert "exit" in described["contract"]["exit_codes"]
    none = json_of(call(server, "yt_describe", {"tool": "doctor"}))
    assert none["contract"] is None


def test_tool_skill_and_json_contract():
    assert tool_skill(ROOT, "director") == "yt-director"
    assert tool_skill(ROOT, "notebook") == "yt-notebook"
    assert tool_skill(ROOT, "metadata") == "yt-publish"
    assert skill_json_contract(ROOT, "yt-director")["example"].startswith("The `--json` output")
    assert skill_json_contract(ROOT, "yt-recap") is None


def test_yt_run_success(tmp_path, monkeypatch):
    root = make_stub_root(tmp_path, ["stub_tool"])
    use_stub_env(monkeypatch, root)
    server = yt_mcp.build_server("yt-test", root=root)
    result = json_of(call(server, "yt_run", {"tool": "stub_tool", "args": []}))
    assert result == {"ok": True, "tool": "stub"}


def test_yt_run_structured_errors(tmp_path, monkeypatch):
    root = make_stub_root(tmp_path, ["stub_tool", "fail_tool"])
    use_stub_env(monkeypatch, root)
    server = yt_mcp.build_server("yt-test", root=root)
    missing = json_of(call(server, "yt_run", {"tool": "nope", "args": []}))
    assert missing["ok"] is False
    assert missing["exit_code"] == 2
    failed = json_of(call(server, "yt_run", {"tool": "fail_tool", "args": []}))
    assert failed == {"ok": False, "error": "boom", "exit_code": 2}


def test_yt_preview(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video-bytes")
    monkeypatch.setattr(
        yt_mcp,
        "extract_frame",
        lambda path, t_s, out, width: Path(out).write_bytes(Path(path).read_bytes()),
    )
    monkeypatch.setattr(yt_mcp, "probe_duration", lambda path: 10.0)

    image = yt_mcp.preview_image(str(video))
    assert isinstance(image, yt_mcp.Image)
    assert image.data == b"video-bytes"

    workspace = tmp_path / "ws"
    (workspace / "thumbs").mkdir(parents=True)
    (workspace / "thumbs" / "poster.jpg").write_bytes(b"poster-bytes")
    poster = yt_mcp.preview_image(str(workspace))
    assert poster.data == b"poster-bytes"

    with pytest.raises(yt_mcp.PreviewError):
        yt_mcp.preview_image(str(tmp_path / "missing"))

    server = yt_mcp.build_server("yt-director", root=ROOT)
    result = call(server, "yt_preview", {"path": str(video)})
    content = result.content[0]
    assert isinstance(content, ImageContent)
    assert content.mime_type == "image/jpeg"


def test_yt_preview_poster_returns_bytes(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "thumbs").mkdir(parents=True)
    poster_bytes = b"poster-bytes"
    (workspace / "thumbs" / "poster.jpg").write_bytes(poster_bytes)

    image = yt_mcp.preview_image(str(workspace))
    assert isinstance(image, yt_mcp.Image)
    assert image.data == poster_bytes
    assert image._format == "jpeg"
