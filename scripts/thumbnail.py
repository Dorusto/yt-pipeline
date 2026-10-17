#!/usr/bin/env python3
"""Render a branded 1280x720 YouTube thumbnail from a portrait photo and a title.

The look comes from the brand kit (``brand.json``, validated against
``schemas/brand.schema.json``): palette, fonts, layout baselines, drop shadow,
portrait cut-out and placement, the generated-background recipe and per-theme
colour roles. The portrait is cut out with rembg (``birefnet-portrait``, lazy
import) or, with ``--skip-cutout``, the photo's own alpha; the outer glow is
screen-blended onto the background before the portrait is composited. With
``--psd`` the same render is also written as a layered ``thumbnail.psd``
(psd-tools, rasterized text layers). No model calls; ``llm_cost_usd`` is 0.0.

Usage:
    python scripts/thumbnail.py --photo IN.jpg --title "AI-UL MEU" --keyword "RULEAZĂ ACASĂ" \
        --kicker "// homelab · automatizări" --theme tech --out DIR [--psd] [--json]
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from common.schema import SchemaError, validate_or_raise

TOOL = "scripts/thumbnail.py"
DEFAULT_BRAND = Path(__file__).resolve().parents[1] / "brand.json"
FONT_KEYS = ("title", "kicker", "tag")
TITLE_FLOOR_PX = 40
TITLE_STEP_PX = 4
KEYWORD_FLOOR_PX = 48
KEYWORD_STEP_PX = 4
KICKER_FLOOR_PX = 16
KICKER_STEP_PX = 2
JPEG_QUALITY = 92


class ThumbnailError(RuntimeError):
    """Raised when the thumbnail cannot be rendered from the given inputs."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="thumbnail.py",
        description=(
            "Render a branded 1280x720 YouTube thumbnail from a portrait photo and a "
            "title, following the brand kit (brand.json). Two themes: tech (generated "
            "dark background) and creator (photo background, required)."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/thumbnail.py --photo photo.jpg --title \"AI-UL MEU\" "
            "--keyword \"RULEAZĂ ACASĂ\" --kicker \"// homelab · automatizări\" --theme tech --out thumb/\n"
            "  python scripts/thumbnail.py --photo photo.jpg --title \"AI-UL MEU\" "
            "--keyword \"RULEAZĂ ACASĂ\" --theme creator --background frame.jpg --out thumb/ --psd\n"
            "  python scripts/thumbnail.py --photo photo.png --title \"T\" --keyword \"K\" "
            "--theme tech --out thumb/ --skip-cutout --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--photo", required=True, help="input portrait photo (jpg/png), read-only")
    parser.add_argument("--title", required=True, help="title line (e.g. AI-UL MEU)")
    parser.add_argument("--keyword", required=True, help="big accented words (e.g. RULEAZĂ ACASĂ), may wrap to 2 lines")
    parser.add_argument("--kicker", default="", help="small line above the title (e.g. // homelab · automatizări)")
    parser.add_argument("--theme", required=True, choices=("tech", "creator"), help="brand theme")
    parser.add_argument("--background", default=None, help="frame for the background (required for creator, optional for tech)")
    parser.add_argument("--out", required=True, help="output directory (created if missing)")
    parser.add_argument("--psd", action="store_true", help="also write thumbnail.psd with one separate layer per element")
    parser.add_argument("--brand", default=None, help="brand kit JSON (default: brand.json at the repo root)")
    parser.add_argument("--skip-cutout", action="store_true", help="use the photo's own alpha as the cut-out, no rembg")
    parser.add_argument("--json", action="store_true", help="print the machine-readable summary object instead of the human line")
    return parser


def hex_to_rgb(value):
    digits = value.lstrip("#")
    return tuple(int(digits[index:index + 2], 16) for index in (0, 2, 4))


