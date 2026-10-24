# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read the channel's own fixed wording from the config (``channel.fixed``).

``channel.fixed`` maps a part name (``members_sentence``, ``closing``,
``thumbnail_formula`` — the names in ``CHANNEL_FIXED_PARTS``) to a mapping of
language code to string. The wording is the channel owner's, so it lives in the
config and never in code or prompts. A missing part, a missing language or an
empty string means the part is left out of the output — never replaced by
built-in text.

``channel.style`` maps a part name (``title_style``, ``description_order``,
``pinned_comment_voice`` — the names in ``CHANNEL_STYLE_PARTS``) to a non-empty
string instruction. The writing tools hand the present parts to the model in a
``## Style`` section; a missing part is left out, never replaced by built-in
text. ``description_order`` also carries the order of the description parts
applied in code (``DESCRIPTION_PARTS``).
"""

CHANNEL_FIXED_PARTS = ("members_sentence", "closing", "thumbnail_formula")
CHANNEL_STYLE_PARTS = ("title_style", "description_order", "pinned_comment_voice")
DESCRIPTION_PARTS = ("intro", "github", "members", "previous", "subscribe", "promo", "closing")


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


def members_placeholder_used(fixed):
    """True when any members_sentence string contains the {members} placeholder."""
    if not isinstance(fixed, dict):
        return False
    sentence = fixed.get("members_sentence")
    if not isinstance(sentence, dict):
        return False
    return any(isinstance(text, str) and "{members}" in text for text in sentence.values())


def style_strings(config):
    """Return the channel's style mapping (``channel.style``), or {} when unset."""
    if not isinstance(config, dict):
        return {}
    channel = config.get("channel")
    if not isinstance(channel, dict):
        return {}
    style = channel.get("style")
    if not isinstance(style, dict):
        return {}
    return style


def style_string(style, part):
    """Return ``style[part]`` when it is a non-empty string, else None."""
    if not isinstance(style, dict):
        return None
    value = style.get(part)
    if not isinstance(value, str) or not value:
        return None
    return value


def parse_description_order(value):
    """Split a ``description_order`` string into part names; raise ValueError on an unknown name."""
    names = [name.strip() for name in value.split(",") if name.strip()]
    for name in names:
        if name not in DESCRIPTION_PARTS:
            raise ValueError(
                f"unknown description part '{name}'; valid parts: {', '.join(DESCRIPTION_PARTS)}"
            )
    return tuple(names)


def description_order(style):
    """Return the description part names in the configured order, or the default order."""
    value = style_string(style, "description_order")
    if value is None:
        return DESCRIPTION_PARTS
    return parse_description_order(value)


def style_section(style):
    """Return the ``## Style`` section text for the present parts, or None when none are set."""
    lines = [
        f"{part}: {value}"
        for part in CHANNEL_STYLE_PARTS
        if (value := style_string(style, part)) is not None
    ]
    if not lines:
        return None
    return "## Style\n" + "\n".join(lines)
