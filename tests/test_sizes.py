from common.sizes import BYTES_PER_GB, format_gb


def test_format_gb():
    assert format_gb(BYTES_PER_GB) == "1 GB"
    assert format_gb(0) == "0 GB"
