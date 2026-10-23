#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render branded graphic overlays (lower third, counter, callout) as ProRes 4444 .mov with alpha.

Usage:
    python3 scripts/graphics.py --template lower_third --props '{"name":"Doru","role":"creator"}' --out lt.mov
    python3 scripts/graphics.py --plan graphics_plan.json --out-dir overlays/

The engine is HyperFrames (Apache-2.0): a composition HTML per template in
``assets/graphics/`` is filled with the brand values from ``brand.json`` and the
entry props, then rendered by the pinned HyperFrames CLI to a transparent MOV.
``--backend mock`` is deterministic and offline (composed HTML plus an ffmpeg
transparent MOV). No model service is called, so ``llm_cost_usd`` is always 0.0.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from common.ffmpeg_tools import ffmpeg_path
from common.brand import BrandError
from common.brand import load_brand as load_brand_kit
from common.brand import resolve_font as resolve_font_kit
from common.schema import validate
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "assets" / "graphics"
HYPERFRAMES_VERSION = "0.8.181"
BACKENDS = ("hyperframes", "mock")
TEMPLATES = ("lower_third", "counter", "callout")
ALPHA_PIX_FMTS = ("yuva444p10le", "yuva444p12le")
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 30.0
DEFAULT_DURATION = 3.0
MAX_DURATION = 15.0
CALLBACK_STYLES = ("text", "terminal")

TEMPLATE_PROPS = {
    "lower_third": {"required": ("name", "role"), "optional": ()},
    "counter": {"required": ("number", "label"), "optional": ()},
    "callout": {"required": ("text",), "optional": ("style",)},
}

VARIABLES_PATTERN = re.compile(r"data-composition-variables='(\[.*?\])'", re.DOTALL)


class GraphicsError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


class GraphicsRuntimeError(Exception):
    """An unexpected failure after validation that exits with code 1."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="graphics.py",
        description=(
            "Render branded graphic overlays (lower third, counter, callout) as ProRes 4444 "
            ".mov files with alpha, ready to drop over the edit in the editor."
        ),
        epilog=(
            "examples:\n"
            "  python3 scripts/graphics.py --template lower_third "
            "--props '{\"name\":\"Doru\",\"role\":\"creator\"}' --out lt.mov\n"
            "  python3 scripts/graphics.py --template counter "
            "--props '{\"number\":\"42\",\"label\":\"clips\"}' --out counter.mov --duration 3 --json\n"
            "  python3 scripts/graphics.py --plan graphics_plan.json --out-dir overlays/\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--template", choices=TEMPLATES, help="overlay template to render")
    parser.add_argument("--props", help="template props as a JSON object")
    parser.add_argument("--out", help="output .mov path (ProRes 4444 with alpha)")
    parser.add_argument("--plan", help="graphics_plan.json; renders only approved entries")
    parser.add_argument("--out-dir", help="output directory for --plan (one <id>.mov per entry)")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH, help=f"canvas width px (default {DEFAULT_WIDTH})")
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help=f"canvas height px (default {DEFAULT_HEIGHT})")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS, help=f"frame rate (default {DEFAULT_FPS:g})")
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION,
        help=f"clip duration in seconds, > 0 and <= {MAX_DURATION:g} (default {DEFAULT_DURATION:g})",
    )
    parser.add_argument("--brand", default=None, help="brand kit JSON (default: brand.json at the repo root)")
    parser.add_argument("--backend", choices=BACKENDS, default="hyperframes", help="render backend (default hyperframes)")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def format_fps(fps):
    """Format a frame rate for the CLI: an integer when whole, else the float."""
    return str(int(fps)) if float(fps).is_integer() else str(fps)


def parse_props(raw):
    """Parse ``--props`` into a dict, or raise a user error."""
    try:
        props = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GraphicsError(f"--props is not valid JSON: {exc}")
    if not isinstance(props, dict):
        raise GraphicsError("--props must be a JSON object")
    return props


def validate_props(template, props):
    """Check the props against the template's required and optional keys."""
    if template not in TEMPLATES:
        raise GraphicsError(f"unknown template '{template}'; use one of {', '.join(TEMPLATES)}")
    spec = TEMPLATE_PROPS[template]
    allowed = set(spec["required"]) | set(spec["optional"])
    unknown = sorted(set(props) - allowed)
    if unknown:
        raise GraphicsError(f"unknown props for {template}: {', '.join(unknown)}")
    for key in spec["required"]:
        if key not in props:
            raise GraphicsError(f"missing required prop '{key}' for {template}")
        value = props[key]
        if value is None or (isinstance(value, str) and not value.strip()):
            raise GraphicsError(f"prop '{key}' for {template} must not be empty")
    if template == "callout":
        style = props.get("style", "text")
        if style not in CALLBACK_STYLES:
            raise GraphicsError(
                f"prop 'style' for callout must be one of {', '.join(CALLBACK_STYLES)}, got '{style}'"
            )


