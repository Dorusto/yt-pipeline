import json
import shutil
import subprocess
from pathlib import Path

import pytest

import graphics

ROOT = Path(__file__).resolve().parents[1]
ALPHA_PIX_FMTS = ("yuva444p10le", "yuva444p12le")
FONT_FILES = ("Montserrat-Black.ttf", "JetBrainsMono-Regular.ttf")


def load_brand_dict():
    return json.loads((ROOT / "brand.json").read_text(encoding="utf-8"))


def write_brand(tmp_path, brand):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps(brand), encoding="utf-8")
    return path


def copy_fonts(tmp_path):
    fonts_dir = tmp_path / "assets" / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    for name in FONT_FILES:
        shutil.copyfile(ROOT / "assets" / "fonts" / name, fonts_dir / name)


def write_plan(tmp_path, entries):
    plan = {"schema_version": "1", "generated_at": "2026-10-21T00:00:00Z", "entries": entries}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def entry(entry_id, template, props, approved, duration=2.0, time_s=1.0):
    return {
        "id": entry_id,
        "time_s": time_s,
        "duration_s": duration,
        "template": template,
        "props": props,
        "reason": "test",
        "approved": approved,
    }


def mock_args(tmp_path, template, props, out_name="out.mov"):
    return [
        "--template",
        template,
        "--props",
        json.dumps(props),
        "--out",
        str(tmp_path / out_name),
        "--width",
        "320",
        "--height",
        "180",
        "--backend",
        "mock",
    ]


def test_help_exits_0_and_lists_flags(capsys):
    with pytest.raises(SystemExit) as excinfo:
        graphics.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--template", "--props", "--out", "--plan", "--out-dir", "--width", "--height", "--fps", "--duration", "--brand", "--backend", "--json"):
        assert flag in out


def test_unknown_template_exits_2(capsys):
    with pytest.raises(SystemExit) as excinfo:
        graphics.main(["--template", "nope", "--props", "{}", "--out", "o.mov", "--backend", "mock"])
    assert excinfo.value.code == 2


@pytest.mark.parametrize(
    "template,props,needle",
    [
        ("lower_third", {"name": "Doru"}, "role"),
        ("lower_third", {"role": "creator"}, "name"),
        ("counter", {"number": "1"}, "label"),
        ("counter", {"label": "clips"}, "number"),
        ("callout", {}, "text"),
        ("lower_third", {"name": "Doru", "role": "creator", "extra": 1}, "extra"),
        ("callout", {"text": "x", "style": "nope"}, "style"),
        ("lower_third", {"name": "", "role": "creator"}, "name"),
    ],
)
def test_props_validation_exits_2(tmp_path, capsys, template, props, needle):
    rc = graphics.main(mock_args(tmp_path, template, props))
    assert rc == 2
    assert needle in capsys.readouterr().err


def test_bad_props_json_exits_2(tmp_path, capsys):
    rc = graphics.main(
        ["--template", "lower_third", "--props", "{not json", "--out", str(tmp_path / "o.mov"), "--backend", "mock"]
    )
    assert rc == 2
    assert "JSON" in capsys.readouterr().err


def test_duration_out_of_range_exits_2(tmp_path, capsys):
    args = mock_args(tmp_path, "counter", {"number": "1", "label": "x"})
    args += ["--duration", "20"]
    rc = graphics.main(args)
    assert rc == 2
    assert "duration" in capsys.readouterr().err


def test_brand_values_reach_composed_html(tmp_path):
    brand = load_brand_dict()
    brand["colors"]["warm"] = "#123456"
    brand["colors"]["ink"] = "#ABCDEF"
    brand["fonts"]["title"]["family"] = "Distinct Title"
    brand["fonts"]["kicker"]["family"] = "Distinct Mono"
    copy_fonts(tmp_path)
    brand_path = write_brand(tmp_path, brand)
    loaded, path = graphics.load_brand(brand_path)
    values = graphics.render_values("lower_third", {"name": "Doru", "role": "creator"}, loaded)
    fonts = graphics.resolve_fonts(loaded, path.parent)
    html, declared = graphics.compose_html("lower_third", values, 1280, 720, 25, 4.0, fonts)
    assert "#123456" in html
    assert "#ABCDEF" in html
    assert "Distinct Title" in html
    assert "Distinct Mono" in html
    assert "name" in declared and "role" in declared
    assert 'data-width="1280"' in html
    assert 'data-height="720"' in html
    assert 'data-fps="25"' in html
    assert 'data-duration="4"' in html
    assert "fonts/Montserrat-Black.ttf" in html
    assert "fonts/JetBrainsMono-Regular.ttf" in html


