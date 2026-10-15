

def test_sharpness_drops_when_a_photo_is_blurred(tmp_path):
    import numpy as np
    from PIL import Image, ImageFilter

    from common.photos import sharpness

    rng = np.random.default_rng(3)
    image = Image.fromarray(rng.integers(0, 255, size=(300, 400, 3), dtype=np.uint8))
    image.save(tmp_path / "crisp.jpg")
    image.filter(ImageFilter.GaussianBlur(4)).save(tmp_path / "soft.jpg")
    assert sharpness(tmp_path / "soft.jpg") < sharpness(tmp_path / "crisp.jpg") / 5