def validate_canvas(width, height, fps, duration):
    """Check the output canvas values, raising a user error on anything invalid."""
    if width <= 0 or height <= 0:
        raise GraphicsError(f"--width and --height must be > 0, got {width}x{height}")
    if fps <= 0:
        raise GraphicsError(f"--fps must be > 0, got {fps}")
    if not 0 < duration <= MAX_DURATION:
        raise GraphicsError(f"--duration must be > 0 and <= {MAX_DURATION:g}, got {duration}")


def load_brand(path):
    """Load and validate the brand kit, returning ``(brand, brand_path)``."""
    try:
        return load_brand_kit(path)
    except BrandError as exc:
        raise GraphicsError(str(exc))


def resolve_font(brand, brand_dir, key):
    """Return the absolute font path for a brand key, or raise naming path and key."""
    try:
        return resolve_font_kit(brand, brand_dir, key)
    except BrandError as exc:
        raise GraphicsError(str(exc))


def resolve_fonts(brand, brand_dir):
    """Resolve the title and kicker fonts into the values the templates need."""
    title = resolve_font(brand, brand_dir, "title")
    mono = resolve_font(brand, brand_dir, "kicker")
    return {
        "title_family": brand["fonts"]["title"]["family"],
        "mono_family": brand["fonts"]["kicker"]["family"],
        "title_url": f"fonts/{title.name}",
        "mono_url": f"fonts/{mono.name}",
        "title_path": title,
        "mono_path": mono,
    }


def brand_values(brand):
    """The brand look values passed to every template as composition variables."""
    colors = brand["colors"]
    fonts = brand["fonts"]
    return {
        "accent": colors["warm"],
        "fg": colors["ink"],
        "bg": colors["night"],
        "muted": colors["muted"],
        "title_font": fonts["title"]["family"],
        "mono_font": fonts["kicker"]["family"],
        "title_size_px": fonts["title"]["size_px"],
        "kicker_size_px": fonts["kicker"]["size_px"],
        "tracking_em": fonts["title"]["tracking_em"],
    }


def render_values(template, props, brand):
    """Merge the brand values with the template props into one variable map."""
    values = brand_values(brand)
    if template == "lower_third":
        values["name"] = props["name"]
        values["role"] = props["role"]
    elif template == "counter":
        values["number"] = str(props["number"])
        values["label"] = props["label"]
    elif template == "callout":
        values["text"] = props["text"]
        values["style"] = props.get("style", "text")
    return values


def read_template(template):
    """Read the composition HTML for a template."""
    path = TEMPLATES_DIR / f"{template}.html"
    if not path.is_file():
        raise GraphicsError(f"template file not found: {path}")
    return path.read_text(encoding="utf-8")


def rewrite_variables(html, values):
    """Set the declared variable defaults to the actual values; return the declared ids."""
    match = VARIABLES_PATTERN.search(html)
    if not match:
        raise GraphicsError("template has no data-composition-variables declaration")
    try:
        declarations = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise GraphicsError(f"template variables are not valid JSON: {exc}")
    declared = [declaration["id"] for declaration in declarations]
    for declaration in declarations:
        if declaration["id"] in values:
            declaration["default"] = values[declaration["id"]]
    replacement = "data-composition-variables='" + json.dumps(declarations, ensure_ascii=False) + "'"
    return html[: match.start()] + replacement + html[match.end():], declared


def compose_html(template, values, width, height, fps, duration, fonts):
    """Fill a template composition with the canvas, fonts and variable values."""
    html = read_template(template)
    html = html.replace("__TITLE_FAMILY__", fonts["title_family"])
    html = html.replace("__MONO_FAMILY__", fonts["mono_family"])
    html = html.replace("__TITLE_FONT_URL__", fonts["title_url"])
    html = html.replace("__MONO_FONT_URL__", fonts["mono_url"])
    html = html.replace('data-width="1920"', f'data-width="{width}"')
    html = html.replace('data-height="1080"', f'data-height="{height}"')
    html = html.replace('data-fps="30"', f'data-fps="{format_fps(fps)}"')
    html = html.replace('data-duration="3"', f'data-duration="{duration:g}"')
    html = html.replace("width: 1920px", f"width: {width}px")
    html = html.replace("height: 1080px", f"height: {height}px")
    html, declared = rewrite_variables(html, values)
    return html, declared


