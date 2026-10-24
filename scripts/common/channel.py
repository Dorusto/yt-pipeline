# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read the channel's own fixed wording from the config (``channel.fixed``).

``channel.fixed`` maps a part name (``members_sentence``, ``closing``,
``thumbnail_formula`` — the names in ``CHANNEL_FIXED_PARTS``) to a mapping of
language code to string. The wording is the channel owner's, so it lives in the
config and never in code or prompts. A missing part, a missing language or an
empty string means the part is left out of the output — never replaced by
built-in text.
"""

CHANNEL_FIXED_PARTS = ("members_sentence", "closing", "thumbnail_formula")


def fixed_strings(config):
    """Return the channel's fixed wording mapping (``channel.fixed``), or {} when unset."""
    if not isinstance(config, dict):
        return {}
    channel = config.get("channel")
    if not isinstance(channel, dict):
        return {}
    fixed = channel.get("fixed")
    if not isinstance(fixed, dict):
        return {}
    return fixed


def fixed_string(fixed, part, lang):
    """Return ``fixed[part][lang]`` when it is a non-empty string, else None."""
    if not isinstance(fixed, dict):
        return None
    languages = fixed.get(part)
    if not isinstance(languages, dict):
        return None
    value = languages.get(lang)
    if not isinstance(value, str) or not value:
        return None
    return value
