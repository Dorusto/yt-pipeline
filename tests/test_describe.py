import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

import describe
from common.llm import BudgetExceeded
from common.schema import validate
from common.vision import MockVisionModel, VisionResponse

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "describe.py"
FIXTURES = ROOT / "tests" / "fixtures"
FOURCC = cv2.VideoWriter_fourcc(*"mp4v")
CONFIG = {
    "models": {"vision": "mock/vision"},
    "llm_budget_usd": 5.0,
    "llm_backend": "mock",
}


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


def segment(
    id,
    start_s,
    end_s,
    role,
    motion_label="static",
    uncertain=False,
    reasons=None,
    confidence=0.7,
    label_confidence=0.8,
):
    return {
        "id": id,
        "start_s": start_s,
        "end_s": end_s,
        "role": role,
        "confidence": confidence,
        "motion_label": motion_label,
        "motion_label_confidence": label_confidence,
        "uncertain": uncertain,
        "uncertain_reasons": reasons or [],
    }


def write_quality(workspace, stem, clip_path, segments, duration_s=8.0):
    quality_dir = workspace / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "file": clip_path.name,
        "path": str(clip_path),
        "duration_s": duration_s,
        "segments": segments,
    }
    (quality_dir / f"{stem}.json").write_text(json.dumps(record), encoding="utf-8")
    return record


def read_describe(workspace, stem):
    return json.loads((workspace / "describe" / f"{stem}.json").read_text(encoding="utf-8"))


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


def write_ingest(workspace, photos, clips=None):
    data = {
        "generated_at": "2026-10-14T00:00:00+00:00",
        "source_dir": str(workspace),
        "clip_count": len(clips or []),
        "clips": clips or [],
        "photo_count": len(photos),
        "photos": photos,
        "warnings": [],
    }
    (workspace / "ingest.json").write_text(json.dumps(data), encoding="utf-8")
    return data


class BrokenModel:
    """Returns text that is not JSON at all."""

    def __init__(self):
        self.calls = []

    def describe(self, request):
        self.calls.append(request)
        return VisionResponse(
            text="I cannot answer that.",
            model="broken",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
        )


class MixedBrokenModel:
    """Returns valid JSON once, then unparseable text, charging per call."""

    def __init__(self, cost_per_call=0.002):
        self.calls = []
        self.cost_per_call = cost_per_call

    def describe(self, request):
        self.calls.append(request)
        if len(self.calls) == 1:
            text = json.dumps(
                {
                    "description": "a shot",
                    "subject": "none",
                    "scenery": "outside",
                    "semantic_motion": "none",
                    "interest": 40,
                    "hero": False,
                    "verdict": None,
                }
            )
        else:
            text = "I cannot answer that."
        return VisionResponse(
            text=text,
            model="mixed",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=self.cost_per_call,
        )


class NoneTextModel:
    """Returns a response with no text, as a reasoning model can."""

    def __init__(self):
        self.calls = []

    def describe(self, request):
        self.calls.append(request)
        return VisionResponse(
            text=None,
            model="none-text",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
        )


class FlakyParseModel:
    """Returns unparseable text once, then valid JSON, charging per call."""

    def __init__(self, cost_per_call=0.003):
        self.calls = []
        self.cost_per_call = cost_per_call

    def describe(self, request):
        self.calls.append(request)
        if len(self.calls) == 1:
            text = "I cannot answer that."
        else:
            text = json.dumps(
                {
                    "description": "a shot",
                    "subject": "none",
                    "scenery": "outside",
                    "semantic_motion": "none",
                    "interest": 40,
                    "hero": False,
                    "verdict": None,
                }
            )
        return VisionResponse(
            text=text,
            model="flaky",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=self.cost_per_call,
        )


