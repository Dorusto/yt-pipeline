from fs_probe import safe_is_dir
from helpers import REFERENCE_DIR_ENV, reference_dir


def test_reference_dir_reads_the_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv(REFERENCE_DIR_ENV, str(tmp_path))
    assert reference_dir() == tmp_path


def test_reference_dir_is_a_missing_placeholder_when_unset(monkeypatch):
    monkeypatch.delenv(REFERENCE_DIR_ENV, raising=False)
    assert not safe_is_dir(reference_dir())