def load_brand(path):
    brand_path = Path(path).expanduser()
    if not brand_path.is_file():
        raise ThumbnailError(f"brand kit not found: {brand_path}")
    try:
        brand = json.loads(brand_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ThumbnailError(f"cannot read brand kit {brand_path}: {exc}") from exc
    try:
        validate_or_raise(brand, "brand")
    except SchemaError as exc:
        raise ThumbnailError(f"invalid brand kit {brand_path}: {exc}") from exc
    return brand, brand_path


def resolve_font(brand, brand_dir, key):
    font_path = Path(brand["fonts"][key]["path"])
    if not font_path.is_absolute():
        font_path = brand_dir / font_path
    if not font_path.is_file():
        raise ThumbnailError(f'font file not found for brand key "fonts.{key}": {font_path}')
    return str(font_path)


_font_cache = {}


def load_font(path, size_px):
    key = (path, round(float(size_px), 3))
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype(path, int(round(size_px)))
        except OSError as exc:
            raise ThumbnailError(f"cannot load font {path} at {size_px} px: {exc}") from exc
    return _font_cache[key]


def open_image(path):
    image_path = Path(path).expanduser()
    if not image_path.is_file():
        raise ThumbnailError(f"image not found: {image_path}")
    try:
        image = Image.open(image_path)
        image.load()
    except (OSError, ValueError) as exc:
        raise ThumbnailError(f"cannot read image {image_path}: {exc}") from exc
    return image


def tracked_width(text, font, tracking_px):
    if not text:
        return 0.0
    return sum(font.getlength(char) for char in text) + tracking_px * (len(text) - 1)


def draw_tracked(draw, xy, text, font, fill, tracking_px):
    pen_x, baseline = xy
    for char in text:
        draw.text((pen_x, baseline), char, font=font, fill=fill, anchor="ls")
        pen_x += font.getlength(char) + tracking_px


def fit_title(text, font_path, spec, column_width):
    size = int(spec["size_px"])
    tracking_em = spec["tracking_em"]
    while size > TITLE_FLOOR_PX and tracked_width(text, load_font(font_path, size), tracking_em * size) > column_width:
        size -= TITLE_STEP_PX
    warnings = []
    if tracked_width(text, load_font(font_path, size), tracking_em * size) > column_width:
        warnings.append(
            f"title does not fit {column_width:g} px at the {TITLE_FLOOR_PX} px floor; drawn anyway"
        )
    return size, warnings


def greedy_wrap(words, font, tracking_px, max_width):
    lines = []
    current = []
    for word in words:
        candidate = " ".join([*current, word])
        if not current or tracked_width(candidate, font, tracking_px) <= max_width:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def hard_split_lines(text, font, tracking_px, max_width, max_lines):
    lines = []
    remaining = text
    while remaining and len(lines) < max_lines:
        take = len(remaining)
        while take > 1 and tracked_width(remaining[:take], font, tracking_px) > max_width:
            take -= 1
        chunk = remaining[:take]
        if len(lines) < max_lines - 1:
            space = chunk.rfind(" ")
            if space > 0:
                chunk = chunk[:space]
        lines.append(chunk)
        remaining = remaining[len(chunk):].lstrip()
    if remaining:
        if lines:
            lines[-1] = f"{lines[-1]} {remaining}"
        else:
            lines.append(remaining)
    return lines


def fit_keyword(text, font_path, spec, tracking_em, column_width, max_lines):
    words = text.split()
    size = int(spec["size_px"])
    warnings = []
    floor = min(KEYWORD_FLOOR_PX, int(spec["size_px"]))
    while size > floor:
        font = load_font(font_path, size)
        lines = greedy_wrap(words, font, tracking_em * size, column_width)
        if len(lines) <= max_lines and all(
            tracked_width(line, font, tracking_em * size) <= column_width for line in lines
        ):
            return lines, size, warnings
        size -= KEYWORD_STEP_PX
    size = floor
    font = load_font(font_path, size)
    lines = greedy_wrap(words, font, tracking_em * size, column_width)
    if len(lines) <= max_lines and all(
        tracked_width(line, font, tracking_em * size) <= column_width for line in lines
    ):
        return lines, size, warnings
    lines = hard_split_lines(text, font, tracking_em * size, column_width, max_lines)
    warnings.append(f"keyword hard-split at the {size} px floor to fit {column_width:g} px")
    return lines, size, warnings


def fit_kicker(text, font_path, spec, column_width):
    size = int(spec["size_px"])
    while size > KICKER_FLOOR_PX and load_font(font_path, size).getlength(text) > column_width:
        size -= KICKER_STEP_PX
    return size


def blend_blob(image, xx, yy, center, radii, color, weight):
    distance = np.sqrt(((xx - center[0]) / radii[0]) ** 2 + ((yy - center[1]) / radii[1]) ** 2)
    weight_map = np.clip(1.0 - distance, 0.0, 1.0) ** 2 * weight
    image *= (1.0 - weight_map)[..., None]
    image += np.asarray(color, dtype=np.float64) * weight_map[..., None]


def blend_grid(image, xx, yy, grid, color):
    step = int(grid["step_px"])
    line_px = int(grid["line_px"])
    lines = ((xx % step) < line_px) | ((yy % step) < line_px)
    alpha = np.clip((xx - grid["x_start"]) / grid["x_ramp"], 0.0, 1.0) * grid["opacity"] * lines
    image *= (1.0 - alpha)[..., None]
    image += np.asarray(color, dtype=np.float64) * alpha[..., None]


def apply_vignette(image, xx, yy, width, height, vignette):
    distance = np.sqrt(
        ((xx - width / 2) / (width * vignette["scale"])) ** 2
        + ((yy - height / 2) / (height * vignette["scale"])) ** 2
    )
    factor = np.clip(vignette["base"] - vignette["falloff"] * distance, vignette["floor"], 1.0)
    image *= factor[..., None]


def apply_grain(image, grain):
    rng = np.random.default_rng(grain["seed"])
    image += rng.normal(0.0, grain["sigma"], image.shape)
    np.clip(image, 0.0, 255.0, out=image)


def generated_background(brand, width, height):
    generated = brand["background"]["generated"]
    colors = brand["colors"]
    image = np.full((height, width, 3), hex_to_rgb(colors["night"]), dtype=np.float64)
    yy, xx = np.mgrid[0:height, 0:width]
    for key in ("warm_glow", "cool_glow"):
        blob = generated[key]
        blend_blob(
            image, xx, yy, blob["center"], blob["radii"],
            hex_to_rgb(colors[key.removesuffix("_glow")]), blob["weight"],
        )
    grid = generated["grid"]
    blend_grid(image, xx, yy, grid, hex_to_rgb(colors[grid["color_role"]]))
    apply_vignette(image, xx, yy, width, height, generated["vignette"])
    apply_grain(image, generated["grain"])
    return image


def cover_scale(image, width, height):
    frame_w, frame_h = image.size
    scale = max(width / frame_w, height / frame_h)
    scaled = image.resize(
        (max(1, round(frame_w * scale)), max(1, round(frame_h * scale))), Image.LANCZOS
    )
    left = (scaled.width - width) // 2
    top = (scaled.height - height) // 2
    return scaled.crop((left, top, left + width, top + height))


def photo_background(path, brand, width, height):
    frame = open_image(path).convert("RGB")
    image = np.asarray(cover_scale(frame, width, height), dtype=np.float64)
    image *= brand["background"]["photo"]["brightness"]
    generated = brand["background"]["generated"]
    yy, xx = np.mgrid[0:height, 0:width]
    apply_vignette(image, xx, yy, width, height, generated["vignette"])
    apply_grain(image, generated["grain"])
    return image


def build_background(background_path, theme, brand, width, height):
    if background_path:
        return photo_background(Path(background_path), brand, width, height)
    if theme["background"] == "photo":
        raise ThumbnailError(
            f"--background is required for this theme (theme background is photo); "
            f"pass a frame or use --theme tech"
        )
    return generated_background(brand, width, height)


def cutout_portrait(photo_image, model_name):
    try:
        from rembg import new_session, remove
    except ImportError as exc:
        raise ThumbnailError(
            "rembg is not installed: pip install rembg (or re-run with --skip-cutout)"
        ) from exc
    session = new_session(model_name)
    return remove(photo_image, session=session)


def harden_alpha(portrait_rgba, spec):
    alpha = np.asarray(portrait_rgba.getchannel("A"))
    hardened = np.where(alpha > spec["harden_threshold"], 255, 0).astype(np.uint8)
    mask = Image.fromarray(hardened, "L")
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.5))
    result = portrait_rgba.copy()
    result.putalpha(mask)
    return result