class NoVerdictThenNoneModel:
    """Returns valid JSON without a verdict, then no text on the retry."""

    def __init__(self):
        self.calls = []

    def describe(self, request):
        self.calls.append(request)
        if len(self.calls) > 1:
            return VisionResponse(
                text=None,
                model="no-verdict-none",
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0,
            )
        text = json.dumps(
            {
                "description": "a shot",
                "subject": "none",
                "scenery": "outside",
                "semantic_motion": "none",
                "interest": 40,
                "hero": False,
                "verdict": None,
            }
        )
        return VisionResponse(
            text=text,
            model="no-verdict-none",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
        )


class NoVerdictModel:
    """Returns valid JSON but never a verdict, even for uncertain segments."""

    def __init__(self):
        self.calls = []

    def describe(self, request):
        self.calls.append(request)
        text = json.dumps(
            {
                "description": "a shot",
                "subject": "none",
                "scenery": "outside",
                "semantic_motion": "none",
                "interest": 40,
                "hero": False,
                "verdict": None,
            }
        )
        return VisionResponse(
            text=text,
            model="no-verdict",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
        )


class BudgetModel:
    """Describes the first segment, then refuses with BudgetExceeded."""

    def __init__(self):
        self.calls = []

    def describe(self, request):
        if self.calls:
            raise BudgetExceeded("budget reached")
        self.calls.append(request)
        return VisionResponse(
            text=json.dumps(
                {
                    "description": "a shot",
                    "subject": "none",
                    "scenery": "outside",
                    "semantic_motion": "none",
                    "interest": 40,
                    "hero": False,
                    "verdict": None,
                }
            ),
            model="budget",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
        )


def test_confident_skip_roles_make_no_call(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [
            segment("s1", 0.0, 2.0, "setup"),
            segment("s2", 2.0, 4.0, "teardown"),
            segment("s3", 4.0, 6.0, "accidental"),
        ],
    )
    model = MockVisionModel()
    summary, code = describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG)
    assert code == 0
    assert model.calls == []
    data = read_describe(tmp_path, "clip")
    assert [s["status"] for s in data["segments"]] == ["skipped", "skipped", "skipped"]
    assert [s["skip_reason"] for s in data["segments"]] == [
        "confident setup",
        "confident teardown",
        "confident accidental",
    ]
    assert all(s["cost_usd"] is None for s in data["segments"])
    assert summary["segments_described"] == 0
    assert summary["segments_skipped"] == 3


def test_uncertain_segment_is_described_with_verdict(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [
            segment(
                "s1",
                0.0,
                4.0,
                "move",
                motion_label="complex_motion",
                uncertain=True,
                reasons=["mixed_motion_signals"],
            )
        ],
    )
    model = MockVisionModel()
    summary, code = describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG)
    assert code == 0
    assert len(model.calls) == 1
    data = read_describe(tmp_path, "clip")
    seg = data["segments"][0]
    assert seg["status"] == "described"
    assert seg["verdict"] is not None
    assert seg["verdict"]["motion_label"] == "complex_motion"
    assert validate(data, "describe") == []


def test_confident_segment_is_described_with_null_verdict(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [segment("s1", 0.0, 4.0, "hold", motion_label="static", uncertain=False)],
    )
    model = MockVisionModel()
    describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG)
    data = read_describe(tmp_path, "clip")
    seg = data["segments"][0]
    assert seg["status"] == "described"
    assert seg["verdict"] is None
    assert validate(data, "describe") == []


def test_frame_timestamps_match_formula_and_order(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [segment("s1", 1.0, 7.0, "hold", motion_label="static")],
    )
    model = MockVisionModel()
    describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG)
    data = read_describe(tmp_path, "clip")
    times = [frame["t_s"] for frame in data["segments"][0]["frames"]]
    expected = [1.0 + (index + 0.5) / 3 * 6.0 for index in range(3)]
    assert times == pytest.approx(expected)
    assert times == sorted(times)
    assert len(model.calls[0].frames) == 3
    assert [frame.t_s for frame in model.calls[0].frames] == pytest.approx(expected)


