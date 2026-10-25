import json
import subprocess

import check_public


def test_blocks_media_and_subtitles(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "clip.mp4").write_bytes(b"x")
    (tmp_path / "talk.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nhello\n")
    assert check_public.check_file("clip.mp4")
    assert check_public.check_file("talk.srt")


def test_allows_fixtures_and_plain_code(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tests" / "fixtures").mkdir(parents=True)
    (tmp_path / "tests" / "fixtures" / "tiny.srt").write_text("1\n")
    (tmp_path / "tool.py").write_text("print('hello')\n")
    assert check_public.check_file("tests/fixtures/tiny.srt") == []
    assert check_public.check_file("tool.py") == []


def test_blocks_secrets_and_base64_images(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.md").write_text("key: sk-or-v1-" + "a" * 40 + "\n")
    (tmp_path / "b.html").write_text('<img src="data:image/jpeg;base64,' + "A" * 300 + '">')
    assert "openrouter key" in check_public.check_file("a.md")[0]
    assert "base64 image" in check_public.check_file("b.html")[0]


def test_blocks_large_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "big.txt").write_text("x" * 1_100_000)
    assert "MB" in check_public.check_file("big.txt")[0]


def test_blocks_private_ipv4_outside_gitignored(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    home = ".".join(["192", "168", "1", "10"])
    bind = ".".join(["10", "0", "0", "5"])
    (tmp_path / "host.md").write_text(f"server http://{home}:8091\n")
    (tmp_path / "range.md").write_text(f"bind {bind}\n")
    (tmp_path / "public.md").write_text("open http://127.0.0.1:8091 and 8.8.8.8\n")
    assert "private IPv4" in check_public.check_file("host.md")[0]
    assert "private IPv4" in check_public.check_file("range.md")[0]
    assert check_public.check_file("public.md") == []


def test_allows_private_ipv4_in_gitignored_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    bind = ".".join(["10", "0", "0", "5"])
    (tmp_path / ".gitignore").write_text("local.yaml\n", encoding="utf-8")
    (tmp_path / "local.yaml").write_text(f"ntfy: http://{bind}:8090\n", encoding="utf-8")
    assert check_public.check_file("local.yaml") == []
    (tmp_path / "shared.md").write_text(f"ntfy: http://{bind}:8090\n", encoding="utf-8")
    assert "private IPv4" in check_public.check_file("shared.md")[0]


def test_blocks_the_owner_first_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    name = "Dor" + "u"
    (tmp_path / "a.md").write_text(f"{name} filmed this\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("the Dorusto handle stays\n", encoding="utf-8")
    assert "owner's first name" in check_public.check_file("a.md")[0]
    assert check_public.check_file("b.md") == []


def test_main_json_and_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ok.py").write_text("x = 1\n")
    (tmp_path / "bad.png").write_bytes(b"\x89PNG")
    assert check_public.main(["ok.py", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert check_public.main(["ok.py", "bad.png", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["problems"]
