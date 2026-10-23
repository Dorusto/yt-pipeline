import subprocess

import pytest

from common.git import GitError, git_lines


def test_git_lines_returns_lines_of_a_real_call(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    lines = git_lines("-C", str(tmp_path), "diff", "--cached", "--name-only")
    assert lines == ["a.txt"]


def test_git_lines_raises_on_a_bad_command(tmp_path):
    with pytest.raises(GitError):
        git_lines("-C", str(tmp_path), "not-a-git-command")
