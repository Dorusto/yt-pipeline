import json

import pytest

from common.llm import DEFAULT_BASE_URL
from common.openrouter import KEY_INFO_URL, key_info


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_key_info_builds_the_url_and_returns_data():
    seen = {}

    def opener(request, timeout=None):
        seen["url"] = request.full_url
        return FakeResponse({"data": {"usage": 1.0}})

    data = key_info("k", opener=opener)
    assert data == {"usage": 1.0}
    assert seen["url"] == DEFAULT_BASE_URL + "/key"
    assert KEY_INFO_URL == DEFAULT_BASE_URL + "/key"


def test_key_info_raises_without_data():
    def opener(request, timeout=None):
        return FakeResponse({"nope": 1})

    with pytest.raises(ValueError):
        key_info("k", opener=opener)