def test_missing_font_exits_2_naming_path_and_key(tmp_path, capsys):
    brand = load_brand_dict()
    brand["fonts"]["title"]["path"] = "missing/Title.ttf"
    brand_path = write_brand(tmp_path, brand)
    args = mock_args(tmp_path, "lower_third", {"name": "Doru", "role": "creator"})
    args += ["--brand", str(brand_path)]
    rc = graphics.main(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "fonts.title" in err
    assert "missing/Title.ttf" in err


def test_missing_brand_exits_2(tmp_path, capsys):
    args = mock_args(tmp_path, "lower_third", {"name": "Doru", "role": "creator"})
    args += ["--brand", str(tmp_path / "nope.json")]
    rc = graphics.main(args)
    assert rc == 2
    assert "nope.json" in capsys.readouterr().err


def test_mock_output_has_alpha_and_composed_html(tmp_path, capsys):
    rc = graphics.main(mock_args(tmp_path, "lower_third", {"name": "Doru", "role": "creator"}) + ["--json"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["backend"] == "mock"
    assert summary["template"] == "lower_third"
    assert summary["width"] == 320 and summary["height"] == 180
    assert summary["duration_s"] == 3.0
    assert summary["llm_cost_usd"] == 0.0
    assert "wall_time_s" in summary
    out = tmp_path / "out.mov"
    assert out.is_file()
    assert graphics.probe_pix_fmt(out) in ALPHA_PIX_FMTS
    assert (tmp_path / "out.index.html").is_file()


def test_mock_human_line(tmp_path, capsys):
    rc = graphics.main(mock_args(tmp_path, "counter", {"number": "42", "label": "clips"}))
    assert rc == 0
    out = capsys.readouterr().out
    assert "out.mov" in out and "counter" in out


def test_plan_renders_only_approved(tmp_path, capsys):
    plan = write_plan(
        tmp_path,
        [
            entry("g1", "lower_third", {"name": "Doru", "role": "creator"}, True),
            entry("g2", "counter", {"number": "42", "label": "clips"}, False),
            entry("g3", "callout", {"text": "echo hi", "style": "terminal"}, True, duration=2.5, time_s=8.0),
        ],
    )
    rc = graphics.main(
        ["--plan", str(plan), "--out-dir", str(tmp_path / "out"), "--width", "320", "--height", "180", "--backend", "mock", "--json"]
    )
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in summary["outputs"]] == ["g1", "g3"]
    assert summary["outputs"][0]["time_s"] == 1.0
    assert summary["outputs"][1]["duration_s"] == 2.5
    assert (tmp_path / "out" / "g1.mov").is_file()
    assert (tmp_path / "out" / "g3.mov").is_file()
    assert not (tmp_path / "out" / "g2.mov").exists()


def test_plan_zero_approved_exits_0(tmp_path, capsys):
    plan = write_plan(tmp_path, [entry("g1", "lower_third", {"name": "Doru", "role": "creator"}, False)])
    rc = graphics.main(
        ["--plan", str(plan), "--out-dir", str(tmp_path / "out"), "--backend", "mock", "--json"]
    )
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["outputs"] == []


def test_plan_zero_approved_human_notice(tmp_path, capsys):
    plan = write_plan(tmp_path, [entry("g1", "lower_third", {"name": "Doru", "role": "creator"}, False)])
    rc = graphics.main(["--plan", str(plan), "--out-dir", str(tmp_path / "out"), "--backend", "mock"])
    assert rc == 0
    assert "nothing rendered" in capsys.readouterr().out


def test_plan_bad_approved_entry_names_id(tmp_path, capsys):
    plan = write_plan(tmp_path, [entry("g9", "lower_third", {"name": "Doru"}, True)])
    rc = graphics.main(["--plan", str(plan), "--out-dir", str(tmp_path / "out"), "--backend", "mock"])
    assert rc == 2
    assert "g9" in capsys.readouterr().err


def test_plan_rejected_draft_props_not_validated(tmp_path, capsys):
    plan = write_plan(
        tmp_path,
        [
            entry("g1", "lower_third", {"name": "Doru", "role": "creator"}, True),
            entry("g2", "counter", {}, False),
        ],
    )
    rc = graphics.main(
        ["--plan", str(plan), "--out-dir", str(tmp_path / "out"), "--width", "320", "--height", "180", "--backend", "mock", "--json"]
    )
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in summary["outputs"]] == ["g1"]


def test_plan_missing_file_exits_2(tmp_path, capsys):
    rc = graphics.main(["--plan", str(tmp_path / "nope.json"), "--out-dir", str(tmp_path / "out"), "--backend", "mock"])
    assert rc == 2
    assert "nope.json" in capsys.readouterr().err


def test_plan_without_out_dir_exits_2(tmp_path, capsys):
    plan = write_plan(tmp_path, [entry("g1", "lower_third", {"name": "Doru", "role": "creator"}, True)])
    rc = graphics.main(["--plan", str(plan), "--backend", "mock"])
    assert rc == 2
    assert "out-dir" in capsys.readouterr().err


def test_plan_and_template_conflict_exits_2(tmp_path, capsys):
    plan = write_plan(tmp_path, [entry("g1", "lower_third", {"name": "Doru", "role": "creator"}, True)])
    rc = graphics.main(
        ["--plan", str(plan), "--out-dir", str(tmp_path / "out"), "--template", "lower_third", "--backend", "mock"]
    )
    assert rc == 2
    assert "cannot be combined" in capsys.readouterr().err


def hyperframes_available():
    if shutil.which("node") is None or shutil.which("npx") is None:
        return False
    try:
        result = subprocess.run(
            ["npx", "--yes", f"hyperframes@{graphics.HYPERFRAMES_VERSION}", "--version"],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


@pytest.mark.skipif(not hyperframes_available(), reason="HyperFrames CLI is not available")
def test_hyperframes_render_has_alpha(tmp_path, capsys):
    out = tmp_path / "lt.mov"
    rc = graphics.main(
        [
            "--template",
            "lower_third",
            "--props",
            json.dumps({"name": "Doru", "role": "creator"}),
            "--out",
            str(out),
            "--width",
            "640",
            "--height",
            "360",
            "--duration",
            "1",
            "--json",
        ]
    )
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["backend"] == "hyperframes"
    assert graphics.probe_pix_fmt(out) in ALPHA_PIX_FMTS
