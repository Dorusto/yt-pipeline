from common.filmcraft import DEFAULT_FRAME_RATE, frame_rate_fraction, parse_frame_rate


def test_parse_frame_rate_reads_num_and_den():
    assert parse_frame_rate({"frame_rate": {"num": 30000, "den": 1001}}) == 30000 / 1001


def test_parse_frame_rate_defaults_when_unset():
    assert parse_frame_rate({}) == DEFAULT_FRAME_RATE
    assert parse_frame_rate(None) == DEFAULT_FRAME_RATE
    assert parse_frame_rate({"frame_rate": {"num": 30}}) == DEFAULT_FRAME_RATE


def test_parse_frame_rate_defaults_when_unusable():
    assert parse_frame_rate({"frame_rate": {"num": 30, "den": 0}}) == DEFAULT_FRAME_RATE
    assert parse_frame_rate({"frame_rate": {"num": "x", "den": 1}}) == DEFAULT_FRAME_RATE
    assert parse_frame_rate({"frame_rate": {"num": -30, "den": 1}}) == DEFAULT_FRAME_RATE


def test_frame_rate_fraction_round_trips():
    assert frame_rate_fraction(25.0) == {"num": 25, "den": 1}
    assert frame_rate_fraction(29.97) == {"num": 2997, "den": 100}
    assert parse_frame_rate({"frame_rate": frame_rate_fraction(25.0)}) == 25.0
