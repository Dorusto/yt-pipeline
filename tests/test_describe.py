import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

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
