from common.sizes import BYTES_PER_GB, format_gb, format_size_short


def test_format_gb():
    assert format_gb(BYTES_PER_GB) == "1 GB"
    assert format_gb(0) == "0 GB"


def test_format_size_short():
    assert format_size_short(500 * 1024) == "0 MB"
    assert format_size_short(45 * 1024 * 1024) == "45 MB"
    assert format_size_short(1536 * 1024 * 1024) == "1.5 GB"
