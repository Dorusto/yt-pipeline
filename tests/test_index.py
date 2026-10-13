import json
from pathlib import Path

import pytest

import index
from common.schema import validate

CLIP_STEM = "dji_20261012_000000_{counter}_0000000000000_video"


def quality_segment(
    id,
    start_s,
    end_s,
    role="hold",
    confidence=0.7,
    motion_label="static",
    defects=None,
    uncertain=False,
    uncertain_reasons=None,
):
    return {
        "id": id,
        "start_s": start_s,
        "end_s": end_s,
        "role": role,
        "confidence": confidence,
        "motion_label": motion_label,
        "motion_label_confidence": 0.8,
        "uncertain": uncertain,
        "uncertain_reasons": list(uncertain_reasons or []),
        "defects": list(defects or []),
    }


def described_segment(
    id,
    start_s,
    end_s,
    interest=60,
    hero=False,
    subject=None,
    description=None,
    summary=None,
    tags=None,
    verdict=None,
    role="hold",
    motion_label="static",
    uncertain=False,
):
    return {
        "id": id,
        "start_s": start_s,
        "end_s": end_s,
        "status": "described",
        "role": role,
        "motion_label": motion_label,
        "uncertain": uncertain,
        "uncertain_reasons": [],
        "description": description,
        "summary": summary,
        "tags": tags or [],
        "subject": subject,
        "scenery": None,
        "semantic_motion": None,
        "interest": interest,
        "hero": hero,
        "verdict": verdict,
    }


