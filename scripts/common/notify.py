# SPDX-License-Identifier: AGPL-3.0-or-later
"""Post short notifications to an ntfy server.

The URL is read from the ``NTFY_URL`` environment variable, else from the
config key ``ntfy_url``; there is no built-in default, so a deployment names
its own server. The message body is the plain-text notification; title,
priority and tags are sent as ntfy headers.
"""

import os
import urllib.error
import urllib.request


class NotifyError(RuntimeError):
    """Raised when a notification cannot be delivered or no URL is configured."""


def _config_url():
    from common.config import ConfigError, load_config

    try:
        value = load_config().get("ntfy_url")
    except ConfigError:
        return None
    return value if isinstance(value, str) and value.strip() else None


def resolve_url(url=None):
    """Return the ntfy target: the argument, else ``NTFY_URL``, else config ``ntfy_url``."""
    if url:
        return url
    from_env = os.environ.get("NTFY_URL")
    if from_env:
        return from_env
    from_config = _config_url()
    if from_config:
        return from_config
    raise NotifyError("no ntfy URL: set NTFY_URL or the config key 'ntfy_url'")


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