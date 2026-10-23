import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

import thumbnail
from common.schema import validate
from fs_probe import safe_is_file

ROOT = Path(__file__).resolve().parents[1]

try:
    import psd_tools
except ImportError as exc:
    raise AssertionError(
        f"psd-tools is a hard requirement of thumbnail.py --psd; install it with pip install psd-tools ({exc})"
    ) from exc
from psd_tools import PSDImage
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME


def _rembg_model_present():
    try:
        import rembg  # noqa: F401
    except Exception:
        return False
    candidates = [
        Path.home() / ".u2net" / "birefnet-portrait.onnx",
        Path.home() / ".rembg" / "models" / "birefnet-portrait" / "birefnet-portrait.onnx",
    ]
    return any(safe_is_file(candidate) for candidate in candidates)


def synthetic_portrait(path, width=800, height=1000):
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    skin = (240, 190, 150, 255)
    clothes = (40, 90, 120, 255)
    draw.ellipse((300, 40, 520, 260), fill=skin)
    draw.rectangle((280, 240, 540, 980), fill=clothes)
    draw.rectangle((120, 260, 300, 700), fill=clothes)
    draw.rectangle((520, 260, 700, 700), fill=clothes)
    image.save(path, format="PNG")
    return path


def synthetic_frame(path, width=640, height=480):
    gradient = np.linspace(30, 220, height, dtype=np.uint8)
    rows = np.repeat(gradient[:, None], width, axis=1)
    Image.fromarray(np.stack([rows, rows, rows], axis=2), "RGB").save(path, format="JPEG")
    return path


def base_args(photo, out):
    return [
        "--photo", str(photo),
        "--title", "AI-UL MEU",
        "--keyword", "RULEAZĂ ACASĂ",
        "--theme", "tech",
        "--out", str(out),
    ]


def test_brand_json_validates_against_schema():
    brand = json.loads((ROOT / "brand.json").read_text(encoding="utf-8"))
    assert validate(brand, "brand") == []


def test_tech_render_from_synthetic_portrait(tmp_path):
    photo = synthetic_portrait(tmp_path / "cartoon_portrait.png")
    out = tmp_path / "out-tech"
    code = thumbnail.main(base_args(photo, out) + ["--kicker", "// homelab · automatizări", "--skip-cutout"])
    assert code == 0
    jpg = out / "thumbnail.jpg"
    assert jpg.is_file()
    image = Image.open(jpg)
    assert image.size == (1280, 720)
    assert image.mode == "RGB"


def test_creator_render_with_background(tmp_path):
    photo = synthetic_portrait(tmp_path / "cartoon_portrait.png")
    frame = synthetic_frame(tmp_path / "gradient_frame.jpg")
    out = tmp_path / "out-creator"
    code = thumbnail.main([
        "--photo", str(photo), "--title", "AI-UL MEU", "--keyword", "RULEAZĂ ACASĂ",
        "--theme", "creator", "--background", str(frame), "--out", str(out), "--skip-cutout",
    ])
    assert code == 0
    image = Image.open(out / "thumbnail.jpg")
    assert image.size == (1280, 720)
    assert image.mode == "RGB"


def test_creator_without_background_fails(tmp_path, capsys):
    photo = synthetic_portrait(tmp_path / "cartoon_portrait.png")
    out = tmp_path / "out-creator"
    code = thumbnail.main([
        "--photo", str(photo), "--title", "AI-UL MEU", "--keyword", "RULEAZĂ ACASĂ",
        "--theme", "creator", "--out", str(out), "--skip-cutout",
    ])
    assert code == EXIT_USAGE
    captured = capsys.readouterr()
    assert "[error]" in captured.err
    assert "--background" in captured.err
    assert not (out / "thumbnail.jpg").exists()


def test_psd_layers_without_kicker(tmp_path):
    photo = synthetic_portrait(tmp_path / "cartoon_portrait.png")
    out = tmp_path / "out"
    code = thumbnail.main(base_args(photo, out) + ["--skip-cutout", "--psd"])
    assert code == 0
    psd_path = out / "thumbnail.psd"
    assert psd_path.is_file()
    psd = PSDImage.open(psd_path)
    assert [layer.name for layer in psd] == ["background", "portrait", "title", "keyword", "tag"]
    assert psd.size == (1280, 720)
    composite = psd.composite()
    assert composite.size == (1280, 720)


def test_psd_layers_with_kicker(tmp_path):
    photo = synthetic_portrait(tmp_path / "cartoon_portrait.png")
    out = tmp_path / "out"
    code = thumbnail.main(base_args(photo, out) + ["--kicker", "// homelab · automatizări", "--skip-cutout", "--psd"])
    assert code == 0
    psd = PSDImage.open(out / "thumbnail.psd")
    assert [layer.name for layer in psd] == [
        "background", "portrait", "title", "keyword", "kicker", "tag",
    ]
    assert psd.size == (1280, 720)
    assert psd.composite().size == (1280, 720)