def test_prompt_contains_segment_facts(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [
            segment(
                "s1",
                1.0,
                7.0,
                "move",
                motion_label="complex_motion",
                uncertain=True,
                reasons=["mixed_motion_signals", "motion_uncertain"],
            )
        ],
    )
    model = MockVisionModel()
    describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG)
    prompt = model.calls[0].prompt
    assert "Segment facts:" in prompt
    assert "complex_motion" in prompt
    assert "uncertain: yes (mixed_motion_signals, motion_uncertain)" in prompt
    for t_s in (2.0, 4.0, 6.0):
        assert f"{t_s} s" in prompt


def test_output_validates_against_schema(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [
            segment("s1", 0.0, 3.0, "move", motion_label="pan_right", uncertain=True, reasons=["parallax_vs_rotation"]),
            segment("s2", 3.0, 5.0, "setup"),
        ],
    )
    model = MockVisionModel()
    describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG)
    data = read_describe(tmp_path, "clip")
    assert validate(data, "describe") == []


def test_fixture_shapes_validate():
    data = json.loads((FIXTURES / "describe_valid.json").read_text(encoding="utf-8"))
    assert validate(data, "describe") == []


def test_unparseable_output_is_skipped_with_warning(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [segment("s1", 0.0, 4.0, "hold", motion_label="static")],
    )
    model = BrokenModel()
    summary, code = describe.run(tmp_path, model=model, model_id="broken", config=CONFIG)
    assert code == 0
    assert len(model.calls) == 2
    data = read_describe(tmp_path, "clip")
    seg = data["segments"][0]
    assert seg["status"] == "skipped"
    assert seg["skip_reason"] == "unparseable model output"
    assert data["warnings"]
    assert validate(data, "describe") == []


def test_unparseable_output_retries_once_then_describes(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [segment("s1", 0.0, 4.0, "hold", motion_label="static")],
    )
    model = FlakyParseModel(cost_per_call=0.003)
    summary, code = describe.run(tmp_path, model=model, model_id="flaky", config=CONFIG)
    assert code == 0
    assert len(model.calls) == 2
    data = read_describe(tmp_path, "clip")
    seg = data["segments"][0]
    assert seg["status"] == "described"
    assert seg["cost_usd"] == pytest.approx(0.006)
    assert not any("unparseable" in warning for warning in data["warnings"])
    assert data["llm_cost_usd"] == pytest.approx(0.006)
    assert summary["llm_cost_usd"] == pytest.approx(0.006)
    assert validate(data, "describe") == []


def test_unparseable_skip_carries_call_cost(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [
            segment("s1", 0.0, 2.0, "hold", motion_label="static"),
            segment("s2", 2.0, 4.0, "hold", motion_label="static"),
        ],
    )
    model = MixedBrokenModel(cost_per_call=0.002)
    summary, code = describe.run(tmp_path, model=model, model_id="mixed", config=CONFIG)
    assert code == 0
    data = read_describe(tmp_path, "clip")
    assert data["segments"][0]["status"] == "described"
    assert data["segments"][1]["status"] == "skipped"
    assert data["segments"][1]["skip_reason"] == "unparseable model output"
    assert data["segments"][1]["cost_usd"] == pytest.approx(0.004)
    assert data["llm_cost_usd"] == pytest.approx(
        sum(seg["cost_usd"] or 0.0 for seg in data["segments"])
    )
    assert data["llm_cost_usd"] == pytest.approx(0.006)
    assert summary["llm_cost_usd"] == pytest.approx(0.006)
    assert validate(data, "describe") == []


def test_none_text_is_skipped_with_warning(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [segment("s1", 0.0, 4.0, "hold", motion_label="static")],
    )
    model = NoneTextModel()
    summary, code = describe.run(tmp_path, model=model, model_id="none-text", config=CONFIG)
    assert code == 0
    assert len(model.calls) == 2
    data = read_describe(tmp_path, "clip")
    seg = data["segments"][0]
    assert seg["status"] == "skipped"
    assert seg["skip_reason"] == "unparseable model output"
    assert data["warnings"]
    assert validate(data, "describe") == []


