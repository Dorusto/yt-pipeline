import json
import re
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

import index
from common.schema import validate
from common.exit_codes import EXIT_OK, EXIT_USAGE

ROOT = Path(__file__).resolve().parents[1]
FOURCC = cv2.VideoWriter_fourcc(*"mp4v")
CLIP_STEM = "dji_20261012_000000_{counter}_0000000000000_video"


def write_video(path, seconds=8, fps=10, size=(64, 48)):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), FOURCC, fps, size)
    if not writer.isOpened():
        pytest.skip("OpenCV mp4v VideoWriter is unavailable on this build")
    for index in range(seconds * fps):
        frame = np.full((size[1], size[0], 3), (index * 7) % 255, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return path


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


def write_quality(workspace, counter, segments, path=None):
    directory = workspace / "quality"
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "file": f"{CLIP_STEM.format(counter=counter)}.mp4",
        "duration_s": max(segment["end_s"] for segment in segments),
        "segments": segments,
    }
    if path is not None:
        record["path"] = str(path)
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


def write_photo(path, size=(64, 48), color=(120, 80, 40)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG")
    return path


def photo_record(path, creation_time="2026-10-12T11:42:00+00:00"):
    return {
        "file": path.name,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "width": 64,
        "height": 48,
        "orientation": None,
        "creation_time": creation_time,
        "creation_time_source": "exif",
        "format": "JPEG",
        "warnings": [],
    }


def write_photo_ingest(workspace, photos):
    data = {
        "generated_at": "2026-10-14T00:00:00+00:00",
        "source_dir": str(workspace),
        "clip_count": 0,
        "clips": [],
        "photo_count": len(photos),
        "photos": photos,
        "warnings": [],
    }
    (workspace / "ingest.json").write_text(json.dumps(data), encoding="utf-8")
    return data


def described_photo_segment(
    interest=60,
    summary="a described still",
    subject="lighthouse",
    subject_position=None,
    status="described",
):
    if status == "skipped":
        return {
            "id": "p1",
            "start_s": 0.0,
            "end_s": 0.04,
            "status": "skipped",
            "role": "hold",
            "motion_label": "static",
            "uncertain": False,
            "uncertain_reasons": [],
            "skip_reason": "skipped in test",
            "cost_usd": 0.0,
        }
    return {
        "id": "p1",
        "start_s": 0.0,
        "end_s": 0.04,
        "status": "described",
        "role": "hold",
        "motion_label": "static",
        "uncertain": False,
        "uncertain_reasons": [],
        "frames": [{"t_s": 0.0}],
        "description": "a still frame",
        "summary": summary,
        "tags": ["still"],
        "subject": subject,
        "scenery": "coast",
        "semantic_motion": "none",
        "interest": interest,
        "hero": False,
        "verdict": None,
        "skip_reason": None,
        "cost_usd": 0.0,
        "subject_position": subject_position or {"x": 0.5, "y": 0.5},
    }


def write_photo_describe(workspace, path, segment):
    directory = workspace / "describe"
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "media_type": "photo",
        "file": path.name,
        "path": str(path),
        "duration_s": 0.0,
        "model": "mock/vision",
        "llm_cost_usd": 0.0,
        "segments": [segment],
        "warnings": [],
    }
    (directory / f"{path.stem}.json").write_text(json.dumps(record), encoding="utf-8")
    return record


def read_shotlog(workspace):
    return json.loads((workspace / "shotlog.json").read_text(encoding="utf-8"))


def build(
    workspace,
    counter="0081",
    segments=None,
    describe_segments=None,
    motion_moves=None,
    similarity_pairs=None,
    clip_path=None,
):
    segments = segments if segments is not None else [quality_segment("s1", 0.0, 10.0)]
    write_quality(workspace, counter, segments, path=clip_path)
    if motion_moves is not None:
        write_motion(workspace, counter, motion_moves)
    if describe_segments is not None:
        write_describe(workspace, counter, describe_segments)
    if similarity_pairs is not None:
        write_similarity(workspace, similarity_pairs)
    summary, exit_code = index.run(str(workspace))
    assert exit_code == EXIT_OK
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