def copy_fonts(fonts, work_dir):
    """Copy the resolved brand fonts into the render work dir under ``fonts/``."""
    target = work_dir / "fonts"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fonts["title_path"], target / fonts["title_path"].name)
    shutil.copyfile(fonts["mono_path"], target / fonts["mono_path"].name)


class GraphicsEngine:
    """Backend interface: render one template to a ProRes 4444 .mov with alpha."""

    name = "base"

    def render(self, template, props, out, width, height, fps, duration, brand, brand_dir):
        raise NotImplementedError


class HyperFramesEngine(GraphicsEngine):
    """Real backend: fill the composition and render it with the pinned HyperFrames CLI."""

    name = "hyperframes"

    def render(self, template, props, out, width, height, fps, duration, brand, brand_dir):
        if shutil.which("node") is None or shutil.which("npx") is None:
            raise GraphicsError(
                "Node.js 22+ and npx are required for --backend hyperframes "
                "(install Node.js from https://nodejs.org, then re-run)"
            )
        values = render_values(template, props, brand)
        fonts = resolve_fonts(brand, brand_dir)
        with tempfile.TemporaryDirectory(prefix="graphics_") as temporary_dir:
            work_dir = Path(temporary_dir)
            html, declared = compose_html(template, values, width, height, fps, duration, fonts)
            (work_dir / "index.html").write_text(html, encoding="utf-8")
            copy_fonts(fonts, work_dir)
            variables = {key: values[key] for key in declared if key in values}
            variables_file = work_dir / "variables.json"
            variables_file.write_text(json.dumps(variables, ensure_ascii=False), encoding="utf-8")
            command = [
                "npx",
                "--yes",
                f"hyperframes@{HYPERFRAMES_VERSION}",
                "render",
                "--format",
                "mov",
                "--output",
                str(out),
                "--variables-file",
                str(variables_file),
                "--strict-variables",
                "--fps",
                format_fps(fps),
            ]
            try:
                result = subprocess.run(command, cwd=work_dir, capture_output=True, text=True)
            except OSError as exc:
                raise GraphicsError(f"could not run the HyperFrames CLI: {exc}")
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip().splitlines()
                tail = "\n".join(detail[-8:]) if detail else "unknown error"
                raise GraphicsRuntimeError(f"hyperframes render failed for {out}:\n{tail}")
        return {"output": str(out), "template": template}


class MockEngine(GraphicsEngine):
    """Deterministic offline backend: composed HTML plus an ffmpeg transparent MOV."""

    name = "mock"

    def render(self, template, props, out, width, height, fps, duration, brand, brand_dir):
        if shutil.which("ffmpeg") is None:
            raise GraphicsError("ffmpeg not found on PATH; --backend mock needs it to write the transparent MOV")
        values = render_values(template, props, brand)
        fonts = resolve_fonts(brand, brand_dir)
        html, _ = compose_html(template, values, width, height, fps, duration, fonts)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.with_suffix(".index.html").write_text(html, encoding="utf-8")
        command = [
            ffmpeg_path(),
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black@0.0:s={width}x{height}:r={format_fps(fps)}:d={duration:g}",
            "-vf",
            "format=yuva444p10le",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4444",
            "-pix_fmt",
            "yuva444p10le",
            str(out),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True)
        except OSError as exc:
            raise GraphicsError(f"could not run ffmpeg: {exc}")
        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            tail = "\n".join(detail[-5:]) if detail else "unknown error"
            raise GraphicsRuntimeError(f"ffmpeg failed to write {out}:\n{tail}")
        return {"output": str(out), "template": template}


def make_engine(backend):
    """Return the engine for a backend name."""
    if backend == "hyperframes":
        return HyperFramesEngine()
    if backend == "mock":
        return MockEngine()
    raise GraphicsError(f"unknown --backend '{backend}'; use one of {', '.join(BACKENDS)}")