def test_parse_answer_handles_none_and_empty():
    assert describe.parse_answer(None) is None
    assert describe.parse_answer("") is None


def test_missing_verdict_retries_then_falls_back(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [
            segment(
                "s1",
                0.0,
                4.0,
                "move",
                motion_label="complex_motion",
                uncertain=True,
                reasons=["mixed_motion_signals"],
            )
        ],
    )
    model = NoVerdictModel()
    describe.run(tmp_path, model=model, model_id="no-verdict", config=CONFIG)
    assert len(model.calls) == 2
    data = read_describe(tmp_path, "clip")
    seg = data["segments"][0]
    assert seg["status"] == "described"
    assert seg["verdict"]["role"] == "move"
    assert seg["verdict"]["motion_label"] == "complex_motion"
    assert seg["verdict"]["confidence"] == 0.0
    assert data["warnings"]
    assert validate(data, "describe") == []


def test_retry_with_none_text_falls_back_to_geometry(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [
            segment(
                "s1",
                0.0,
                4.0,
                "move",
                motion_label="complex_motion",
                uncertain=True,
                reasons=["mixed_motion_signals"],
            )
        ],
    )
    model = NoVerdictThenNoneModel()
    describe.run(tmp_path, model=model, model_id="no-verdict-none", config=CONFIG)
    assert len(model.calls) == 2
    data = read_describe(tmp_path, "clip")
    seg = data["segments"][0]
    assert seg["status"] == "described"
    assert seg["verdict"]["role"] == "move"
    assert seg["verdict"]["motion_label"] == "complex_motion"
    assert seg["verdict"]["confidence"] == 0.0
    assert data["warnings"]
    assert validate(data, "describe") == []


def test_max_calls_stops_run_and_marks_rest(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [
            segment("s1", 0.0, 2.0, "hold", motion_label="static"),
            segment("s2", 2.0, 4.0, "hold", motion_label="static"),
            segment("s3", 4.0, 6.0, "hold", motion_label="static"),
        ],
    )
    model = MockVisionModel()
    summary, code = describe.run(
        tmp_path, model=model, model_id="mock/vision", config=CONFIG, max_calls=1
    )
    assert code == 2
    assert len(model.calls) == 1
    data = read_describe(tmp_path, "clip")
    assert [s["status"] for s in data["segments"]] == ["described", "skipped", "skipped"]
    assert data["segments"][1]["skip_reason"] == "max_calls"
    assert data["segments"][2]["skip_reason"] == "max_calls"
    assert validate(data, "describe") == []


def test_budget_exceeded_stops_run_and_marks_rest(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [
            segment("s1", 0.0, 2.0, "hold", motion_label="static"),
            segment("s2", 2.0, 4.0, "hold", motion_label="static"),
        ],
    )
    model = BudgetModel()
    summary, code = describe.run(tmp_path, model=model, model_id="budget", config=CONFIG)
    assert code == 2
    data = read_describe(tmp_path, "clip")
    assert [s["status"] for s in data["segments"]] == ["described", "skipped"]
    assert data["segments"][1]["skip_reason"] == "budget_exceeded"
    assert validate(data, "describe") == []


def test_rerun_without_force_skips_clip(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [segment("s1", 0.0, 4.0, "hold", motion_label="static")],
    )
    model = MockVisionModel()
    describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG)
    calls_after_first = len(model.calls)
    summary, code = describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG)
    assert code == 0
    assert summary["described"] == 0
    assert summary["skipped"] == 1
    assert len(model.calls) == calls_after_first


