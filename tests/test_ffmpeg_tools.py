import pytest

import common.ffmpeg_tools as ff


def test_pick_prefers_nvenc_when_gpu_present():
    enc = ff.pick_video_encoder(available={"h264_nvenc", "libx264"}, prefer_nvenc=True)
    assert enc == "h264_nvenc"


def test_pick_falls_back_to_libx264_without_gpu():
    enc = ff.pick_video_encoder(available={"h264_nvenc", "libx264"}, prefer_nvenc=False)
    assert enc == "libx264"


def test_pick_uses_nvenc_only_available():
    enc = ff.pick_video_encoder(available={"h264_nvenc"}, prefer_nvenc=False)
    assert enc == "h264_nvenc"


def test_pick_raises_without_encoders():
    with pytest.raises(RuntimeError):
        ff.pick_video_encoder(available=set(), prefer_nvenc=False)


def test_video_encode_args_libx264():
    assert "-crf" in ff.video_encode_args("libx264")


def test_video_encode_args_nvenc():
    args = ff.video_encode_args("h264_nvenc")
    assert "h264_nvenc" in args and "-cq" in args


def test_video_encode_args_unknown_encoder_passthrough():
    assert ff.video_encode_args("something_else") == ["-c:v", "something_else"]


def test_this_machine_has_a_usable_encoder():
    assert "libx264" in ff.available_video_encoders()