def place_portrait(portrait_rgba, brand):
    spec = brand["portrait"]
    alpha = np.asarray(portrait_rgba.getchannel("A"))
    subject = alpha > spec["alpha_subject_min"]
    if not subject.any():
        raise ThumbnailError(
            f"no subject in photo: no pixel with alpha > {spec['alpha_subject_min']}"
        )
    ys, xs = np.where(subject)
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    mean_alpha = float(alpha[y0:y1 + 1, x0:x1 + 1].mean())
    if mean_alpha < spec["harden_mean_min"]:
        portrait_rgba = harden_alpha(portrait_rgba, spec)
    margin = int(spec["margin_px"])
    crop_box = (
        max(0, x0 - margin),
        max(0, y0 - margin),
        min(portrait_rgba.width, x1 + 1 + margin),
        min(portrait_rgba.height, y1 + 1 + int(spec["bottom_margin_px"])),
    )
    crop = portrait_rgba.crop(crop_box)
    subject_height = y1 - y0 + 1
    scale = (spec["subject_bottom_y"] - spec["subject_top_y"]) / subject_height
    scaled_size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    scaled = crop.resize(scaled_size, Image.LANCZOS)
    subject_left_in_crop = (x0 - crop_box[0]) * scale
    subject_bottom_in_crop = (y1 + 1 - crop_box[1]) * scale
    left = round(spec["subject_left_x"] - subject_left_in_crop)
    top = round(spec["subject_bottom_y"] - subject_bottom_in_crop)
    return scaled, (left, top, left + scaled_size[0], top + scaled_size[1])