def test_clips_filter_selects_substrings(tmp_path):
    first = write_video(tmp_path / "dji_0067.mp4")
    second = write_video(tmp_path / "dji_0081.mp4")
    write_quality(tmp_path, "dji_0067", first, [segment("s1", 0.0, 4.0, "hold")])
    write_quality(tmp_path, "dji_0081", second, [segment("s1", 0.0, 4.0, "hold")])
    model = MockVisionModel()
    summary, code = describe.run(
        tmp_path, clips="0067", model=model, model_id="mock/vision", config=CONFIG
    )
    assert code == 0
    assert summary["clips"] == 1
    assert (tmp_path / "describe" / "dji_0067.json").exists()
    assert not (tmp_path / "describe" / "dji_0081.json").exists()


def test_json_summary_has_flat_keys(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [segment("s1", 0.0, 4.0, "hold", motion_label="static")],
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "models:\n  vision: mock/vision\nllm_budget_usd: 5.0\nllm_backend: mock\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workspace",
            str(tmp_path),
            "--config",
            str(config_path),
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert "wall_time_s" in summary
    assert "llm_cost_usd" in summary
    assert summary["model"] == "mock/vision"
    assert summary["described"] == 1


def test_normalize_summary_is_capped_at_twelve_words():
    answer = describe.normalize_answer(
        {
            "description": "d",
            "summary": " ".join(f"w{i}" for i in range(20)),
            "tags": ["Boat", "boat", " Sea Cave ", "x" * 40, "sunset", "wake", "cave", "extra"],
            "subject": "none",
            "scenery": "s",
            "semantic_motion": "none",
            "interest": 50,
            "hero": False,
            "verdict": None,
        }
    )
    assert len(answer["summary"].split()) == 12
    assert answer["summary"] == " ".join(f"w{i}" for i in range(12))
    assert answer["tags"] == ["boat", "sea cave", "x" * 32, "sunset", "wake", "cave"]
    assert len(answer["tags"]) == 6
    assert all(len(tag) <= 32 for tag in answer["tags"])


def test_normalize_summary_and_tags_tolerate_missing_values():
    answer = describe.normalize_answer({"description": "d"})
    assert answer["summary"] is None
    assert answer["tags"] == []


def test_described_segment_carries_summary_and_tags(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(
        tmp_path,
        "clip",
        clip,
        [segment("s1", 0.0, 4.0, "hold", motion_label="static", uncertain=False)],
    )
    model = MockVisionModel()
    describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG)
    data = read_describe(tmp_path, "clip")
    seg = data["segments"][0]
    assert seg["status"] == "described"
    assert seg["summary"] and len(seg["summary"].split()) <= 12
    assert isinstance(seg["tags"], list) and seg["tags"]
    assert validate(data, "describe") == []


class BeatsModel:
    """Returns a full answer with content beats for long segments."""

    def __init__(self, beats):
        self.beats = beats
        self.calls = []

    def describe(self, request):
        self.calls.append(request)
        context = request.context or {}
        beats = self.beats if context.get("id") == "s7" else []
        beats_json = json.dumps(beats)
        text = (
            "{\n"
            '  "description": "Mock long take",\n'
            '  "summary": "mock long take through the tunnel",\n'
            '  "tags": ["mock", "tunnel"],\n'
            '  "subject": "tunnel",\n'
            '  "scenery": "mock scenery",\n'
            '  "semantic_motion": "travel",\n'
            '  "interest": 70,\n'
            '  "hero": true,\n'
            '  "verdict": null,\n'
            f'  "beats": {beats_json}\n'
            "}"
        )
        return VisionResponse(
            text=text,
            model="mock/vision",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
        )

    def spend_usd(self):
        return 0.0


def test_frame_times_sample_every_five_seconds_on_long_segment():
    short = describe.frame_times(0.0, 12.0)
    assert len(short) == 3
    long = describe.frame_times(0.0, 134.0)
    assert len(long) == describe.BEATS_MAX_FRAMES
    mid = describe.frame_times(0.0, 25.0)
    assert len(mid) == 5
    assert mid == sorted(mid)
    assert all(0.0 < t < 25.0 for t in mid)


def test_normalize_beats_tiles_and_renumbers():
    beats = describe.normalize_beats(
        [
            {"start_s": 20.0, "end_s": 55.0, "label": "approach", "summary": "boat enters the tunnel", "interest": 80, "hero": True},
            {"start_s": 60.0, "end_s": 130.0, "label": "inside", "summary": "inside the tunnel", "interest": 55, "hero": False},
            {"start_s": 130.0, "end_s": 162.7, "label": "exit", "summary": "tunnel exit into the light", "interest": 90, "hero": True},
        ]
    )
    assert [b["id"] for b in beats] == ["b1", "b2", "b3"]
    assert beats[0]["start_s"] == 20.0
    assert beats[0]["end_s"] == beats[1]["start_s"] == 55.0 or beats[0]["end_s"] == 60.0
    assert beats[1]["end_s"] == beats[2]["start_s"]
    assert beats[2]["end_s"] == 162.7
    assert beats[0]["label"] == "approach"
    assert beats[0]["hero"] is True
    assert beats[1]["hero"] is False
    assert beats[2]["interest"] == 90
    assert all(b["end_s"] > b["start_s"] for b in beats)


def test_normalize_beats_fills_the_gaps_between_beats():
    beats = describe.normalize_beats(
        [
            {"start_s": 0.0, "end_s": 10.0, "label": "a"},
            {"start_s": 30.0, "end_s": 40.0, "label": "b"},
            {"start_s": 90.0, "end_s": 100.0, "label": "c"},
        ]
    )
    assert [b["id"] for b in beats] == ["b1", "b2", "b3"]
    assert beats[0]["end_s"] == 30.0
    assert beats[1]["start_s"] == 30.0
    assert beats[1]["end_s"] == 90.0
    assert beats[2]["start_s"] == 90.0
    assert beats[2]["end_s"] == 100.0


def test_normalize_beats_rejects_single_and_invalid():
    assert describe.normalize_beats([]) == []
    assert describe.normalize_beats(None) == []
    assert describe.normalize_beats([{"start_s": 0.0, "end_s": 10.0, "label": "a"}]) == []
    assert describe.normalize_beats([{"start_s": 10.0, "end_s": 10.0, "label": "a"}, {"start_s": 10.0, "end_s": 10.0, "label": "b"}]) == []
    assert describe.normalize_beats([{"start_s": "x", "end_s": 10.0}, {"start_s": 0.0, "end_s": 5.0, "label": "b"}]) == []


def test_long_segment_carries_beats_and_short_does_not(tmp_path):
    clip = write_video(tmp_path / "clip.mp4", seconds=28, fps=5)
    write_quality(
        tmp_path,
        "clip",
        clip,
        [
            segment("s7", 0.0, 25.0, "move", motion_label="travel_forward", uncertain=False),
            segment("s8", 25.0, 28.0, "hold", motion_label="static", uncertain=False),
        ],
    )
    model = BeatsModel(
        [
            {"start_s": 0.0, "end_s": 10.0, "label": "approach", "summary": "approach to the tunnel", "interest": 80, "hero": True},
            {"start_s": 10.0, "end_s": 18.0, "label": "inside", "summary": "inside the tunnel", "interest": 55, "hero": False},
            {"start_s": 18.0, "end_s": 25.0, "label": "exit", "summary": "exit into the light", "interest": 90, "hero": True},
        ]
    )
    describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG)
    data = read_describe(tmp_path, "clip")
    assert validate(data, "describe") == []
    long_segment = data["segments"][0]
    short_segment = data["segments"][1]
    assert long_segment["id"] == "s7"
    assert [b["id"] for b in long_segment["beats"]] == ["b1", "b2", "b3"]
    assert long_segment["beats"][0]["label"] == "approach"
    assert short_segment.get("beats") == []


