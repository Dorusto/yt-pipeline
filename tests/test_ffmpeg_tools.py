import pytest

import common.ffmpeg_tools as ff


def test_pick_prefers_nvenc_when_preferred():
    enc = ff.pick_video_encoder(
        available={"h264_nvenc", "h264_videotoolbox", "libx264"}, prefer="h264_nvenc"
    )
    assert enc == "h264_nvenc"


def test_pick_prefers_videotoolbox_when_preferred():
    enc = ff.pick_video_encoder(
        available={"h264_nvenc", "h264_videotoolbox", "libx264"}, prefer="h264_videotoolbox"
    )
    assert enc == "h264_videotoolbox"


def test_pick_falls_back_to_libx264_when_preferred():
    enc = ff.pick_video_encoder(
        available={"h264_nvenc", "h264_videotoolbox", "libx264"}, prefer="libx264"
    )
    assert enc == "libx264"


def test_pick_uses_the_only_available_encoder():
    enc = ff.pick_video_encoder(available={"h264_nvenc"}, prefer="libx264")
    assert enc == "h264_nvenc"


def test_pick_missing_preferred_encoder_falls_back_to_libx264():
    enc = ff.pick_video_encoder(available={"libx264"}, prefer="h264_videotoolbox")
    assert enc == "libx264"


def test_pick_raises_without_encoders():
    with pytest.raises(RuntimeError):
        ff.pick_video_encoder(available=set(), prefer="libx264")


def test_auto_prefers_nvenc_with_an_nvidia_gpu(monkeypatch):
    monkeypatch.setattr(ff, "has_nvidia_gpu", lambda: True)
    monkeypatch.setattr(ff.sys, "platform", "linux")
    enc = ff.pick_video_encoder(
        available={"h264_nvenc", "h264_videotoolbox", "libx264"}
    )
    assert enc == "h264_nvenc"


def test_auto_prefers_videotoolbox_on_macos(monkeypatch):
    monkeypatch.setattr(ff, "has_nvidia_gpu", lambda: False)
    monkeypatch.setattr(ff.sys, "platform", "darwin")
    assert ff.preferred_video_encoder() == "h264_videotoolbox"
    enc = ff.pick_video_encoder(
        available={"h264_nvenc", "h264_videotoolbox", "libx264"}
    )
    assert enc == "h264_videotoolbox"


def test_auto_prefers_libx264_on_linux_without_gpu(monkeypatch):
    monkeypatch.setattr(ff, "has_nvidia_gpu", lambda: False)
    monkeypatch.setattr(ff.sys, "platform", "linux")
    assert ff.preferred_video_encoder() == "libx264"
    enc = ff.pick_video_encoder(
        available={"h264_nvenc", "h264_videotoolbox", "libx264"}
    )
    assert enc == "libx264"


def test_auto_falls_back_to_libx264_when_the_hardware_encoder_is_missing(monkeypatch):
    monkeypatch.setattr(ff, "has_nvidia_gpu", lambda: False)
    monkeypatch.setattr(ff.sys, "platform", "darwin")
    assert ff.pick_video_encoder(available={"libx264"}) == "libx264"


def test_video_encode_args_libx264():
    args = ff.video_encode_args("libx264")
    assert "-crf" in args and "libx264" in args


def test_video_encode_args_libx264_honours_crf_and_preset():
    args = ff.video_encode_args("libx264", crf=18, preset="veryfast")
    assert args[args.index("-crf") + 1] == "18"
    assert args[args.index("-preset") + 1] == "veryfast"


def test_video_encode_args_nvenc():
    args = ff.video_encode_args("h264_nvenc")
    assert "h264_nvenc" in args and "-cq" in args


def test_video_encode_args_videotoolbox_uses_a_bitrate():
    args = ff.video_encode_args("h264_videotoolbox")
    assert "h264_videotoolbox" in args and "-b:v" in args
    assert "-cq" not in args and "-q:v" not in args


def test_video_encode_args_unknown_encoder_passthrough():
    assert ff.video_encode_args("something_else") == ["-c:v", "something_else"]


def test_this_machine_has_a_usable_encoder():
    assert "libx264" in ff.available_video_encoders()