def write_quality(workspace, counter, segments):
    directory = workspace / "quality"
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "file": f"{CLIP_STEM.format(counter=counter)}.mp4",
        "duration_s": max(segment["end_s"] for segment in segments),
        "segments": segments,
    }
    (directory / f"{CLIP_STEM.format(counter=counter)}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    return record


def write_motion(workspace, counter, moves):
    directory = workspace / "motion"
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "file": f"{CLIP_STEM.format(counter=counter)}.mp4",
        "duration_s": max(move["end_s"] for move in moves),
        "moves": moves,
    }
    (directory / f"{CLIP_STEM.format(counter=counter)}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    return record


def write_describe(workspace, counter, segments):
    directory = workspace / "describe"
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "file": f"{CLIP_STEM.format(counter=counter)}.mp4",
        "duration_s": max(segment["end_s"] for segment in segments),
        "model": "mock/vision",
        "segments": segments,
    }
    (directory / f"{CLIP_STEM.format(counter=counter)}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    return record


def write_similarity(workspace, pairs):
    (workspace / "similarity.json").write_text(json.dumps({"pairs": pairs}), encoding="utf-8")


def read_shotlog(workspace):
    return json.loads((workspace / "shotlog.json").read_text(encoding="utf-8"))


def build(
    workspace,
    counter="0081",
    segments=None,
    describe_segments=None,
    motion_moves=None,
    similarity_pairs=None,
):
    segments = segments if segments is not None else [quality_segment("s1", 0.0, 10.0)]
    write_quality(workspace, counter, segments)
    if motion_moves is not None:
        write_motion(workspace, counter, motion_moves)
    if describe_segments is not None:
        write_describe(workspace, counter, describe_segments)
    if similarity_pairs is not None:
        write_similarity(workspace, similarity_pairs)
    summary, exit_code = index.run(str(workspace))
    assert exit_code == 0
    return read_shotlog(workspace), summary


def find(data, key):
    return next(shot for shot in data["shots"] if shot["key"] == key)


def test_verdict_wins_for_described_uncertain(tmp_path):
    segment = quality_segment(
        "s1", 0.0, 10.0, role="move", confidence=0.4, motion_label="complex_motion", uncertain=True
    )
    describe = described_segment(
        "s1",
        0.0,
        10.0,
        interest=58,
        uncertain=True,
        verdict={"role": "hold", "motion_label": "minor_motion", "confidence": 0.6, "note": "x"},
    )
    data, _ = build(tmp_path, segments=[segment], describe_segments=[describe])
    shot = data["shots"][0]
    assert shot["role"] == "hold"
    assert shot["role_source"] == "verdict"
    assert shot["motion_label"] == "minor_motion"
    assert shot["label_source"] == "verdict"
    assert shot["confidence"] == pytest.approx(0.6)


def test_geometry_kept_when_no_verdict(tmp_path):
    segment = quality_segment(
        "s1", 0.0, 10.0, role="move", confidence=0.55, motion_label="pan_right", uncertain=True
    )
    describe = described_segment(
        "s1", 0.0, 10.0, interest=40, uncertain=True, role="move", motion_label="pan_right", verdict=None
    )
    data, _ = build(tmp_path, segments=[segment], describe_segments=[describe])
    shot = data["shots"][0]
    assert shot["role"] == "move"
    assert shot["role_source"] == "geometry"
    assert shot["motion_label"] == "pan_right"
    assert shot["label_source"] == "geometry"
    assert shot["confidence"] == pytest.approx(0.55)


def test_score_matches_formula_with_vision(tmp_path):
    segment = quality_segment("s1", 0.0, 10.0, role="hold", confidence=0.7)
    describe = described_segment("s1", 0.0, 10.0, interest=60, hero=False)
    data, _ = build(tmp_path, segments=[segment], describe_segments=[describe])
    shot = data["shots"][0]

    expected_labels = index.clamp(
        index.ROLE_BASE["hold"]
        + 0.25 * 0.7
        + index.STEADINESS_BONUS["static"]
        + 0.10,
        0.0,
        1.0,
    )
    expected_vision = min(1.0, 60 / 100)
    expected_score = (index.DEFAULT_WEIGHT_VISION * expected_vision + index.DEFAULT_WEIGHT_LABELS * expected_labels) / (
        index.DEFAULT_WEIGHT_VISION + index.DEFAULT_WEIGHT_LABELS
    )
    assert shot["scores"]["labels"] == pytest.approx(expected_labels)
    assert shot["scores"]["vision"] == pytest.approx(expected_vision)
    assert shot["score"] == pytest.approx(expected_score)
    assert expected_labels == pytest.approx(0.925)
    assert expected_score == pytest.approx(0.74625)


def test_score_matches_formula_without_vision(tmp_path):
    segment = quality_segment(
        "s1", 0.0, 10.0, role="move", confidence=0.5, motion_label="pan_right"
    )
    moves = [{"id": "m1", "start_s": 0.0, "end_s": 10.0, "label": "pan_right", "steadiness": "no_shaking"}]
    data, _ = build(tmp_path, segments=[segment], motion_moves=moves)
    shot = data["shots"][0]

    expected_labels = index.clamp(
        index.ROLE_BASE["move"]
        + 0.25 * 0.5
        + index.STEADINESS_BONUS["no_shaking"]
        + 0.10,
        0.0,
        1.0,
    )
    expected_score = (
        index.DEFAULT_WEIGHT_VISION * index.NO_VISION_PRIOR
        + index.DEFAULT_WEIGHT_LABELS * expected_labels
    ) / (index.DEFAULT_WEIGHT_VISION + index.DEFAULT_WEIGHT_LABELS)
    assert shot["scores"]["vision"] is None
    assert shot["scores"]["labels"] == pytest.approx(expected_labels)
    assert shot["score"] == pytest.approx(expected_score)
    assert expected_labels == pytest.approx(0.875)
    assert expected_score == pytest.approx(0.66875)


def test_missing_vision_uses_neutral_prior(tmp_path):
    segment = quality_segment("s1", 0.0, 10.0, role="hold", confidence=0.7)
    data, _ = build(tmp_path, segments=[segment])
    shot = data["shots"][0]
    assert shot["scores"]["vision"] is None
    assert shot["scores"]["labels"] == pytest.approx(0.925)
    assert shot["score"] == pytest.approx(0.55 * index.NO_VISION_PRIOR + 0.45 * 0.925)
    assert shot["score"] == pytest.approx(0.69125)


def test_hero_shot_gets_vision_boost(tmp_path):
    segment = quality_segment("s1", 0.0, 10.0, role="hold", confidence=0.7)
    describe = described_segment("s1", 0.0, 10.0, interest=50, hero=True)
    data, _ = build(tmp_path, segments=[segment], describe_segments=[describe])
    shot = data["shots"][0]
    assert shot["scores"]["vision"] == pytest.approx(0.65)
    assert shot["scores"]["vision"] == pytest.approx(min(1.0, 50 / 100 + 0.15))


def test_hero_vision_is_capped_at_one(tmp_path):
    segment = quality_segment("s1", 0.0, 10.0, role="hold", confidence=0.7)
    describe = described_segment("s1", 0.0, 10.0, interest=95, hero=True)
    data, _ = build(tmp_path, segments=[segment], describe_segments=[describe])
    assert data["shots"][0]["scores"]["vision"] == pytest.approx(1.0)


@pytest.mark.parametrize("role", ["accidental", "setup", "teardown"])
def test_confident_ineligible_role_is_zeroed(tmp_path, role):
    segment = quality_segment("s1", 0.0, 10.0, role=role, confidence=0.9, uncertain=False)
    data, _ = build(tmp_path, segments=[segment])
    shot = data["shots"][0]
    assert shot["eligible"] is False
    assert shot["score"] == 0.0
    assert shot["scores"]["labels"] == 0.0
    assert shot["scores"]["vision"] is None
    assert shot["reason"].endswith("not eligible")


@pytest.mark.parametrize("role", ["accidental", "setup", "teardown"])
def test_uncertain_ineligible_role_stays_eligible(tmp_path, role):
    segment = quality_segment("s1", 0.0, 10.0, role=role, confidence=0.6, uncertain=True)
    data, _ = build(tmp_path, segments=[segment])
    shot = data["shots"][0]
    assert shot["eligible"] is True
    assert shot["score"] > 0.0
    assert not shot["reason"].endswith("not eligible")


def test_verdict_can_make_uncertain_accidental_eligible(tmp_path):
    segment = quality_segment(
        "s1", 0.0, 10.0, role="accidental", confidence=0.65, motion_label="complex_motion", uncertain=True
    )
    describe = described_segment(
        "s1",
        0.0,
        10.0,
        interest=68,
        uncertain=True,
        role="accidental",
        motion_label="complex_motion",
        verdict={"role": "accidental", "motion_label": "complex_motion", "confidence": 0.5, "note": "x"},
    )
    data, _ = build(tmp_path, segments=[segment], describe_segments=[describe])
    shot = data["shots"][0]
    assert shot["eligible"] is True
    assert shot["role_source"] == "verdict"
    assert shot["label_source"] == "verdict"


def test_hook_and_reason_match_exact_formats(tmp_path):
    segment = quality_segment(
        "s1",
        0.0,
        35.36,
        role="move",
        confidence=0.8,
        motion_label="complex_motion",
        defects=[{"name": "obstruction", "value": 0.26}],
        uncertain=True,
    )
    describe = described_segment(
        "s1",
        0.0,
        35.36,
        interest=60,
        subject="the boat's wake receding toward a mountain village",
        description="Stern-facing view from a moving boat inside a strait.",
        uncertain=True,
        role="move",
        motion_label="complex_motion",
        verdict={"role": "move", "motion_label": "travel_backward", "confidence": 0.8, "note": "x"},
    )
    data, _ = build(tmp_path, segments=[segment], describe_segments=[describe])
    shot = data["shots"][0]
    assert shot["hook"] == "the boat's wake receding toward a mountain village"
    assert shot["reason"] == "interest 60 · travel_backward by verdict · move · obstruction"


def test_hook_and_reason_without_description(tmp_path):
    segment = quality_segment("s1", 0.0, 1.12, role="accidental", confidence=0.65)
    data, _ = build(tmp_path, segments=[segment])
    shot = data["shots"][0]
    assert shot["hook"] == "accidental · static"
    assert shot["reason"] == "no description · static by geometry · accidental · clean · not eligible"


def test_hook_falls_back_to_description_words_and_hero(tmp_path):
    segment = quality_segment("s1", 0.0, 10.0, role="hold", confidence=0.7)
    describe = described_segment(
        "s1",
        0.0,
        10.0,
        interest=50,
        hero=True,
        subject="none",
        description="alpha beta gamma delta epsilon zeta eta theta iota kappa",
    )
    data, _ = build(tmp_path, segments=[segment], describe_segments=[describe])
    shot = data["shots"][0]
    assert shot["hook"] == "alpha beta gamma delta epsilon zeta eta theta"
    assert shot["reason"] == "interest 50 · hero · static by geometry · hold · clean"


def test_similar_to_is_filled_most_similar_first(tmp_path):
    write_quality(
        tmp_path,
        "0081",
        [quality_segment("s1", 0.0, 5.0), quality_segment("s2", 5.0, 10.0)],
    )
    write_quality(tmp_path, "0072", [quality_segment("s1", 0.0, 5.0)])
    write_similarity(
        tmp_path,
        [
            {"a": "0081/s1", "b": "0072/s1", "similarity": 0.97},
            {"a": "0081/s1", "b": "0081/s2", "similarity": 0.91},
        ],
    )
    summary, exit_code = index.run(str(tmp_path))
    assert exit_code == 0
    data = read_shotlog(tmp_path)
    assert find(data, "0081/s1")["similar_to"] == ["0072/s1", "0081/s2"]
    assert find(data, "0072/s1")["similar_to"] == ["0081/s1"]
    assert find(data, "0081/s2")["similar_to"] == ["0081/s1"]


def test_overlap_rule_suppresses_lower_scored_shot(tmp_path):
    segments = [
        quality_segment("s1", 0.0, 10.0, role="move", confidence=0.7, motion_label="pan_right"),
        quality_segment("s2", 5.0, 13.0, role="hold", confidence=0.7),
    ]
    data, _ = build(tmp_path, segments=segments)
    first = find(data, "0081/s1")
    second = find(data, "0081/s2")
    assert second["score"] > first["score"]
    assert first["suppressed_by"] == "0081/s2"
    assert first["eligible"] is False
    assert first["score"] == 0.0
    assert first["scores"]["labels"] == 0.0
    assert first["scores"]["vision"] is None
    assert second["suppressed_by"] is None
    assert second["eligible"] is True


def test_no_suppression_below_half_overlap(tmp_path):
    segments = [
        quality_segment("s1", 0.0, 10.0, role="move", confidence=0.7, motion_label="pan_right"),
        quality_segment("s2", 6.0, 14.0, role="hold", confidence=0.7),
    ]
    data, _ = build(tmp_path, segments=segments)
    assert find(data, "0081/s1")["suppressed_by"] is None
    assert find(data, "0081/s1")["eligible"] is True


def test_json_summary_carries_wall_time_and_zero_cost(tmp_path, capsys):
    write_quality(tmp_path, "0081", [quality_segment("s1", 0.0, 10.0)])
    exit_code = index.main(["--workspace", str(tmp_path), "--force", "--json"])
    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)
    assert "wall_time_s" in summary
    assert isinstance(summary["wall_time_s"], float)
    assert summary["llm_cost_usd"] == 0.0
    assert summary["weights"] == {
        "vision": 0.55,
        "labels": 0.45,
        "audio_energy": 0.0,
        "text": 0.0,
    }
    assert summary["shots"] == 1
    assert summary["skipped"] is False


