"""Load and validate the brand kit (``brand.json``) and resolve its font paths.

Shared by ``thumbnail.py``, ``graphics.py`` and ``shorts_generator.py``: the kit
is validated against ``schemas/brand.schema.json`` and a missing font file is a
hard error naming the path and the brand key, never a silent fallback.
"""

import json
from pathlib import Path

from common.schema import SchemaError, validate_or_raise

DEFAULT_BRAND = Path(__file__).resolve().parents[1] / "brand.json"


class BrandError(Exception):
    """A user-facing error: the brand kit is missing, unreadable or invalid."""


def load_brand(path=None):
    """Return ``(brand, brand_path)`` for the validated brand kit."""
    brand_path = Path(path).expanduser() if path is not None else DEFAULT_BRAND
    if not brand_path.is_file():
        raise BrandError(f"brand kit not found: {brand_path}")
    try:
        brand = json.loads(brand_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrandError(f"cannot read brand kit {brand_path}: {exc}") from exc
    try:
        validate_or_raise(brand, "brand")
    except SchemaError as exc:
        raise BrandError(f"invalid brand kit {brand_path}: {exc}") from exc
    return brand, brand_path


def resolve_font(brand, brand_dir, key):
    """Return the absolute font path of a brand font key, or raise naming path and key."""
    font_path = Path(brand["fonts"][key]["path"])
    if not font_path.is_absolute():
        font_path = Path(brand_dir) / font_path
    if not font_path.is_file():
        raise BrandError(f'font file not found for brand key "fonts.{key}": {font_path}')
    return font_path
