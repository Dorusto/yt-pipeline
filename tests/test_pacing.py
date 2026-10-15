from common.pacing import estimate_all_good, shot_length_in_long_cut


def shot(key, score=0.8, start=0.0, end=10.0, **extra):
    row = {"key": key, "eligible": True, "role": "move", "score": score,
           "start_s": start, "end_s": end, "motion_label": "pan_left", "similar_to": []}
    row.update(extra)
    return row


def test_lengths_by_kind():
    assert shot_length_in_long_cut(shot("a")) == 5.0
    assert shot_length_in_long_cut(shot("b", motion_label="static", role="hold")) == 4.0
    assert shot_length_in_long_cut(shot("c", hero=True)) == 6.0
    assert shot_length_in_long_cut(shot("d", end=2.0)) == 2.0
    assert shot_length_in_long_cut({"media_type": "photo"}) == 3.0


def test_estimate_skips_bad_and_duplicate_shots():
    shots = [
        shot("best", score=0.9),
        shot("twin", score=0.7, similar_to=["best"]),
        shot("weak", score=0.3),
        shot("junk", role="accidental"),
        shot("off", eligible=False),
        shot("burst", suppressed_by="best"),
        shot("hold", motion_label="static", role="hold"),
    ]
    assert estimate_all_good({"shots": shots}) == {"seconds": 9.0, "shots": 2}


def test_estimate_of_nothing():
    assert estimate_all_good(None) == {"seconds": 0.0, "shots": 0}