def build_glow_mask(placed_rgba, glow_spec):
    dilate_px = max(1, round(glow_spec["spread"] * glow_spec["size_px"]))
    kernel = 2 * dilate_px + 1
    mask = placed_rgba.getchannel("A").filter(ImageFilter.MaxFilter(kernel))
    mask = mask.filter(ImageFilter.GaussianBlur(glow_spec["size_px"]))
    return mask.point(lambda value: round(value * glow_spec["opacity"]))


def paste_layer(canvas, layer, left, top):
    canvas_w, canvas_h = canvas.size
    layer_w, layer_h = layer.size
    dst_left, dst_top = max(0, left), max(0, top)
    dst_right, dst_bottom = min(canvas_w, left + layer_w), min(canvas_h, top + layer_h)
    if dst_right <= dst_left or dst_bottom <= dst_top:
        return
    src_box = (dst_left - left, dst_top - top, dst_right - left, dst_bottom - top)
    canvas.alpha_composite(layer.crop(src_box), (dst_left, dst_top))


def screen_blend(canvas, mask, color, left, top):
    canvas_w, canvas_h = canvas.size
    mask_w, mask_h = mask.size
    dst_left, dst_top = max(0, left), max(0, top)
    dst_right, dst_bottom = min(canvas_w, left + mask_w), min(canvas_h, top + mask_h)
    if dst_right <= dst_left or dst_bottom <= dst_top:
        return
    src_box = (dst_left - left, dst_top - top, dst_right - left, dst_bottom - top)
    alpha = np.asarray(mask.crop(src_box), dtype=np.float64) / 255.0
    region = np.array(canvas.crop((dst_left, dst_top, dst_right, dst_bottom)), dtype=np.float64)
    rgb = region[..., :3]
    color_arr = np.asarray(color, dtype=np.float64)
    screen = 255.0 - (255.0 - rgb) * (255.0 - color_arr) / 255.0
    rgb[...] = (1.0 - alpha[..., None]) * rgb + alpha[..., None] * screen
    canvas.paste(Image.fromarray(np.clip(region, 0, 255).astype(np.uint8), "RGBA"), (dst_left, dst_top))


def shadow_offset(spec):
    angle = math.radians(spec["angle_deg"] + 180)
    dx = spec["distance_px"] * math.cos(angle)
    dy = -spec["distance_px"] * math.sin(angle)
    return dx, dy


def render_text_layer(size, glyphs, color, shadow):
    """Rasterize one text element; ``glyphs(draw, dx, dy, fill)`` draws the glyphs."""
    width, height = size
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if shadow is not None:
        dx, dy = shadow_offset(shadow)
        silhouette = Image.new("L", (width, height), 0)
        glyphs(ImageDraw.Draw(silhouette), dx, dy, 255)
        silhouette = silhouette.filter(ImageFilter.GaussianBlur(shadow["blur_px"]))
        shadow_rgba = Image.new("RGBA", (width, height), hex_to_rgb(shadow["color"]) + (0,))
        shadow_rgba.putalpha(silhouette.point(lambda value: round(value * shadow["opacity"])))
        layer.alpha_composite(shadow_rgba)
    glyphs(ImageDraw.Draw(layer), 0.0, 0.0, color)
    bbox = layer.getbbox()
    if bbox is None:
        return None, None
    return layer.crop(bbox), (bbox[0], bbox[1])