def test_json_summary_keys_and_keyword_fit(tmp_path, capsys):
    photo = synthetic_portrait(tmp_path / "cartoon_portrait.png")
    out = tmp_path / "out"
    code = thumbnail.main([
        "--photo", str(photo), "--title", "AI-UL MEU",
        "--keyword", "RULEAZĂ ACASĂ PÂNĂ DIMINEAȚA",
        "--theme", "tech", "--out", str(out), "--skip-cutout", "--json",
    ])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["tool"] == "scripts/thumbnail.py"
    assert summary["theme"] == "tech"
    assert summary["psd"] is None
    assert summary["psd_writer"] == "psd-tools"
    assert summary["cutout_used"] is False
    assert summary["cutout_model"] == "birefnet-portrait"
    assert summary["llm_cost_usd"] == 0.0
    assert summary["wall_time_s"] >= 0
    assert set(summary["subject_bbox_px"]) == {"left", "top", "width", "height"}
    assert 1 <= len(summary["keyword_lines"]) <= 2
    brand = json.loads((ROOT / "brand.json").read_text(encoding="utf-8"))
    font = ImageFont.truetype(str(ROOT / brand["fonts"]["title"]["path"]), summary["font_size_keyword_px"])
    tracking = brand["fonts"]["title"]["tracking_em"] * summary["font_size_keyword_px"]
    column = brand["layout"]["text_right_x"] - brand["layout"]["text_x"]
    fits = all(
        thumbnail.tracked_width(line, font, tracking) <= column
        for line in summary["keyword_lines"]
    )
    assert fits or summary["warnings"]


def test_keyword_two_lines_with_comma_below_do_not_touch():
    brand = json.loads((ROOT / "brand.json").read_text(encoding="utf-8"))
    brand["shadow"] = None
    font_path = str(ROOT / brand["fonts"]["title"]["path"])
    column = brand["layout"]["text_right_x"] - brand["layout"]["text_x"]
    theme = brand["themes"]["tech"]
    element, size, lines, warnings = thumbnail.render_keyword(
        "A IEȘIT HAOS", brand, font_path, theme, column
    )
    assert len(lines) == 2
    assert "Ș" in lines[0]
    font = thumbnail.load_font(font_path, size)
    tracking = brand["fonts"]["title"]["tracking_em"] * size
    leading = thumbnail.keyword_leading(lines, font, tracking, brand["layout"]["keyword_leading_px"])
    bands = [thumbnail.line_ink_band(line, font, tracking) for line in lines]
    gap = leading + bands[1][0] - bands[0][1] - 1
    assert gap >= 2
    alpha = np.asarray(element["image"].getchannel("A"))
    ink_rows = np.where((alpha > 0).any(axis=1))[0]
    expected_top = min(bands[0][0], leading + bands[1][0])
    expected_bottom = max(bands[0][1], leading + bands[1][1])
    assert ink_rows[-1] - ink_rows[0] == expected_bottom - expected_top


def test_missing_font_fails_naming_path_and_key(tmp_path, capsys):
    brand = json.loads((ROOT / "brand.json").read_text(encoding="utf-8"))
    brand["fonts"]["title"]["path"] = "assets/fonts/DoesNotExist.ttf"
    brand_path = tmp_path / "brand.json"
    brand_path.write_text(json.dumps(brand), encoding="utf-8")
    photo = synthetic_portrait(tmp_path / "cartoon_portrait.png")
    code = thumbnail.main(base_args(photo, tmp_path / "out") + ["--skip-cutout", "--brand", str(brand_path)])
    assert code == EXIT_USAGE
    captured = capsys.readouterr()
    assert "[error]" in captured.err
    assert "DoesNotExist.ttf" in captured.err
    assert "fonts.title" in captured.err


def test_invalid_brand_canvas_fails_via_schema(tmp_path, capsys):
    brand = json.loads((ROOT / "brand.json").read_text(encoding="utf-8"))
    brand["canvas"]["width"] = 100
    brand_path = tmp_path / "brand.json"
    brand_path.write_text(json.dumps(brand), encoding="utf-8")
    photo = synthetic_portrait(tmp_path / "cartoon_portrait.png")
    code = thumbnail.main(base_args(photo, tmp_path / "out") + ["--skip-cutout", "--brand", str(brand_path)])
    assert code == EXIT_USAGE
    captured = capsys.readouterr()
    assert "[error]" in captured.err
    assert "brand" in captured.err


def test_invalid_brand_color_fails_via_schema(tmp_path, capsys):
    brand = json.loads((ROOT / "brand.json").read_text(encoding="utf-8"))
    brand["colors"]["cool"] = "#5FD3C"
    brand_path = tmp_path / "brand.json"
    brand_path.write_text(json.dumps(brand), encoding="utf-8")
    photo = synthetic_portrait(tmp_path / "cartoon_portrait.png")
    code = thumbnail.main(base_args(photo, tmp_path / "out") + ["--skip-cutout", "--brand", str(brand_path)])
    assert code == EXIT_USAGE
    assert "[error]" in capsys.readouterr().err


def test_rerun_overwrites_both_outputs(tmp_path):
    photo = synthetic_portrait(tmp_path / "cartoon_portrait.png")
    out = tmp_path / "out"
    command = base_args(photo, out) + ["--kicker", "// homelab · automatizări", "--skip-cutout", "--psd"]
    assert thumbnail.main(command) == 0
    assert thumbnail.main(command) == 0
    assert (out / "thumbnail.jpg").is_file()
    assert (out / "thumbnail.psd").is_file()
    assert not list(out.glob("*.tmp"))


@pytest.mark.skipif(
    not _rembg_model_present(),
    reason="rembg or the birefnet-portrait model (~/.u2net/ or ~/.rembg/models/) is not present",
)
def test_rembg_cutout_integration(tmp_path, capsys):
    photo = synthetic_portrait(tmp_path / "cartoon_portrait.png")
    out = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts" / "thumbnail.py"),
            "--photo", str(photo), "--title", "AI-UL MEU", "--keyword", "RULEAZĂ ACASĂ",
            "--theme", "tech", "--out", str(out), "--json",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode in (137, -9):
        pytest.skip("the birefnet-portrait cut-out was OOM-killed on this machine")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["cutout_used"] is True
    image = Image.open(out / "thumbnail.jpg")
    assert image.size == (1280, 720)
    assert image.mode == "RGB"


def test_psd_tools_is_available():
    assert psd_tools.__version__
