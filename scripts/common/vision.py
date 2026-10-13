"""Vision model interface: JPEG frames and a prompt in, model text out.

Model backends are config, not code (VISION, principle 6): ``llm_backend`` in
``config.yaml`` picks the OpenRouter cloud client or the offline mock, and the
default model id is ``models.vision``. One request describes one segment — three
frames in order with timestamps, the motion label and the question — and returns
the model's raw text; parsing and schema validation stay in the calling tool.

Images leave the homelab only through this interface, and every call is charged
to the shared spend ledger with the per-run ``llm_budget_usd`` cap.
"""

from dataclasses import dataclass, field

from common.llm import LLMResponse, make_client

VISION_ROLE = "vision"
DEFAULT_MAX_TOKENS = 8000
DEFAULT_TEMPERATURE = 0.2
SEMANTIC_BY_LABEL = {
    "orbit_candidate": "orbit",
    "orbit": "orbit",
    "travel_forward": "travel",
    "travel_backward": "travel",
    "tracking": "tracking",
    "reveal": "reveal",
}


class VisionError(RuntimeError):
    """Raised when a vision request cannot be built or served."""


@dataclass
class VisionFrame:
    """One JPEG frame sampled from a segment, with its timestamp in the clip."""

    t_s: float
    jpeg_bytes: bytes


@dataclass
class VisionRequest:
    """One segment to describe: the prompt, its frames and the segment facts."""

    prompt: str
    frames: list
    context: dict = field(default_factory=dict)
    role: str = VISION_ROLE
    model: str = None
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = DEFAULT_TEMPERATURE


@dataclass
class VisionResponse:
    """The model's answer and what it cost."""

    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class VisionModel:
    """Interface every vision backend implements."""

    def describe(self, request):
        raise NotImplementedError


class OpenRouterVisionModel(VisionModel):
    """Cloud vision through the OpenRouter chat completions API."""

    def __init__(self, client=None, config=None, ledger_path=None):
        self.config = config
        self.client = client if client is not None else make_client(config, ledger_path)

    def describe(self, request):
        """Send the prompt with the frames as base64 image parts and return the text."""
        if not request.frames:
            raise VisionError("a vision request needs at least one frame")
        content = [{"type": "text", "text": request.prompt}]
        for frame in request.frames:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64," + _b64(frame.jpeg_bytes)
                    },
                }
            )
        messages = [{"role": "user", "content": content}]
        response = self.client.complete(
            request.role,
            messages,
            model=request.model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )
        return _response_from(response)

    def spend_usd(self):
        """Total USD charged to this client's ledger so far."""
        return self.client.spent_usd


class MockVisionModel(VisionModel):
    """Offline backend: deterministic structured text from the request context."""

    def __init__(self, client=None, config=None, ledger_path=None, cost_per_call=0.0):
        self.cost_per_call = cost_per_call
        self.calls = []

    def describe(self, request):
        """Return a deterministic JSON answer derived from ``request.context``."""
        self.calls.append(request)
        context = request.context or {}
        label = context.get("motion_label") or "static"
        uncertain = bool(context.get("uncertain"))
        frames = len(request.frames)
        times = ", ".join(f"{frame.t_s:.2f}s" for frame in request.frames)
        semantic = SEMANTIC_BY_LABEL.get(label, "none")
        interest = _mock_interest(context.get("id") or label)
        text = (
            "{\n"
            f'  "description": "Mock description of {frames} frames at {times} '
            f'with motion {label}",\n'
            f'  "subject": "mock subject of {context.get("id", "segment")}",\n'
            '  "scenery": "mock scenery",\n'
            f'  "semantic_motion": "{semantic}",\n'
            f'  "interest": {interest},\n'
            f'  "hero": {"true" if interest >= 75 else "false"},\n'
            f'  "verdict": {_mock_verdict_json(context) if uncertain else "null"}\n'
            "}"
        )
        return VisionResponse(
            text=text,
            model=request.model or "mock/vision",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=float(self.cost_per_call),
        )

    def spend_usd(self):
        """Total USD this mock has charged: the fixed per-call cost."""
        return round(self.cost_per_call * len(self.calls), 8)


def make_vision_model(config=None, ledger_path=None, client=None):
    """Build the vision backend selected by ``llm_backend`` in the config."""
    if client is not None:
        return OpenRouterVisionModel(client=client, config=config, ledger_path=ledger_path)
    if config is None:
        from common.config import load_config

        config = load_config()
    backend = config.get("llm_backend")
    if backend == "mock":
        return MockVisionModel(config=config, ledger_path=ledger_path)
    if backend == "openrouter":
        return OpenRouterVisionModel(config=config, ledger_path=ledger_path)
    raise VisionError(f"unknown llm_backend '{backend}'")


def _response_from(response: LLMResponse):
    return VisionResponse(
        text=response.text,
        model=response.model,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        cost_usd=response.cost_usd,
    )


def _b64(payload):
    import base64

    return base64.b64encode(payload).decode("ascii")


def _mock_interest(key):
    import zlib

    return 50 + zlib.crc32(str(key).encode("utf-8")) % 30


def _mock_verdict_json(context):
    role = context.get("role") or "hold"
    label = context.get("motion_label") or "static"
    reasons = context.get("uncertain_reasons") or []
    note = "mock verdict for " + (", ".join(reasons) if reasons else "uncertain label")
    return (
        "{"
        f'"role": "{role}", '
        f'"motion_label": "{label}", '
        '"confidence": 0.5, '
        f'"note": "{note}"'
        "}"
    )