def test_beats_dropped_when_segment_is_short(tmp_path):
    clip = write_video(tmp_path / "clip.mp4", seconds=12, fps=5)
    write_quality(
        tmp_path,
        "clip",
        clip,
        [segment("s1", 0.0, 10.0, "move", motion_label="travel_forward", uncertain=False)],
    )
    model = BeatsModel(
        [
            {"start_s": 0.0, "end_s": 5.0, "label": "a", "summary": "one", "interest": 50, "hero": False},
            {"start_s": 5.0, "end_s": 10.0, "label": "b", "summary": "two", "interest": 50, "hero": False},
        ]
    )
    describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG)
    data = read_describe(tmp_path, "clip")
    assert validate(data, "describe") == []
    assert data["segments"][0].get("beats") == []


def test_json_summary_counts_beats(tmp_path):
    clip = write_video(tmp_path / "clip.mp4", seconds=26, fps=5)
    write_quality(
        tmp_path,
        "clip",
        clip,
        [segment("s7", 0.0, 25.0, "move", motion_label="travel_forward", uncertain=False)],
    )
    model = BeatsModel(
        [
            {"start_s": 0.0, "end_s": 12.0, "label": "approach", "summary": "approach", "interest": 80, "hero": True},
            {"start_s": 12.0, "end_s": 25.0, "label": "exit", "summary": "exit", "interest": 90, "hero": True},
        ]
    )
    summary, exit_code = describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG)
    assert exit_code == 0
    assert summary["beats"] == 2


