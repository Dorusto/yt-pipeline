import json
from types import SimpleNamespace

import pytest

from common.llm import LLMResponse
from common.vision import (
    MockVisionModel,
    OpenRouterVisionModel,
    VisionError,
    VisionFrame,
    VisionRequest,
    make_vision_model,
)


def frame(t_s=1.0, payload=b"\xff\xd8fake"):
    return VisionFrame(t_s=t_s, jpeg_bytes=payload)


def request(context=None, frames=None):
    return VisionRequest(
        prompt="describe the segment",
        frames=frames if frames is not None else [frame(0.5), frame(1.5), frame(2.5)],
        context=context
        or {"id": "s1", "role": "move", "motion_label": "pan_right", "uncertain": False},
    )


class FakeClient:
    """Records the call and returns a fixed LLMResponse."""

    def __init__(self):
        self.seen = None
        self.spent_usd = 0.0

    def complete(self, role, messages, model=None, **kwargs):
        self.seen = (role, messages, model, kwargs)
        self.spent_usd += 0.002
        return LLMResponse(
            text='{"description": "x"}',
            model=model or "test",
            role=role,
            prompt_tokens=11,
            completion_tokens=7,
            cost_usd=0.002,
        )


def test_mock_returns_parseable_json():
    model = MockVisionModel()
    response = model.describe(request())
    data = json.loads(response.text)
    assert data["subject"].startswith("mock subject")
    assert data["semantic_motion"] == "none"
    assert 0 <= data["interest"] <= 100
    assert data["verdict"] is None
    assert response.cost_usd == 0.0


def test_mock_is_deterministic():
    first = MockVisionModel().describe(request()).text
    second = MockVisionModel().describe(request()).text
    assert first == second


def test_mock_verdict_on_uncertain_segment():
    model = MockVisionModel()
    response = model.describe(
        request(
            context={
                "id": "s7",
                "role": "move",
                "motion_label": "complex_motion",
                "uncertain": True,
                "uncertain_reasons": ["mixed_motion_signals"],
            }
        )
    )
    data = json.loads(response.text)
    assert data["verdict"]["role"] == "move"
    assert data["verdict"]["motion_label"] == "complex_motion"
    assert "mixed_motion_signals" in data["verdict"]["note"]


@pytest.mark.parametrize(
    "label,semantic",
    [
        ("orbit_candidate", "orbit"),
        ("travel_forward", "travel"),
        ("travel_backward", "travel"),
        ("static", "none"),
    ],
)
def test_mock_semantic_motion_from_label(label, semantic):
    response = MockVisionModel().describe(
        request(
            context={"id": "s1", "role": "move", "motion_label": label, "uncertain": False}
        )
    )
    assert json.loads(response.text)["semantic_motion"] == semantic


def test_mock_interest_is_stable_and_bounded():
    values = {
        json.loads(MockVisionModel().describe(request(context={"id": key})).text)["interest"]
        for key in ("s1", "s2", "s3", "s4")
    }
    assert all(0 <= value <= 100 for value in values)
    assert len(values) >= 2


def test_openrouter_sends_frames_as_base64_images():
    client = FakeClient()
    model = OpenRouterVisionModel(client=client)
    response = model.describe(request(frames=[frame(0.5), frame(1.5)]))
    role, messages, model_id, kwargs = client.seen
    assert role == "vision"
    content = messages[0]["content"]
    assert content[0]["type"] == "text"
    images = [part for part in content if part["type"] == "image_url"]
    assert len(images) == 2
    assert all(
        part["image_url"]["url"].startswith("data:image/jpeg;base64,") for part in images
    )
    assert response.cost_usd == 0.002
    assert kwargs["max_tokens"] == request().max_tokens


def test_openrouter_rejects_empty_frames():
    model = OpenRouterVisionModel(client=FakeClient())
    with pytest.raises(VisionError):
        model.describe(request(frames=[]))


def test_make_vision_model_mock_backend():
    model = make_vision_model(config={"llm_backend": "mock"})
    assert isinstance(model, MockVisionModel)


def test_make_vision_model_rejects_unknown_backend():
    with pytest.raises(VisionError):
        make_vision_model(config={"llm_backend": "nope"})


def test_spend_usd_sums_calls():
    model = MockVisionModel(cost_per_call=0.01)
    model.describe(request())
    model.describe(request())
    assert model.spend_usd() == 0.02
