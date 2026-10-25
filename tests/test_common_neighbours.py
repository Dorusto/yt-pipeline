import common.neighbours as neighbours


def slot(shot_key, clip, segment_id, score, segment=None):
    row = {"shot_key": shot_key, "clip": clip, "segment_id": segment_id, "score": score}
    if segment is not None:
        row["segment"] = segment
    return row


def test_same_segment_beats_true_for_two_beats_of_one_segment():
    first = slot("0072/s7b1", "clip_0072.mp4", "s7", 0.5)
    second = slot("0072/s7b2", "clip_0072.mp4", "s7", 0.6)
    assert neighbours.same_segment_beats(first, second) is True


def test_same_segment_beats_false_for_different_clips():
    first = slot("0072/s7b1", "clip_0072.mp4", "s7", 0.5)
    second = slot("0073/s7b2", "clip_0073.mp4", "s7", 0.6)
    assert neighbours.same_segment_beats(first, second) is False


def test_same_segment_beats_false_for_different_segment_ids():
    first = slot("0072/s7b1", "clip_0072.mp4", "s7", 0.5)
    second = slot("0072/s8b1", "clip_0072.mp4", "s8", 0.6)
    assert neighbours.same_segment_beats(first, second) is False


def test_same_segment_beats_false_for_the_same_shot_key():
    first = slot("0072/s7b1", "clip_0072.mp4", "s7", 0.5)
    second = slot("0072/s7b1", "clip_0072.mp4", "s7", 0.6)
    assert neighbours.same_segment_beats(first, second) is False


def test_same_segment_beats_false_when_a_clip_is_missing():
    first = slot("0072/s7b1", None, "s7", 0.5)
    second = slot("0072/s7b2", "clip_0072.mp4", "s7", 0.6)
    assert neighbours.same_segment_beats(first, second) is False


def test_segment_id_of_reads_the_segment_dict_then_the_key():
    assert neighbours.segment_id_of({"segment": {"id": "s7"}}) == "s7"
    assert neighbours.segment_id_of({"segment_id": "s8"}) == "s8"
    assert neighbours.segment_id_of({"segment": {"id": "s7"}, "segment_id": "s8"}) == "s7"
    assert neighbours.segment_id_of({}) is None


def test_drop_pointless_neighbours_keeps_the_best_of_three_beats():
    slots = [
        slot("0072/s7b1", "clip_0072.mp4", "s7", 0.4),
        slot("0072/s7b2", "clip_0072.mp4", "s7", 0.9),
        slot("0072/s7b3", "clip_0072.mp4", "s7", 0.6),
    ]
    kept, dropped = neighbours.drop_pointless_neighbours(slots, lambda a, b: False)
    assert [row["shot_key"] for row in kept] == ["0072/s7b2"]
    assert {row["shot_key"] for row, _ in dropped} == {"0072/s7b1", "0072/s7b3"}
    assert all(reason.startswith("pointless jump cut: another beat of") for _, reason in dropped)


def test_drop_pointless_neighbours_keeps_the_better_of_a_similar_pair():
    slots = [
        slot("0001/s1", "clip_0001.mp4", "s1", 0.3),
        slot("0002/s1", "clip_0002.mp4", "s1", 0.8),
    ]
    similar = lambda a, b: True
    kept, dropped = neighbours.drop_pointless_neighbours(slots, similar)
    assert [row["shot_key"] for row in kept] == ["0002/s1"]
    assert dropped[0][0]["shot_key"] == "0001/s1"
    assert dropped[0][1].startswith("pointless jump cut: similar to")


def test_drop_pointless_neighbours_keeps_a_pair_the_owner_asked_for():
    slots = [
        slot("0072/s7b1", "clip_0072.mp4", "s7", 0.4),
        slot("0072/s7b2", "clip_0072.mp4", "s7", 0.9),
    ]
    kept, dropped = neighbours.drop_pointless_neighbours(
        slots, lambda a, b: False, keep_keys={"0072/s7b1", "0072/s7b2"}
    )
    assert [row["shot_key"] for row in kept] == ["0072/s7b1", "0072/s7b2"]
    assert dropped == []


def test_drop_pointless_neighbours_tie_keeps_the_earlier():
    slots = [
        slot("0072/s7b1", "clip_0072.mp4", "s7", 0.5),
        slot("0072/s7b2", "clip_0072.mp4", "s7", 0.5),
    ]
    kept, dropped = neighbours.drop_pointless_neighbours(slots, lambda a, b: False)
    assert [row["shot_key"] for row in kept] == ["0072/s7b1"]
    assert dropped[0][0]["shot_key"] == "0072/s7b2"