class PhotoPositionModel:
    """Returns a photo answer with an explicit subject position."""

    def __init__(self, position):
        self.position = position
        self.calls = []

    def describe(self, request):
        self.calls.append(request)
        text = json.dumps(
            {
                "description": "a photo of a boat",
                "summary": "boat moored in a turquoise bay",
                "tags": ["boat", "bay"],
                "subject": "boat",
                "scenery": "sea",
                "semantic_motion": "none",
                "interest": 65,
                "hero": False,
                "subject_position": self.position,
            }
        )
        return VisionResponse(
            text=text,
            model="photo",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
        )


def test_photo_described_with_pseudo_segment(tmp_path):
    photo = write_photo(tmp_path / "photo_01.jpg")
    write_ingest(tmp_path, [photo_record(photo)])
    model = MockVisionModel()
    summary, code = describe.run(
        tmp_path, model=model, model_id="mock/vision", config=CONFIG, photos_only=True
    )
    assert code == 0
    assert summary["photos"] == 1
    assert len(model.calls) == 1
    assert len(model.calls[0].frames) == 1
    data = read_describe(tmp_path, "photo_01")
    assert data["media_type"] == "photo"
    assert data["file"] == "photo_01.jpg"
    assert data["duration_s"] == 0.0
    seg = data["segments"][0]
    assert seg["id"] == "p1"
    assert seg["status"] == "described"
    assert seg["start_s"] == 0.0
    assert seg["end_s"] == 0.04
    assert seg["role"] == "hold"
    assert seg["motion_label"] == "static"
    assert seg["uncertain"] is False
    assert seg["uncertain_reasons"] == []
    assert seg["frames"] == [{"t_s": 0.0}]
    assert seg["semantic_motion"] == "none"
    assert seg["verdict"] is None
    assert seg["subject_position"] == {"x": 0.5, "y": 0.5}
    assert validate(data, "describe") == []


def test_photo_subject_position_falls_back_to_center(tmp_path):
    photo = write_photo(tmp_path / "photo_01.jpg")
    write_ingest(tmp_path, [photo_record(photo)])
    model = MockVisionModel()
    describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG, photos_only=True)
    data = read_describe(tmp_path, "photo_01")
    assert data["segments"][0]["subject_position"] == {"x": 0.5, "y": 0.5}