def probe_pix_fmt(path):
    """Return the pixel format of the first video stream of ``path``."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=pix_fmt",
        "-of",
        "default=nw=1:nk=1",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise GraphicsRuntimeError(f"could not run ffprobe: {exc}")
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else "unknown error"
        raise GraphicsRuntimeError(f"ffprobe could not read {path}: {tail}")
    return result.stdout.strip()


def verify_alpha(path):
    """Fail loudly when the rendered MOV does not carry an alpha pixel format."""
    pix_fmt = probe_pix_fmt(path)
    if pix_fmt not in ALPHA_PIX_FMTS:
        raise GraphicsRuntimeError(
            f"{path} has no alpha: pix_fmt={pix_fmt} (expected one of {', '.join(ALPHA_PIX_FMTS)})"
        )
    return pix_fmt


def run_single(template, props_json, out, width, height, fps, duration, brand_path, backend):
    """Render one template to one .mov and return the summary dict."""
    started = time.monotonic()
    if template not in TEMPLATES:
        raise GraphicsError(f"unknown --template '{template}'; use one of {', '.join(TEMPLATES)}")
    props = parse_props(props_json)
    validate_props(template, props)
    validate_canvas(width, height, fps, duration)
    brand, brand_path = load_brand(brand_path)
    brand_dir = brand_path.parent
    output = Path(out).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(backend)
    engine.render(template, props, output, width, height, fps, duration, brand, brand_dir)
    verify_alpha(output)
    return {
        "output": str(output),
        "template": template,
        "width": width,
        "height": height,
        "fps": fps,
        "duration_s": duration,
        "backend": engine.name,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def rejected_props_error(error, entries):
    """True when a schema error is about the props of a rejected plan entry."""
    path = error.split(":", 1)[0]
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] == "entries" and parts[1].isdigit() and parts[2] == "props":
        index = int(parts[1])
        if 0 <= index < len(entries) and isinstance(entries[index], dict):
            return not entries[index].get("approved")
    return False


def run_plan(plan_path, out_dir, width, height, fps, brand_path, backend):
    """Render every approved entry of a graphics plan and return the summary dict."""
    started = time.monotonic()
    plan_file = Path(plan_path).expanduser()
    if not plan_file.is_file():
        raise GraphicsError(f"plan not found: {plan_file}")
    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GraphicsError(f"cannot read plan {plan_file}: {exc}") from exc
    if not isinstance(plan, dict):
        raise GraphicsError(f"invalid plan {plan_file}: not a JSON object")
    entries = plan.get("entries") if isinstance(plan.get("entries"), list) else []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("approved"):
            continue
        template = entry.get("template")
        props = entry.get("props")
        if template in TEMPLATES and isinstance(props, dict):
            try:
                validate_props(template, props)
            except GraphicsError as exc:
                raise GraphicsError(f"entry {entry.get('id', '?')}: {exc}")
    errors = [error for error in validate(plan, "graphics_plan") if not rejected_props_error(error, entries)]
    if errors:
        raise GraphicsError(f"invalid plan {plan_file}: " + "; ".join(errors))
    validate_canvas(width, height, fps, DEFAULT_DURATION)
    brand, brand_path = load_brand(brand_path)
    brand_dir = brand_path.parent
    output_dir = Path(out_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(backend)
    outputs = []
    for entry in plan["entries"]:
        if not entry.get("approved"):
            continue
        entry_id = entry["id"]
        template = entry["template"]
        props = entry["props"]
        try:
            validate_props(template, props)
        except GraphicsError as exc:
            raise GraphicsError(f"entry {entry_id}: {exc}")
        duration = float(entry["duration_s"])
        if not 0 < duration <= MAX_DURATION:
            raise GraphicsError(f"entry {entry_id}: duration_s {duration} must be > 0 and <= {MAX_DURATION:g}")
        output = output_dir / f"{entry_id}.mov"
        engine.render(template, props, output, width, height, fps, duration, brand, brand_dir)
        verify_alpha(output)
        outputs.append(
            {
                "id": entry_id,
                "template": template,
                "output": str(output),
                "time_s": entry["time_s"],
                "duration_s": duration,
            }
        )
    return {
        "outputs": outputs,
        "width": width,
        "height": height,
        "fps": fps,
        "backend": engine.name,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.plan:
            if args.template or args.props or args.out:
                raise GraphicsError("--plan cannot be combined with --template/--props/--out")
            if not args.out_dir:
                raise GraphicsError("--plan needs --out-dir")
            summary = run_plan(
                args.plan, args.out_dir, args.width, args.height, args.fps, args.brand, args.backend
            )
        else:
            if not args.template:
                raise GraphicsError("either --template or --plan is required")
            if not args.props:
                raise GraphicsError("--template needs --props")
            if not args.out:
                raise GraphicsError("--template needs --out")
            summary = run_single(
                args.template,
                args.props,
                args.out,
                args.width,
                args.height,
                args.fps,
                args.duration,
                args.brand,
                args.backend,
            )
    except GraphicsError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_USAGE
    except GraphicsRuntimeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    if args.json:
        print(json.dumps(summary))
    elif "outputs" in summary:
        if not summary["outputs"]:
            print("no approved entries in the plan; nothing rendered")
        for item in summary["outputs"]:
            print(f"written: {item['output']} ({item['template']})")
    else:
        print(f"written: {summary['output']} ({summary['template']})")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
