# SPDX-License-Identifier: AGPL-3.0-or-later
"""Post short notifications to an ntfy server.

The URL is read from the ``NTFY_URL`` environment variable and falls back to
``http://10.10.1.61:8090/opencode-doru``. The message body is the plain-text
notification; title, priority and tags are sent as ntfy headers.
"""

import os
import urllib.error
import urllib.request

DEFAULT_NTFY_URL = "http://10.10.1.61:8090/opencode-doru"


class NotifyError(RuntimeError):
    """Raised when a notification cannot be delivered."""


def resolve_url(url=None):
    return url or os.environ.get("NTFY_URL") or DEFAULT_NTFY_URL


def send(message, title=None, url=None, priority=None, tags=None, timeout=10):
    if not message or not message.strip():
        raise NotifyError("notification message is empty")

    target = resolve_url(url)
    headers = {}
    if title:
        headers["Title"] = title
    if priority:
        headers["Priority"] = str(priority)
    if tags:
        headers["Tags"] = ",".join(tags) if isinstance(tags, (list, tuple)) else str(tags)

    request = urllib.request.Request(
        target,
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except (urllib.error.URLError, OSError) as exc:
        raise NotifyError(f"could not reach {target}: {exc}") from exc