def test_photo_subject_position_is_used(tmp_path):
    photo = write_photo(tmp_path / "photo_01.jpg")
    write_ingest(tmp_path, [photo_record(photo)])
    model = PhotoPositionModel({"x": 0.2, "y": 0.8})
    describe.run(tmp_path, model=model, model_id="photo", config=CONFIG, photos_only=True)
    data = read_describe(tmp_path, "photo_01")
    assert data["segments"][0]["subject_position"] == {"x": 0.2, "y": 0.8}
    assert validate(data, "describe") == []


def test_normalize_subject_position_clamps_and_falls_back():
    assert describe.normalize_subject_position({"x": 1.5, "y": -0.2}) == {"x": 1.0, "y": 0.0}
    assert describe.normalize_subject_position(None) == {"x": 0.5, "y": 0.5}
    assert describe.normalize_subject_position({"x": "a", "y": 0.5}) == {"x": 0.5, "y": 0.5}


def test_photo_rerun_skips_and_force_redescribes(tmp_path):
    photo = write_photo(tmp_path / "photo_01.jpg")
    write_ingest(tmp_path, [photo_record(photo)])
    model = MockVisionModel()
    describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG, photos_only=True)
    calls_after_first = len(model.calls)
    summary, code = describe.run(
        tmp_path, model=model, model_id="mock/vision", config=CONFIG, photos_only=True
    )
    assert code == 0
    assert summary["photos"] == 0
    assert len(model.calls) == calls_after_first
    summary, code = describe.run(
        tmp_path, model=model, model_id="mock/vision", config=CONFIG, photos_only=True, force=True
    )
    assert code == 0
    assert summary["photos"] == 1
    assert len(model.calls) == calls_after_first + 1


def test_photos_only_skips_video_segments(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(tmp_path, "clip", clip, [segment("s1", 0.0, 4.0, "hold")])
    photo = write_photo(tmp_path / "photo_01.jpg")
    write_ingest(tmp_path, [photo_record(photo)])
    model = MockVisionModel()
    summary, code = describe.run(
        tmp_path, model=model, model_id="mock/vision", config=CONFIG, photos_only=True
    )
    assert code == 0
    assert summary["clips"] == 0
    assert summary["photos"] == 1
    assert not (tmp_path / "describe" / "clip.json").exists()
    assert (tmp_path / "describe" / "photo_01.json").exists()


def test_photos_described_automatically_with_video(tmp_path):
    clip = write_video(tmp_path / "clip.mp4")
    write_quality(tmp_path, "clip", clip, [segment("s1", 0.0, 4.0, "hold")])
    photo = write_photo(tmp_path / "photo_01.jpg")
    write_ingest(tmp_path, [photo_record(photo)])
    model = MockVisionModel()
    summary, code = describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG)
    assert code == 0
    assert summary["described"] == 1
    assert summary["photos"] == 1
    assert (tmp_path / "describe" / "clip.json").exists()
    assert (tmp_path / "describe" / "photo_01.json").exists()


def test_photo_prompt_uses_photo_file(tmp_path):
    photo = write_photo(tmp_path / "photo_01.jpg")
    write_ingest(tmp_path, [photo_record(photo)])
    model = MockVisionModel()
    describe.run(tmp_path, model=model, model_id="mock/vision", config=CONFIG, photos_only=True)
    prompt = model.calls[0].prompt
    assert "Photo facts:" in prompt
    assert "photo_01.jpg" in prompt
    assert "subject_position" in prompt


def test_prompt_file_overrides_photo_prompt(tmp_path):
    photo = write_photo(tmp_path / "photo_01.jpg")
    write_ingest(tmp_path, [photo_record(photo)])
    custom = tmp_path / "custom.md"
    custom.write_text("CUSTOM PHOTO PROMPT", encoding="utf-8")
    model = MockVisionModel()
    describe.run(
        tmp_path,
        model=model,
        model_id="mock/vision",
        config=CONFIG,
        photos_only=True,
        prompt_path=custom,
    )
    assert model.calls[0].prompt.startswith("CUSTOM PHOTO PROMPT")
