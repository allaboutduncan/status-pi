"""Bezel margins.

The framebuffer is the full 480x320, but the plastic frame overlaps part of
the glass and does so unevenly, so content near an edge can be hidden. These
insets shift the whole layout inward by a measured amount.
"""

from datetime import datetime, timezone

import pytest
from PIL import Image, ImageChops

from status_pi.config import Config
from status_pi.render import make_renderer, palette
from status_pi.render.calibrate import STEPS
from status_pi.render.calibrate import test_pattern as build_ruler
from status_pi.render.mono import PADDING
from status_pi.runtime import RuntimeState
from status_pi import state as S

NOW = datetime(2026, 9, 3, 9, 41, tzinfo=timezone.utc)


def render(style="mono", **margins):
    config = Config.from_dict({"display": dict(margins, style=style)})
    display = S.compute(NOW, config=config, runtime=RuntimeState(), events=[],
                        mic_on=True, ha_connected=True, cal_ok=True)
    display.context = "Sprint Review - until 10:30"
    display.subline = "timer 12:34"
    return make_renderer(config).render(display)


def ink_box(img, background=None):
    bg = Image.new("RGB", img.size, background or palette.BACKGROUND)
    diff = ImageChops.difference(img.convert("RGB"), bg).convert("L")
    return diff.point(lambda v: 255 if v > 8 else 0).getbbox()


def test_default_is_unchanged():
    """No margins configured must look exactly as it did before they existed."""
    config = Config.from_dict({})
    assert make_renderer(config).box == (PADDING, PADDING, 480 - PADDING, 320 - PADDING)


def test_a_left_margin_moves_content_right():
    """The reported bug: the clock's first digit sat under the frame."""
    before = ink_box(render())[0]
    after = ink_box(render(margin_left=20))[0]
    assert after - before == 20


@pytest.mark.parametrize("side,index,sign", [
    ("margin_left", 0, 1), ("margin_top", 1, 1),
    ("margin_right", 2, -1), ("margin_bottom", 3, -1),
])
def test_each_side_moves_independently(side, index, sign):
    before = ink_box(render())[index]
    after = ink_box(render(**{side: 12}))[index]
    assert (after - before) * sign == 12


def test_content_is_centred_in_the_visible_area_not_the_framebuffer():
    """With an uneven bezel the two are different, and centring on the
    framebuffer would leave the headline visibly off-centre on the glass."""
    renderer = make_renderer(Config.from_dict({"display": {"margin_left": 40}}))
    assert renderer.centre_x == (40 + PADDING + (480 - PADDING)) / 2
    box = ink_box(render(margin_left=40))
    headline_centre = (box[0] + box[2]) / 2
    assert abs(headline_centre - renderer.centre_x) < 12


def test_margins_never_push_content_off_the_panel():
    img = render(margin_left=30, margin_right=30, margin_top=20, margin_bottom=20)
    box = ink_box(img)
    assert box[0] >= PADDING + 30
    assert box[2] <= 480 - PADDING - 30
    assert box[1] >= PADDING + 20 and box[3] <= 320 - PADDING - 20


def test_negative_margins_are_ignored():
    assert make_renderer(Config.from_dict({"display": {"margin_left": -50}})).box[0] == PADDING


def test_the_matrix_style_uses_the_same_margins():
    """Switching style must not put the content back under the frame."""
    before = ink_box(render(style="matrix"), palette.BLACK)[0]
    after = ink_box(render(style="matrix", margin_left=20), palette.BLACK)[0]
    assert after > before


# -- the ruler used to measure all this ------------------------------------

def test_the_ruler_draws_every_step_to_the_very_edge():
    img = build_ruler()
    assert img.size == (480, 320)
    assert ink_box(img, palette.BLACK)[:2] == (0, 0), "the 0 ring reaches the edge"


def test_the_ruler_covers_a_plausible_bezel():
    assert STEPS[0] == 0 and max(STEPS) >= 20
