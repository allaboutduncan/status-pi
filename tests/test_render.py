from PIL import Image

from status_pi.config import Config
from status_pi.render import palette
from status_pi.render.fb import Framebuffer, to_rgb565
from status_pi.render.matrix import DotGrid, Marquee, text_image, text_size
from status_pi.render.screens import PITCH, Renderer, fit_scale
from status_pi.runtime import RuntimeState
from status_pi import state as S

from datetime import datetime, timezone

NOW = datetime(2026, 9, 1, 9, 41, tzinfo=timezone.utc)


def test_rgb565_packing_is_little_endian():
    img = Image.new("RGB", (3, 1))
    img.putpixel((0, 0), (255, 0, 0))
    img.putpixel((1, 0), (0, 255, 0))
    img.putpixel((2, 0), (0, 0, 255))
    assert to_rgb565(img).hex() == "00f8e0071f00"


def test_only_changed_rows_are_written():
    """A full frame is ~150ms on this panel's SPI bus; the dirty-row diff is
    what keeps a ticking clock cheap."""
    fb = Framebuffer.__new__(Framebuffer)
    fb.width, fb.height, fb.row_bytes, fb.stride = 480, 320, 960, 960
    blank = Image.new("RGB", (480, 320))
    fb._previous = None
    assert fb._changed_rows(to_rgb565(blank)) == [(0, 320)]

    fb._previous = to_rgb565(blank)
    changed = blank.copy()
    for y in range(100, 140):
        for x in range(480):
            changed.putpixel((x, y), (255, 255, 255))
    assert fb._changed_rows(to_rgb565(changed)) == [(100, 140)]


def test_identical_frame_writes_nothing():
    fb = Framebuffer.__new__(Framebuffer)
    fb.width, fb.height, fb.row_bytes, fb.stride = 480, 320, 960, 960
    frame = to_rgb565(Image.new("RGB", (480, 320), (10, 20, 30)))
    fb._previous = frame
    assert fb._changed_rows(frame) == []


def test_text_measurement_matches_the_bitmap():
    assert text_size("BUSY") == (23, 7)
    assert text_image("BUSY").size == (23, 7)
    assert text_image("").size == (1, 7)


def test_big_words_fill_the_panel():
    """BUSY and FREE must land at the largest scale, or the wall-readability
    of the whole device is gone."""
    grid = DotGrid((0, 0, 480, 320), PITCH)
    for word in ("BUSY", "FREE"):
        scale, width, fits = fit_scale(word, grid.cols, (5, 4, 3, 2))
        assert (scale, fits) == (5, True)
        assert width <= grid.cols


def test_long_status_falls_back_to_scrolling():
    grid = DotGrid((0, 0, 480, 320), PITCH)
    _, _, fits = fit_scale("IN THE ZONE UNTIL FOUR", grid.cols, (5, 4, 3, 2))
    assert fits is False


def test_marquee_scrolls_then_returns():
    marquee = Marquee(hold_ticks=2)
    marquee.set_text("something long")
    offsets = [marquee.advance(content_cols=130, view_cols=120) for _ in range(40)]
    assert min(offsets) == -10, "scrolls exactly far enough to show the end"
    assert offsets[-1] <= 0 and marquee.animating


def test_marquee_is_still_when_text_fits():
    marquee = Marquee()
    marquee.set_text("short")
    assert marquee.advance(content_cols=30, view_cols=120) == 0
    assert marquee.animating is False


def test_dim_scales_towards_black():
    assert palette.dim((200, 100, 50), 0.5) == (100, 50, 25)
    assert palette.dim((200, 100, 50), 0) == (0, 0, 0)


def test_render_produces_a_full_panel_image():
    config = Config.from_dict({})
    renderer = Renderer(config)
    state = S.compute(NOW, config=config, runtime=RuntimeState(), events=[],
                      mic_on=True, ha_connected=True, cal_ok=True)
    img = renderer.render(state)
    assert img.size == (480, 320)
    colours = dict((c, n) for n, c in img.getcolors(maxcolors=1 << 16))
    assert colours.get(palette.BUSY, 0) > 1000, "BUSY is drawn in red"
    assert colours.get(palette.OFF_DOT, 0) > 1000, "unlit lattice is visible"


def test_quiet_hours_render_is_dimmer():
    config = Config.from_dict({})
    renderer = Renderer(config)
    night = S.compute(NOW.replace(hour=23), config=config, runtime=RuntimeState(),
                      events=[], mic_on=True, ha_connected=True, cal_ok=True)
    img = renderer.render(night)
    assert palette.BUSY not in dict((c, n) for n, c in img.getcolors(maxcolors=1 << 16))
    assert palette.dim(palette.BUSY, night.brightness) in \
        dict((c, n) for n, c in img.getcolors(maxcolors=1 << 16))