def render_title(text, brand, font_path, column_width):
    spec = brand["fonts"]["title"]
    size, warnings = fit_title(text, font_path, spec, column_width)
    font = load_font(font_path, size)
    tracking_px = spec["tracking_em"] * size
    origin_x = brand["layout"]["text_x"]
    baseline = brand["layout"]["title_baseline_y"]
    color = hex_to_rgb(brand["colors"]["ink"])

    def glyphs(draw, dx, dy, fill):
        draw_tracked(draw, (origin_x + dx, baseline + dy), text, font, fill, tracking_px)

    image, offset = render_text_layer(
        (brand["canvas"]["width"], brand["canvas"]["height"]), glyphs, color, brand["shadow"]
    )
    return {"name": "title", "image": image, "offset": offset}, size, warnings


def render_keyword(text, brand, font_path, theme, column_width):
    spec = brand["fonts"]["title"]
    tracking_em = spec["tracking_em"]
    max_lines = brand["layout"]["keyword_max_lines"]
    lines, size, warnings = fit_keyword(text, font_path, spec, tracking_em, column_width, max_lines)
    font = load_font(font_path, size)
    tracking_px = tracking_em * size
    origin_x = brand["layout"]["text_x"]
    baseline = brand["layout"]["keyword_baseline_y"]
    leading = brand["layout"]["keyword_leading_px"]
    color = hex_to_rgb(brand["colors"][theme["keyword"]])

    def glyphs(draw, dx, dy, fill):
        for index, line in enumerate(lines):
            draw_tracked(draw, (origin_x + dx, baseline + index * leading + dy), line, font, fill, tracking_px)

    image, offset = render_text_layer(
        (brand["canvas"]["width"], brand["canvas"]["height"]), glyphs, color, brand["shadow"]
    )
    return {"name": "keyword", "image": image, "offset": offset}, size, lines, warnings


def render_kicker(text, brand, font_path, theme, column_width):
    if not text:
        return None
    spec = brand["fonts"]["kicker"]
    size = fit_kicker(text, font_path, spec, column_width)
    font = load_font(font_path, size)
    origin_x = brand["layout"]["text_x"]
    baseline = brand["layout"]["kicker_baseline_y"]
    color = hex_to_rgb(brand["colors"][theme["kicker"]])

    def glyphs(draw, dx, dy, fill):
        draw.text((origin_x + dx, baseline + dy), text, font=font, fill=fill, anchor="ls")

    image, offset = render_text_layer(
        (brand["canvas"]["width"], brand["canvas"]["height"]), glyphs, color, None
    )
    return {"name": "kicker", "image": image, "offset": offset}


def render_tag(brand, font_path):
    spec = brand["fonts"]["tag"]
    size = int(spec["size_px"])
    font = load_font(font_path, size)
    right_x = brand["layout"]["tag_right_x"]
    baseline = brand["layout"]["tag_baseline_y"]
    color = hex_to_rgb(brand["colors"]["muted"])
    text = brand["tag_text"]

    def glyphs(draw, dx, dy, fill):
        draw.text((right_x + dx, baseline + dy), text, font=font, fill=fill, anchor="rs")

    image, offset = render_text_layer(
        (brand["canvas"]["width"], brand["canvas"]["height"]), glyphs, color, None
    )
    return {"name": "tag", "image": image, "offset": offset}


def bake_glow_into_portrait(placed_rgba, glow_mask, glow_color):
    layer = Image.new("RGBA", placed_rgba.size, tuple(glow_color) + (0,))
    layer.putalpha(glow_mask)
    layer.alpha_composite(placed_rgba)
    return layer


def build_psd(brand, background_image, placed_rgba, placed_rect, glow_mask, glow_color, text_elements):
    from psd_tools import PSDImage

    width = brand["canvas"]["width"]
    height = brand["canvas"]["height"]
    psd = PSDImage.new("RGB", (width, height), color=hex_to_rgb(brand["colors"]["night"]))
    psd.append(psd.create_pixel_layer(background_image.convert("RGBA"), name="background", top=0, left=0))
    portrait_layer = bake_glow_into_portrait(placed_rgba, glow_mask, glow_color)
    psd.append(psd.create_pixel_layer(
        portrait_layer, name="portrait", top=placed_rect[1], left=placed_rect[0]
    ))
    for element in text_elements:
        psd.append(psd.create_pixel_layer(
            element["image"], name=element["name"],
            top=element["offset"][1], left=element["offset"][0],
        ))
    return psd