@pytest.mark.parametrize("role", ["setup", "teardown"])
def test_uncertain_ineligible_role_stays_eligible(tmp_path, role):
    segment = quality_segment("s1", 0.0, 10.0, role=role, confidence=0.6, uncertain=True)
    data, _ = build(tmp_path, segments=[segment])
    shot = data["shots"][0]
    assert shot["eligible"] is True
    assert shot["score"] > 0.0
    assert not shot["reason"].endswith("not eligible")


def test_uncertain_accidental_is_ineligible(tmp_path):
    segment = quality_segment("s1", 0.0, 10.0, role="accidental", confidence=0.6, uncertain=True)
    data, _ = build(tmp_path, segments=[segment])
    shot = data["shots"][0]
    assert shot["role"] == "accidental"
    assert shot["role_source"] == "geometry"
    assert shot["eligible"] is False
    assert shot["score"] == 0.0


def test_geometry_accidental_role_is_final(tmp_path):
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
        verdict={"role": "move", "motion_label": "pan_left", "confidence": 0.6, "note": "x"},
    )
    data, _ = build(tmp_path, segments=[segment], describe_segments=[describe])
    shot = data["shots"][0]
    assert shot["role"] == "accidental"
    assert shot["role_source"] == "geometry"
    assert shot["eligible"] is False
    assert shot["score"] == 0.0


def test_geometry_accidental_keeps_verdict_motion_label(tmp_path):
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
        verdict={"role": "move", "motion_label": "pan_left", "confidence": 0.6, "note": "x"},
    )
    data, _ = build(tmp_path, segments=[segment], describe_segments=[describe])
    shot = data["shots"][0]
    assert shot["motion_label"] == "pan_left"
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
    assert exit_code == EXIT_OK
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
    assert exit_code == EXIT_OK
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

    assert exit_code == EXIT_OK
    assert summary["skipped"] is True
    assert summary["shots"] == 1
    assert output.read_text(encoding="utf-8") == first_text
    assert output.stat().st_mtime == first_mtime


def test_force_recomputes(tmp_path):
    build(tmp_path, segments=[quality_segment("s1", 0.0, 10.0)])
    summary, exit_code = index.run(str(tmp_path), force=True)
    assert exit_code == EXIT_OK
    assert summary["skipped"] is False


def test_output_validates_against_schema(tmp_path):
    data, _ = build(tmp_path, segments=[quality_segment("s1", 0.0, 10.0)])
    assert validate(data, "shotlog") == []


def test_clips_filter_limits_the_run(tmp_path):
    write_quality(tmp_path, "0081", [quality_segment("s1", 0.0, 5.0)])
    write_quality(tmp_path, "0072", [quality_segment("s1", 0.0, 5.0)])
    summary, exit_code = index.run(str(tmp_path), clips="0072")
    assert exit_code == EXIT_OK
    assert summary["shots"] == 1
    data = read_shotlog(tmp_path)
    assert data["shots"][0]["key"] == "0072/s1"


def test_missing_quality_folder_exits_one(tmp_path, capsys):
    exit_code = index.main(["--workspace", str(tmp_path)])
    assert exit_code == EXIT_USAGE
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


def shot_record(key, score, clip="clip.mp4"):
    return {
        "key": key,
        "clip": clip,
        "start_s": 0.0,
        "end_s": 1.0,
        "eligible": True,
        "role": "hold",
        "motion_label": "static",
        "score": score,
        "scores": {"vision": score, "labels": score},
        "hook": key,
        "summary": None,
        "tags": [],
        "hero": False,
    }


