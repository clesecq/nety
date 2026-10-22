from nety.draw import render_png


def test_render_crops_to_ink_on_transparent_background():
    image = render_png([[(100, 100), (200, 150)]], size=(500, 250), width=3)
    assert image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0
    # The diagonal stroke crosses the middle of the cropped image.
    assert image.getpixel((image.width // 2, image.height // 2))[3] > 0
    assert 100 <= image.width <= 120 and 50 <= image.height <= 70


def test_render_single_click_is_a_dot():
    assert render_png([[(10, 10)]]) is not None


def test_render_without_ink():
    assert render_png([]) is None