def atomic_write(path, writer):
    temporary = path.with_name(path.name + ".tmp")
    try:
        writer(temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def human_summary(summary):
    cutout = f"rembg {summary['cutout_model']}" if summary["cutout_used"] else "photo alpha"
    return (
        f"thumbnail: {summary['thumbnail']} (theme {summary['theme']}, "
        f"title {summary['font_size_title_px']} px, keyword {summary['font_size_keyword_px']} px, "
        f"cutout {cutout}, {summary['wall_time_s']:.2f} s)"
    )


def render(args, started):
    brand, brand_path = load_brand(args.brand or DEFAULT_BRAND)
    width = brand["canvas"]["width"]
    height = brand["canvas"]["height"]
    theme = brand["themes"][args.theme]
    brand_dir = brand_path.parent
    font_paths = {key: resolve_font(brand, brand_dir, key) for key in FONT_KEYS}
    column_width = brand["layout"]["text_right_x"] - brand["layout"]["text_x"]
    warnings = []

    background = build_background(args.background, theme, brand, width, height)

    photo_path = Path(args.photo).expanduser()
    photo = open_image(photo_path)
    if args.skip_cutout:
        portrait = photo.convert("RGBA")
        cutout_used = False
    else:
        portrait = cutout_portrait(photo, brand["portrait"]["cutout_model"])
        cutout_used = True
    placed_rgba, placed_rect = place_portrait(portrait, brand)

    glow_color = hex_to_rgb(brand["colors"][theme["glow"]])
    glow_mask = build_glow_mask(placed_rgba, brand["glow"])

    background_image = Image.fromarray(np.clip(background, 0, 255).astype(np.uint8), "RGB")
    canvas = background_image.convert("RGBA")
    screen_blend(canvas, glow_mask, glow_color, placed_rect[0], placed_rect[1])
    paste_layer(canvas, placed_rgba, placed_rect[0], placed_rect[1])

    title_element, title_size, title_warnings = render_title(args.title, brand, font_paths["title"], column_width)
    keyword_element, keyword_size, keyword_lines, keyword_warnings = render_keyword(
        args.keyword, brand, font_paths["title"], theme, column_width
    )
    kicker_element = render_kicker(args.kicker, brand, font_paths["kicker"], theme, column_width)
    tag_element = render_tag(brand, font_paths["tag"])
    warnings.extend(title_warnings)
    warnings.extend(keyword_warnings)

    text_elements = [
        element for element in (title_element, keyword_element, kicker_element, tag_element)
        if element is not None and element["image"] is not None
    ]
    for element in text_elements:
        paste_layer(canvas, element["image"], element["offset"][0], element["offset"][1])

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    thumbnail_path = out_dir / "thumbnail.jpg"
    flat = canvas.convert("RGB")
    atomic_write(thumbnail_path, lambda target: flat.save(target, format="JPEG", quality=JPEG_QUALITY))

    psd_path = None
    if args.psd:
        psd_path = out_dir / "thumbnail.psd"
        psd = build_psd(
            brand, background_image, placed_rgba, placed_rect, glow_mask, glow_color, text_elements
        )
        atomic_write(psd_path, lambda target: psd.save(target))

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tool": TOOL,
        "photo": str(photo_path),
        "title": args.title,
        "keyword": args.keyword,
        "kicker": args.kicker or "",
        "theme": args.theme,
        "brand": str(brand_path),
        "out_dir": str(out_dir),
        "thumbnail": str(thumbnail_path),
        "psd": str(psd_path) if psd_path else None,
        "cutout_used": cutout_used,
        "cutout_model": brand["portrait"]["cutout_model"],
        "psd_writer": "psd-tools",
        "font_size_title_px": title_size,
        "font_size_keyword_px": keyword_size,
        "keyword_lines": keyword_lines,
        "subject_bbox_px": {
            "left": placed_rect[0],
            "top": placed_rect[1],
            "width": placed_rect[2] - placed_rect[0],
            "height": placed_rect[3] - placed_rect[1],
        },
        "warnings": warnings,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": 0.0,
    }


def main(argv=None):
    started = time.monotonic()
    args = build_parser().parse_args(argv)
    try:
        summary = render(args, started)
    except (ThumbnailError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(human_summary(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