def test_rerun_without_force_is_a_noop(tmp_path):
    build(tmp_path, segments=[quality_segment("s1", 0.0, 10.0)])
    output = tmp_path / "shotlog.json"
    first_text = output.read_text(encoding="utf-8")
    first_mtime = output.stat().st_mtime

    summary, exit_code = index.run(str(tmp_path))

    assert exit_code == 0
    assert summary["skipped"] is True
    assert summary["shots"] == 1
    assert output.read_text(encoding="utf-8") == first_text
    assert output.stat().st_mtime == first_mtime


def test_force_recomputes(tmp_path):
    build(tmp_path, segments=[quality_segment("s1", 0.0, 10.0)])
    summary, exit_code = index.run(str(tmp_path), force=True)
    assert exit_code == 0
    assert summary["skipped"] is False


def test_output_validates_against_schema(tmp_path):
    data, _ = build(tmp_path, segments=[quality_segment("s1", 0.0, 10.0)])
    assert validate(data, "shotlog") == []


def test_clips_filter_limits_the_run(tmp_path):
    write_quality(tmp_path, "0081", [quality_segment("s1", 0.0, 5.0)])
    write_quality(tmp_path, "0072", [quality_segment("s1", 0.0, 5.0)])
    summary, exit_code = index.run(str(tmp_path), clips="0072")
    assert exit_code == 0
    assert summary["shots"] == 1
    data = read_shotlog(tmp_path)
    assert data["shots"][0]["key"] == "0072/s1"


def test_missing_quality_folder_exits_one(tmp_path, capsys):
    exit_code = index.main(["--workspace", str(tmp_path)])
    assert exit_code == 1
    assert "[error]" in capsys.readouterr().err


def test_shot_carries_summary_and_tags(tmp_path):
    segment = quality_segment("s1", 0.0, 10.0, role="hold", confidence=0.7)
    describe = described_segment(
        "s1",
        0.0,
        10.0,
        interest=60,
        summary="boat glides toward the dark mouth of a sea cave",
        tags=["boat", "sea cave", "approach"],
    )
    data, _ = build(tmp_path, segments=[segment], describe_segments=[describe])
    shot = data["shots"][0]
    assert shot["summary"] == "boat glides toward the dark mouth of a sea cave"
    assert shot["tags"] == ["boat", "sea cave", "approach"]


def test_shot_without_vision_has_null_summary_and_empty_tags(tmp_path):
    segment = quality_segment("s1", 0.0, 10.0, role="hold", confidence=0.7)
    data, _ = build(tmp_path, segments=[segment], describe_segments=None)
    shot = data["shots"][0]
    assert shot["summary"] is None
    assert shot["tags"] == []
    assert validate(data, "shotlog") == []
