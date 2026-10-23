import json

import check_stable

CANDIDATE = """# RELEASES

## v0.1.0 (candidate, not released)

**Stable candidate**

```stable-files
scripts/serve.py
scripts/recap.py
scripts/common/versions.py
```
"""

RELEASED = """# RELEASES

## v0.1.0

**Stable**

```stable-files
scripts/serve.py
scripts/recap.py
```
"""


def test_parse_candidate_is_not_enforced():
    version, enforced, stable_files = check_stable.parse_stable_files(CANDIDATE)
    assert version == "v0.1.0"
    assert enforced is False
    assert stable_files == ["scripts/serve.py", "scripts/recap.py", "scripts/common/versions.py"]


def test_parse_released_is_enforced():
    version, enforced, stable_files = check_stable.parse_stable_files(RELEASED)
    assert version == "v0.1.0"
    assert enforced is True
    assert stable_files == ["scripts/serve.py", "scripts/recap.py"]


def test_parse_without_version_section():
    assert check_stable.parse_stable_files("# RELEASES\n\nnothing here\n") == (None, False, [])


def test_find_violations():
    stable = ["scripts/serve.py", "scripts/recap.py"]
    assert check_stable.find_violations(["scripts/serve.py", "docs/x.md"], stable) == ["scripts/serve.py"]
    assert check_stable.find_violations(["docs/x.md", "scripts/other.py"], stable) == []


def test_has_approval():
    assert check_stable.has_approval("feat: x\n\nStable-change: approved\n")
    assert check_stable.has_approval("Stable-change: approved")
    assert not check_stable.has_approval("feat: x\n")
    assert not check_stable.has_approval("Stable-change: pending\n")


def _write_inputs(tmp_path, releases_text, changed, messages):
    releases = tmp_path / "RELEASES.md"
    releases.write_text(releases_text, encoding="utf-8")
    changed_file = tmp_path / "changed.txt"
    changed_file.write_text("\n".join(changed) + "\n", encoding="utf-8")
    messages_file = tmp_path / "messages.txt"
    messages_file.write_text(messages, encoding="utf-8")
    return releases, changed_file, messages_file


def test_main_blocks_stable_change_without_trailer(tmp_path, capsys):
    releases, changed_file, messages_file = _write_inputs(
        tmp_path, RELEASED, ["scripts/serve.py"], "feat: x\n"
    )
    code = check_stable.main([
        "--releases", str(releases),
        "--changed-files", str(changed_file),
        "--commit-messages", str(messages_file),
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["violations"] == ["scripts/serve.py"]


def test_main_allows_stable_change_with_trailer(tmp_path, capsys):
    releases, changed_file, messages_file = _write_inputs(
        tmp_path, RELEASED, ["scripts/serve.py"], "feat: x\n\nStable-change: approved\n"
    )
    code = check_stable.main([
        "--releases", str(releases),
        "--changed-files", str(changed_file),
        "--commit-messages", str(messages_file),
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["approved"] is True


def test_main_allows_non_stable_change(tmp_path, capsys):
    releases, changed_file, messages_file = _write_inputs(
        tmp_path, RELEASED, ["docs/x.md"], "feat: x\n"
    )
    code = check_stable.main([
        "--releases", str(releases),
        "--changed-files", str(changed_file),
        "--commit-messages", str(messages_file),
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["violations"] == []


def test_main_candidate_is_not_enforced(tmp_path, capsys):
    releases, changed_file, messages_file = _write_inputs(
        tmp_path, CANDIDATE, ["scripts/serve.py"], "feat: x\n"
    )
    code = check_stable.main([
        "--releases", str(releases),
        "--changed-files", str(changed_file),
        "--commit-messages", str(messages_file),
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["enforced"] is False


def test_main_missing_releases_is_usage_error(tmp_path, capsys):
    code = check_stable.main(["--releases", str(tmp_path / "missing.md"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["ok"] is False
