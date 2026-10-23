import shutil
import subprocess
import sys
from pathlib import Path
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".githooks" / "pre-commit"
GUARD = ROOT / "scripts" / "check_public.py"


def run_git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def build_repo(tmp_path, with_guard=True):
    repo = tmp_path / "repo"
    (repo / ".githooks").mkdir(parents=True)
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(HOOK, repo / ".githooks" / "pre-commit")
    if with_guard:
        shutil.copy2(GUARD, repo / "scripts" / "check_public.py")
        common = repo / "scripts" / "common"
        common.mkdir()
        shutil.copy2(ROOT / "scripts" / "common" / "__init__.py", common / "__init__.py")
        shutil.copy2(ROOT / "scripts" / "common" / "exit_codes.py", common / "exit_codes.py")
    run_git(repo, "init")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Test")
    return repo


def run_hook(repo):
    return subprocess.run(
        [sys.executable, str(repo / ".githooks" / "pre-commit")],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_hook_source_compiles():
    source = HOOK.read_text(encoding="utf-8")
    compile(source, str(HOOK), "exec")


def test_hook_refuses_staged_media(tmp_path):
    repo = build_repo(tmp_path)
    (repo / "clip.mp4").write_bytes(b"x")
    run_git(repo, "add", "clip.mp4")
    result = run_hook(repo)
    assert result.returncode == 1
    assert "refused" in result.stderr.lower()


def test_hook_allows_clean_commit(tmp_path):
    repo = build_repo(tmp_path)
    (repo / "tool.py").write_text("x = 1\n")
    run_git(repo, "add", "tool.py")
    result = run_hook(repo)
    assert result.returncode == EXIT_OK


def test_hook_fails_closed_without_guard(tmp_path):
    repo = build_repo(tmp_path, with_guard=False)
    result = run_hook(repo)
    assert result.returncode == 1
    assert "missing" in result.stderr.lower()
