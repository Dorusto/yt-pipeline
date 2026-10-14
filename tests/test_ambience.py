from pathlib import Path

import pytest

from common import ambience


def test_resolve_denoiser_auto_uses_model_when_present(tmp_path):
    model = tmp_path / "model.rnnn"
    model.write_text("rnnoise-nu model file version 1\n", encoding="utf-8")
    denoiser, warnings = ambience.resolve_denoiser(None, model)
    assert denoiser == "arnndn"
    assert warnings == []


def test_resolve_denoiser_auto_falls_back_to_afftdn(tmp_path):
    denoiser, warnings = ambience.resolve_denoiser(None, tmp_path / "missing.rnnn")
    assert denoiser == "afftdn"
    assert warnings and "afftdn" in warnings[0]


def test_resolve_denoiser_arnndn_without_model_is_an_error(tmp_path):
    with pytest.raises(ambience.AmbienceError):
        ambience.resolve_denoiser("arnndn", tmp_path / "missing.rnnn")


def test_resolve_denoiser_explicit_choices(tmp_path):
    assert ambience.resolve_denoiser("afftdn", tmp_path / "missing.rnnn") == ("afftdn", [])
    assert ambience.resolve_denoiser("none", tmp_path / "missing.rnnn") == ("none", [])


def test_build_filters_full_chain(tmp_path):
    model = tmp_path / "model.rnnn"
    model.write_text("x", encoding="utf-8")
    filters, applied = ambience.build_filters(
        "arnndn", model, 150.0, apply_highpass=True, apply_loudnorm=True, target_lufs=-16.0
    )
    assert applied == ["highpass", "arnndn", "loudnorm"]
    assert filters[0] == "highpass=f=150"
    assert filters[1].startswith("arnndn=m=")
    assert filters[2].startswith("loudnorm=I=-16")


def test_build_filters_without_highpass_and_loudnorm(tmp_path):
    filters, applied = ambience.build_filters(
        "afftdn", tmp_path / "missing.rnnn", 150.0, apply_highpass=False, apply_loudnorm=False, target_lufs=-16.0
    )
    assert applied == ["afftdn"]
    assert filters == ["afftdn=nr=12"]


def test_escape_filter_value():
    assert ambience.escape_filter_value("a:b,c'd\\e") == "a\\:b\\,c\\'d\\\\e"
