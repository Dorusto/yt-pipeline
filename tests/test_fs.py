import pytest

import fs_probe
from helpers import REFERENCE_DIR_PLACEHOLDER


def test_safe_is_dir_true_and_false(tmp_path):
    assert fs_probe.safe_is_dir(tmp_path) is True
    assert fs_probe.safe_is_dir(tmp_path / "missing") is False


def test_safe_is_file_true_and_false(tmp_path):
    file = tmp_path / "one.txt"
    file.write_text("x", encoding="utf-8")
    assert fs_probe.safe_is_file(file) is True
    assert fs_probe.safe_is_file(tmp_path / "missing") is False


@pytest.mark.parametrize("helper,method", [
    (fs_probe.safe_is_dir, "is_dir"),
    (fs_probe.safe_is_file, "is_file"),
])
def test_probes_return_false_on_oserror(helper, method, monkeypatch):
    def boom(self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("pathlib.Path." + method, boom)
    assert helper(str(REFERENCE_DIR_PLACEHOLDER)) is False
