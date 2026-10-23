# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read the OpenRouter key endpoint: credit and daily usage.

The URL is built from ``common.constants.DEFAULT_BASE_URL`` so the API host
lives in one place and this module stays light (it must not pull ``openai`` or
``filelock``). ``key_info`` returns the endpoint's ``data`` object; the callers
pick the fields they need (credit limits, daily usage).
"""

import json
import urllib.request

from common.constants import DEFAULT_BASE_URL

KEY_INFO_URL = DEFAULT_BASE_URL + "/key"


def key_info(api_key, timeout=10, opener=None):
    """GET the OpenRouter key endpoint; return its `data` object or raise."""
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(
        KEY_INFO_URL, headers={"Authorization": f"Bearer {api_key}"}
    )
    with opener(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8")).get("data")
    if not isinstance(data, dict):
        raise ValueError("key endpoint returned no data object")
    return data