def test_html_is_written_next_to_the_shotlog(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    _, summary = build(tmp_path, clip_path=clip)
    html_path = tmp_path / "shotlog.html"
    assert html_path.exists()
    assert Path(summary["html"]).resolve() == html_path.resolve()


def test_html_is_self_contained(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    build(tmp_path, clip_path=clip)
    text = (tmp_path / "shotlog.html").read_text(encoding="utf-8")
    assert "data:image/jpeg;base64," in text
    assert "http://" not in text
    assert "https://" not in text
    for src in re.findall(r'src="([^"]*)"', text):
        assert src.startswith("data:")


def test_cards_carry_summary_tags_hero_and_eligibility(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    segment = quality_segment("s1", 0.0, 10.0, role="accidental", confidence=0.9, uncertain=False)
    describe = described_segment(
        "s1",
        0.0,
        10.0,
        interest=80,
        hero=True,
        summary="a boat glides into a sea cave",
        tags=["boat", "sea cave"],
    )
    data, _ = build(tmp_path, segments=[segment], describe_segments=[describe], clip_path=clip)
    shot = data["shots"][0]
    assert shot["eligible"] is False
    text = (tmp_path / "shotlog.html").read_text(encoding="utf-8")
    assert "a boat glides into a sea cave" in text
    assert "boat" in text
    assert "sea cave" in text
    assert "HERO" in text
    assert "not eligible" in text
    assert f"{shot['score']:.2f}" in text


def test_cards_are_in_score_order(tmp_path):
    record = {
        "workspace": str(tmp_path),
        "generated_at": "2026-01-01T00:00:00+00:00",
        "shots": [shot_record("0081/s1", 0.4), shot_record("0081/s2", 0.9)],
    }
    html_path = tmp_path / "shotlog.html"
    index._write_html(html_path, record, {})
    text = html_path.read_text(encoding="utf-8")
    assert text.index("0081/s2") < text.index("0081/s1")


def test_summary_line_carries_pieces_clips_usable_shaky_moves_and_scenes():
    shots = [
        {"clip": "a.mp4", "eligible": True, "defects": ["shake"], "motion_label": "pan_right", "scenery": "coast"},
        {"clip": "a.mp4", "eligible": True, "defects": [], "motion_label": "pan_right", "scenery": "coast"},
        {"clip": "b.mp4", "eligible": False, "defects": [], "motion_label": "static", "scenery": "forest"},
    ]
    line = index._summary_line(shots)
    assert line == (
        "3 pieces from 2 clips · 2 usable · 1 shaky · "
        "moves: pan_right, static · scenes: coast, forest"
    )


def test_summary_line_omits_moves_and_scenes_when_there_are_none():
    shots = [{"clip": "a.mp4", "eligible": True, "defects": [], "motion_label": None, "scenery": None}]
    assert index._summary_line(shots) == "1 pieces from 1 clips · 1 usable · 0 shaky"


def test_html_opens_with_a_summary_line(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    build(tmp_path, clip_path=clip)
    text = (tmp_path / "shotlog.html").read_text(encoding="utf-8")
    assert "1 pieces from 1 clips" in text
    assert "1 usable" in text
    assert "0 shaky" in text


def test_html_has_three_collapsed_verdict_sections(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    build(tmp_path, clip_path=clip)
    text = (tmp_path / "shotlog.html").read_text(encoding="utf-8")
    for verdict in ("used", "kept", "unusable"):
        assert (
            f'<details class="shots-section" data-verdict="{verdict}" id="{verdict}"' in text
        )
    for tag in re.findall(r'<details class="shots-section"[^>]*>', text):
        assert " open" not in tag
    used_tag = next(tag for tag in re.findall(r'<details class="shots-section"[^>]*>', text) if 'id="used"' in tag)
    assert 'data-keys=""' in used_tag
    for tag in re.findall(r'<details class="shots-section"[^>]*>', text):
        if 'id="used"' not in tag:
            assert "data-keys" not in tag


def test_used_shot_lands_in_the_used_section(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    build(tmp_path, clip_path=clip)
    (tmp_path / "edit.json").write_text(
        json.dumps({"decisions": [{"id": "0081/s1", "status": "used", "slot": "v1"}]}),
        encoding="utf-8",
    )
    index._write_html(tmp_path / "shotlog.html", read_shotlog(tmp_path), {"clip.mp4": str(clip)})
    text = (tmp_path / "shotlog.html").read_text(encoding="utf-8")
    used = text[text.index('id="used"'):text.index('id="kept"')]
    kept = text[text.index('id="kept"'):text.index('id="unusable"')]
    assert "0081/s1" in used
    assert 'data-keys="0081/s1"' in used
    assert '<span class="count">1</span>' in used
    assert '<span class="count">0</span>' in kept


def test_without_an_edit_the_used_section_is_empty(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    build(tmp_path, clip_path=clip)
    text = (tmp_path / "shotlog.html").read_text(encoding="utf-8")
    used = text[text.index('id="used"'):text.index('id="kept"')]
    assert '<span class="count">0</span>' in used
    assert "None." in used


def test_shots_are_grouped_per_source_clip(tmp_path):
    record = {
        "workspace": str(tmp_path),
        "generated_at": "2026-01-01T00:00:00+00:00",
        "shots": [
            shot_record("0081/s1", 0.4, clip="a.mp4"),
            shot_record("0082/s1", 0.9, clip="b.mp4"),
        ],
    }
    index._write_html(tmp_path / "shotlog.html", record, {})
    text = (tmp_path / "shotlog.html").read_text(encoding="utf-8")
    assert '<section class="clip-group" data-clip="a.mp4">' in text
    assert '<section class="clip-group" data-clip="b.mp4">' in text
    assert "<h3>a.mp4</h3>" in text
    assert "<h3>b.mp4</h3>" in text


def test_missing_clip_file_does_not_fail(tmp_path):
    missing = tmp_path / "missing.mp4"
    data, summary = build(tmp_path, clip_path=missing)
    assert summary["html"] is not None
    text = (tmp_path / "shotlog.html").read_text(encoding="utf-8")
    assert data["shots"][0]["clip"] in text
    assert "placeholder" in text


def test_no_html_skips_the_contact_sheet(tmp_path):
    write_quality(tmp_path, "0081", [quality_segment("s1", 0.0, 10.0)])
    summary, exit_code = index.run(str(tmp_path), no_html=True)
    assert exit_code == EXIT_OK
    assert summary["html"] is None
    assert not (tmp_path / "shotlog.html").exists()


def test_html_inside_repo_is_refused(tmp_path, capsys):
    write_quality(tmp_path, "0081", [quality_segment("s1", 0.0, 10.0)])
    target = ROOT / "shotlog.html"
    exit_code = index.main(["--workspace", str(tmp_path), "--html", str(target), "--force"])
    assert exit_code == EXIT_USAGE
    assert "[error]" in capsys.readouterr().err
    assert not target.exists()


def test_json_summary_keeps_previous_keys_and_adds_html(tmp_path, capsys):
    write_quality(tmp_path, "0081", [quality_segment("s1", 0.0, 10.0)])
    exit_code = index.main(["--workspace", str(tmp_path), "--force", "--json"])
    assert exit_code == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert set(summary) == {
        "shots",
        "eligible",
        "with_vision",
        "suppressed",
        "weights",
        "wall_time_s",
        "llm_cost_usd",
        "output",
        "html",
        "skipped",
    }
    assert summary["llm_cost_usd"] == 0.0
    assert Path(summary["html"]).resolve() == (tmp_path / "shotlog.html").resolve()


def test_shotlog_still_validates_with_html(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    data, _ = build(tmp_path, clip_path=clip)
    assert validate(data, "shotlog") == []
    assert (tmp_path / "shotlog.html").exists()


def test_split_segment_yields_one_shot_per_beat(tmp_path):
    segment = quality_segment("s7", 20.0, 160.0, role="move", confidence=0.8, motion_label="travel_forward")
    described = described_segment(
        "s7",
        20.0,
        160.0,
        interest=70,
        hero=True,
        subject="tunnel",
        description="boat rides through the tunnel",
        summary="boat rides through the graffiti tunnel",
        tags=["boat", "tunnel"],
    )
    described["beats"] = [
        {"id": "b1", "start_s": 20.0, "end_s": 60.0, "label": "approach", "summary": "boat approaches the tunnel mouth", "interest": 82, "hero": True},
        {"id": "b2", "start_s": 60.0, "end_s": 120.0, "label": "inside", "summary": "inside the graffiti tunnel", "interest": 55, "hero": False},
        {"id": "b3", "start_s": 120.0, "end_s": 160.0, "label": "exit", "summary": "tunnel exit into the light", "interest": 90, "hero": True},
    ]
    data, _ = build(tmp_path, segments=[segment], describe_segments=[described])
    keys = [shot["key"] for shot in data["shots"]]
    assert keys == ["0081/s7b1", "0081/s7b2", "0081/s7b3"]
    first = data["shots"][0]
    assert first["segment_id"] == "s7b1"
    assert first["beat_of"] == "s7"
    assert first["beat_id"] == "b1"
    assert first["beat_label"] == "approach"
    assert first["start_s"] == 20.0
    assert first["end_s"] == 60.0
    assert first["summary"] == "boat approaches the tunnel mouth"
    assert first["interest"] == 82
    assert first["hero"] is True
    second = data["shots"][1]
    assert second["summary"] == "inside the graffiti tunnel"
    assert second["hero"] is False
    assert second["role"] == "move"
    assert second["motion_label"] == "travel_forward"
    assert all(shot["eligible"] for shot in data["shots"])
    assert validate(data, "shotlog") == []


def test_beat_vision_falls_back_to_parent(tmp_path):
    segment = quality_segment("s7", 20.0, 60.0, role="move", confidence=0.8, motion_label="travel_forward")
    described = described_segment(
        "s7",
        20.0,
        60.0,
        interest=70,
        hero=True,
        description="boat rides through the tunnel",
        summary="boat rides through the tunnel",
    )
    described["beats"] = [
        {"id": "b1", "start_s": 20.0, "end_s": 40.0, "label": "approach", "summary": None, "interest": None, "hero": None},
        {"id": "b2", "start_s": 40.0, "end_s": 60.0, "label": "exit", "summary": None, "interest": None, "hero": None},
    ]
    data, _ = build(tmp_path, segments=[segment], describe_segments=[described])
    first = data["shots"][0]
    assert first["summary"] == "boat rides through the tunnel"
    assert first["interest"] == 70
    assert first["hero"] is True
    assert first["description"] == "boat rides through the tunnel"


def test_unsplit_segment_stays_one_shot(tmp_path):
    segment = quality_segment("s7", 20.0, 60.0, role="move", confidence=0.8, motion_label="travel_forward")
    described = described_segment("s7", 20.0, 60.0, interest=70, hero=True)
    described["beats"] = [
        {"id": "b1", "start_s": 20.0, "end_s": 60.0, "label": "transit", "summary": "one moment", "interest": 70, "hero": True},
    ]
    data, _ = build(tmp_path, segments=[segment], describe_segments=[described])
    assert [shot["key"] for shot in data["shots"]] == ["0081/s7"]
    assert data["shots"][0]["beat_of"] is None


def test_beat_reason_carries_the_beat_label(tmp_path):
    segment = quality_segment("s7", 20.0, 60.0, role="move", confidence=0.8, motion_label="travel_forward")
    described = described_segment("s7", 20.0, 60.0, interest=70)
    described["beats"] = [
        {"id": "b1", "start_s": 20.0, "end_s": 40.0, "label": "approach", "summary": "approach", "interest": 70, "hero": False},
        {"id": "b2", "start_s": 40.0, "end_s": 60.0, "label": "exit", "summary": "exit", "interest": 70, "hero": False},
    ]
    data, _ = build(tmp_path, segments=[segment], describe_segments=[described])
    assert data["shots"][0]["reason"].startswith("approach · ")
    assert data["shots"][1]["reason"].startswith("exit · ")


def test_described_photo_becomes_one_shot(tmp_path):
    photo = write_photo(tmp_path / "dji_mimo_20261012_120102_0068_x_photo.jpg")
    write_photo_ingest(tmp_path, [photo_record(photo, "2026-10-12T11:42:00+00:00")])
    write_photo_describe(tmp_path, photo, described_photo_segment(interest=57))
    summary, exit_code = index.run(str(tmp_path), no_html=True)
    assert exit_code == EXIT_OK
    assert summary["shots"] == 1
    data = read_shotlog(tmp_path)
    shot = data["shots"][0]
    assert shot["key"] == "0068/p1"
    assert shot["media_type"] == "photo"
    assert shot["clip"] == photo.name
    assert shot["segment_id"] == "p1"
    assert shot["start_s"] == 0.0
    assert shot["end_s"] == 0.04
    assert shot["role"] == "hold"
    assert shot["role_source"] == "geometry"
    assert shot["motion_label"] == "static"
    assert shot["scores"]["labels"] == pytest.approx(0.7)
    assert shot["scores"]["vision"] == pytest.approx(0.57)
    assert shot["score"] == pytest.approx(0.55 * 0.57 + 0.45 * 0.7)
    assert shot["capture_time"] == "2026-10-12T11:42:00+00:00"
    assert shot["subject_position"] == {"x": 0.5, "y": 0.5}
    assert shot["eligible"] is True
    assert validate(data, "shotlog") == []


def test_photo_key_matches_similarity_counter_convention(tmp_path):
    first = write_photo(tmp_path / "dji_mimo_20261012_120102_0068_a_photo.jpg")
    second = write_photo(tmp_path / "dji_mimo_20261012_120103_0068_b_photo.jpg")
    write_photo_ingest(tmp_path, [photo_record(first), photo_record(second)])
    write_photo_describe(tmp_path, first, described_photo_segment())
    write_photo_describe(tmp_path, second, described_photo_segment())
    _, exit_code = index.run(str(tmp_path), no_html=True)
    assert exit_code == EXIT_OK
    keys = {shot["key"] for shot in read_shotlog(tmp_path)["shots"]}
    assert keys == {"0068/p1", f"{second.stem}/p1"}


def test_photo_burst_keeps_only_the_best_eligible(tmp_path):
    first = write_photo(tmp_path / "dji_mimo_20261012_120102_0068_x_photo.jpg", color=(10, 20, 30))
    second = write_photo(tmp_path / "dji_mimo_20261012_120103_0069_x_photo.jpg", color=(200, 180, 160))
    write_photo_ingest(
        tmp_path,
        [
            photo_record(first, "2026-10-12T11:42:00+00:00"),
            photo_record(second, "2026-10-12T11:42:03+00:00"),
        ],
    )
    write_photo_describe(tmp_path, first, described_photo_segment(interest=80))
    write_photo_describe(tmp_path, second, described_photo_segment(interest=40))
    write_similarity(tmp_path, [{"a": "0068/p1", "b": "0069/p1", "similarity": 0.95}])
    summary, exit_code = index.run(str(tmp_path), no_html=True)
    assert exit_code == EXIT_OK
    data = read_shotlog(tmp_path)
    winner = find(data, "0068/p1")
    loser = find(data, "0069/p1")
    assert winner["eligible"] is True
    assert winner["suppressed_by"] is None
    assert loser["eligible"] is False
    assert loser["suppressed_by"] == "0068/p1"
    assert loser["score"] == 0.0
    assert summary["suppressed"] == 1


def test_photos_120s_apart_are_not_a_burst(tmp_path):
    first = write_photo(tmp_path / "dji_mimo_20261012_120102_0068_x_photo.jpg", color=(10, 20, 30))
    second = write_photo(tmp_path / "dji_mimo_20261012_120103_0069_x_photo.jpg", color=(200, 180, 160))
    write_photo_ingest(
        tmp_path,
        [
            photo_record(first, "2026-10-12T11:42:00+00:00"),
            photo_record(second, "2026-10-12T11:44:00+00:00"),
        ],
    )
    write_photo_describe(tmp_path, first, described_photo_segment(interest=80))
    write_photo_describe(tmp_path, second, described_photo_segment(interest=40))
    write_similarity(tmp_path, [{"a": "0068/p1", "b": "0069/p1", "similarity": 0.95}])
    summary, exit_code = index.run(str(tmp_path), no_html=True)
    assert exit_code == EXIT_OK
    data = read_shotlog(tmp_path)
    assert find(data, "0068/p1")["eligible"] is True
    assert find(data, "0069/p1")["eligible"] is True
    assert find(data, "0068/p1")["suppressed_by"] is None
    assert find(data, "0069/p1")["suppressed_by"] is None
    assert summary["suppressed"] == 0


def test_photo_burst_chain_resolves_to_global_best(tmp_path):
    first = write_photo(tmp_path / "dji_mimo_20261012_120102_0068_x_photo.jpg", color=(10, 20, 30))
    middle = write_photo(tmp_path / "dji_mimo_20261012_120103_0069_x_photo.jpg", color=(60, 90, 120))
    last = write_photo(tmp_path / "dji_mimo_20261012_120104_0070_x_photo.jpg", color=(200, 180, 160))
    write_photo_ingest(
        tmp_path,
        [
            photo_record(first, "2026-10-12T11:42:00+00:00"),
            photo_record(middle, "2026-10-12T11:42:05+00:00"),
            photo_record(last, "2026-10-12T11:42:10+00:00"),
        ],
    )
    write_photo_describe(tmp_path, first, described_photo_segment(interest=50))
    write_photo_describe(tmp_path, middle, described_photo_segment(interest=60))
    write_photo_describe(tmp_path, last, described_photo_segment(interest=90))
    write_similarity(
        tmp_path,
        [
            {"a": "0068/p1", "b": "0069/p1", "similarity": 0.95},
            {"a": "0069/p1", "b": "0070/p1", "similarity": 0.95},
        ],
    )
    summary, exit_code = index.run(str(tmp_path), no_html=True)
    assert exit_code == EXIT_OK
    data = read_shotlog(tmp_path)
    assert find(data, "0070/p1")["eligible"] is True
    assert find(data, "0070/p1")["suppressed_by"] is None
    assert find(data, "0068/p1")["suppressed_by"] == "0070/p1"
    assert find(data, "0069/p1")["suppressed_by"] == "0070/p1"


def test_skipped_photo_still_yields_a_shot(tmp_path):
    photo = write_photo(tmp_path / "dji_mimo_20261012_120102_0068_x_photo.jpg")
    write_photo_ingest(tmp_path, [photo_record(photo)])
    write_photo_describe(tmp_path, photo, described_photo_segment(status="skipped"))
    summary, exit_code = index.run(str(tmp_path), no_html=True)
    assert exit_code == EXIT_OK
    data = read_shotlog(tmp_path)
    shot = data["shots"][0]
    assert shot["key"] == "0068/p1"
    assert shot["description"] is None
    assert shot["subject_position"] is None
    assert shot["capture_time"] is not None
    assert shot["scores"]["vision"] is None
    assert shot["scores"]["labels"] == pytest.approx(0.7)
    assert shot["score"] == pytest.approx(0.55 * 0.5 + 0.45 * 0.7)
    assert validate(data, "shotlog") == []


def test_html_contact_sheet_shows_photo_card(tmp_path):
    photo = write_photo(tmp_path / "dji_mimo_20261012_120102_0068_x_photo.jpg")
    write_photo_ingest(tmp_path, [photo_record(photo)])
    write_photo_describe(
        tmp_path,
        photo,
        described_photo_segment(interest=57, summary="a lighthouse at dusk"),
    )
    summary, exit_code = index.run(str(tmp_path))
    assert exit_code == EXIT_OK
    text = (tmp_path / "shotlog.html").read_text(encoding="utf-8")
    assert 'class="badge media">photo</span>' in text
    assert "data:image/jpeg;base64," in text
    assert "a lighthouse at dusk" in text
    assert validate(read_shotlog(tmp_path), "shotlog") == []


def test_vision_peak_reaches_the_shotlog(tmp_path):
    segment = quality_segment("s1", 0.0, 20.0, role="move", confidence=0.8, motion_label="pan_right")
    described = described_segment("s1", 0.0, 20.0, interest=70, role="move", motion_label="pan_right")
    described["peak_s"] = 12.4
    data, _ = build(tmp_path, segments=[segment], describe_segments=[described])
    assert data["shots"][0]["peak_s"] == 12.4
    assert validate(data, "shotlog") == []


def test_peak_outside_the_shot_or_missing_is_null_in_the_shotlog(tmp_path):
    segment = quality_segment("s1", 0.0, 20.0, role="move", confidence=0.8, motion_label="pan_right")
    described = described_segment("s1", 0.0, 20.0, interest=70, role="move", motion_label="pan_right")
    described["peak_s"] = 25.0
    data, _ = build(tmp_path, segments=[segment], describe_segments=[described])
    assert data["shots"][0]["peak_s"] is None
    undescribed, _ = build(tmp_path / "other", segments=[segment])
    assert undescribed["shots"][0]["peak_s"] is None


def test_beats_take_their_own_peak_or_the_segment_peak_when_it_falls_inside(tmp_path):
    segment = quality_segment("s7", 20.0, 160.0, role="move", confidence=0.8, motion_label="travel_forward")
    described = described_segment("s7", 20.0, 160.0, interest=70, summary="tunnel")
    described["peak_s"] = 90.0
    described["beats"] = [
        {"id": "b1", "start_s": 20.0, "end_s": 60.0, "label": "approach", "interest": 80, "hero": True, "peak_s": 41.0},
        {"id": "b2", "start_s": 60.0, "end_s": 120.0, "label": "inside", "interest": 55, "hero": False},
        {"id": "b3", "start_s": 120.0, "end_s": 160.0, "label": "exit", "interest": 90, "hero": True},
    ]
    data, _ = build(tmp_path, segments=[segment], describe_segments=[described])
    assert [shot["peak_s"] for shot in data["shots"]] == [41.0, 90.0, None]
    assert validate(data, "shotlog") == []


def test_photo_burst_prefers_open_eyes_over_a_higher_score(tmp_path):
    first = write_photo(tmp_path / "dji_mimo_20261012_120102_0068_x_photo.jpg", color=(10, 20, 30))
    second = write_photo(tmp_path / "dji_mimo_20261012_120103_0069_x_photo.jpg", color=(200, 180, 160))
    write_photo_ingest(tmp_path, [
        photo_record(first, "2026-10-12T11:42:00+00:00"),
        photo_record(second, "2026-10-12T11:42:03+00:00"),
    ])
    closed = described_photo_segment(interest=90)
    closed["photo_flaws"] = ["eyes_closed"]
    opened = described_photo_segment(interest=60)
    opened["photo_flaws"] = []
    write_photo_describe(tmp_path, first, closed)
    write_photo_describe(tmp_path, second, opened)
    write_similarity(tmp_path, [{"a": "0068/p1", "b": "0069/p1", "similarity": 0.95}])
    index.run(str(tmp_path), no_html=True)
    data = read_shotlog(tmp_path)
    assert find(data, "0069/p1")["eligible"] is True
    assert find(data, "0068/p1")["suppressed_by"] == "0069/p1"


def test_photo_burst_prefers_the_sharper_of_equal_photos(tmp_path):
    first = write_photo(tmp_path / "dji_mimo_20261012_120102_0068_x_photo.jpg", color=(10, 20, 30))
    second = write_photo(tmp_path / "dji_mimo_20261012_120103_0069_x_photo.jpg", color=(200, 180, 160))
    write_photo_ingest(tmp_path, [
        photo_record(first, "2026-10-12T11:42:00+00:00"),
        photo_record(second, "2026-10-12T11:42:03+00:00"),
    ])
    soft = described_photo_segment(interest=62)
    soft["sharpness"] = 20.0
    crisp = described_photo_segment(interest=60)
    crisp["sharpness"] = 200.0
    write_photo_describe(tmp_path, first, soft)
    write_photo_describe(tmp_path, second, crisp)
    write_similarity(tmp_path, [{"a": "0068/p1", "b": "0069/p1", "similarity": 0.95}])
    index.run(str(tmp_path), no_html=True)
    data = read_shotlog(tmp_path)
    assert find(data, "0068/p1")["suppressed_by"] == "0069/p1"


def test_a_lone_photo_with_closed_eyes_is_not_eligible(tmp_path):
    only = write_photo(tmp_path / "dji_mimo_20261012_120102_0068_x_photo.jpg", color=(10, 20, 30))
    write_photo_ingest(tmp_path, [photo_record(only, "2026-10-12T11:42:00+00:00")])
    closed = described_photo_segment(interest=90)
    closed["photo_flaws"] = ["eyes_closed"]
    write_photo_describe(tmp_path, only, closed)
    write_similarity(tmp_path, [])
    index.run(str(tmp_path), no_html=True)
    shot = find(read_shotlog(tmp_path), "0068/p1")
    assert shot["eligible"] is False
    assert shot["defects"] == ["eyes_closed"]


def test_shotlog_page_shares_the_theme_tokens():
    page = index._html_document("w", "summary", "now", [])
    for token in ("--water:#151619", "--sea:#4fb0bd", "--tap:44px",
                  "--font-display:", "prefers-color-scheme:light"):
        assert token in page
