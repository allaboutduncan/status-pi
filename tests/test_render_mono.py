"""The typographic panel style, ported from the Claude design.

Where the design is specific -- 16px padding, 80px bold headline, 4px
tracking, #0a0a0e ground -- these tests pin those numbers, because they are
the difference between "looks like the design" and "roughly similar".
"""

from datetime import datetime, timezone

from PIL import Image, ImageChops

from status_pi.config import Config
from status_pi.render import make_renderer, palette
from status_pi.render.mono import (
    HEADLINE_SIZE, HEADLINE_TRACK, MonoRenderer, PADDING, ellipsize,
    fit_headline, load, pulse_amount, tracked_width,
)
from status_pi.render.screens import Renderer as MatrixRenderer
from status_pi.runtime import RuntimeState
from status_pi import state as S

NOW = datetime(2026, 9, 1, 9, 41, tzinfo=timezone.utc)


def config(**display):
    return Config.from_dict({"display": display} if display else {})


def render(state, **display):
    return MonoRenderer(config(**display)).render(state)


def make_state(**kwargs):
    kwargs.setdefault("mic_on", True)
    kwargs.setdefault("ha_connected", True)
    kwargs.setdefault("cal_ok", True)
    kwargs.setdefault("events", [])
    when = kwargs.pop("when", NOW)
    return S.compute(when, config=config(), runtime=RuntimeState(), **kwargs)


def ink_box(img):
    """Bounding box of everything that is not the background."""
    bg = Image.new("RGB", img.size, palette.BACKGROUND)
    diff = ImageChops.difference(img.convert("RGB"), bg).convert("L")
    return diff.point(lambda v: 255 if v > 8 else 0).getbbox()


def test_default_style_is_the_design():
    assert config().display.style == "mono"
    assert isinstance(make_renderer(config()), MonoRenderer)
    assert isinstance(make_renderer(config(style="matrix")), MatrixRenderer)
    assert isinstance(make_renderer(config(style="nonsense")), MonoRenderer)


def test_background_is_the_design_ground():
    img = render(make_state())
    assert img.getpixel((0, 0)) == palette.BACKGROUND == (10, 10, 14)


def test_padding_is_16px_on_every_side():
    state = make_state()
    state.subline = "timer 12:34"
    box = ink_box(render(state))
    assert box[0] == PADDING and box[1] == PADDING
    assert box[2] <= 480 - PADDING and box[3] <= 320 - PADDING


def test_busy_and_free_get_the_full_80px():
    """The whole point of the panel is readability across a room, so the two
    words that matter must never shrink."""
    for word in ("BUSY", "FREE"):
        font, size, tracking = fit_headline(word, 480 - 2 * PADDING)
        assert (size, tracking) == (HEADLINE_SIZE, HEADLINE_TRACK)


def test_long_status_shrinks_rather_than_truncating():
    """'BACK AT 3PM' at a fixed 80px would render as 'BACK AT...', throwing
    away the half that carries the information."""
    width = 480 - 2 * PADDING
    font, size, tracking = fit_headline("BACK AT 3PM", width)
    assert size < HEADLINE_SIZE
    assert tracked_width("BACK AT 3PM", font, tracking) <= width
    assert ellipsize("BACK AT 3PM", font, tracking, width) == "BACK AT 3PM"


def test_the_longest_allowed_status_still_fits():
    """The web UI caps a custom status at 24 characters; nothing inside that
    limit should ever need an ellipsis."""
    width = 480 - 2 * PADDING
    text = "W" * 24
    font, _, tracking = fit_headline(text, width)
    assert tracked_width(text, font, tracking) <= width


def test_status_colour_is_used_for_the_headline():
    img = render(make_state(mic_on=True))
    colours = dict((c, n) for n, c in img.getcolors(maxcolors=1 << 16))
    assert colours.get(palette.BUSY, 0) > 500


def test_health_dots_show_red_when_the_mic_signal_is_missing():
    healthy = render(make_state(mic_on=True))
    degraded = render(make_state(mic_on=None, ha_connected=False))
    healthy_colours = dict((c, n) for n, c in healthy.getcolors(maxcolors=1 << 16))
    degraded_colours = dict((c, n) for n, c in degraded.getcolors(maxcolors=1 << 16))
    assert palette.HEALTH_BAD not in healthy_colours
    assert palette.HEALTH_BAD in degraded_colours


def test_context_is_ellipsised_not_scrolled():
    """This style has no marquee, so the tick loop must stay at 1Hz."""
    renderer = MonoRenderer(config())
    assert renderer.scrolling is False
    assert renderer.animating is False

    font = load(16)
    long_title = "A very long meeting title that will not fit across the panel"
    trimmed = ellipsize(long_title, font, 0, 440)
    assert trimmed.endswith("…") and len(trimmed) < len(long_title)
    assert tracked_width(trimmed, font, 0) <= 440


def test_short_text_is_left_alone_by_ellipsize():
    font = load(16)
    assert ellipsize("Standup", font, 0, 440) == "Standup"
    assert ellipsize("", font, 0, 440) == ""


def test_quiet_hours_dim_everything():
    night = make_state(when=NOW.replace(hour=23))
    img = render(night)
    colours = dict((c, n) for n, c in img.getcolors(maxcolors=1 << 16))
    assert palette.BUSY not in colours
    assert palette.dim(palette.BUSY, night.brightness) in colours
    assert img.getpixel((0, 0)) == palette.dim(palette.BACKGROUND, night.brightness)


def test_clock_only_mode_draws_just_the_time():
    state = make_state(when=NOW.replace(hour=23))
    state.clock_only = True
    img = render(state)
    colours = dict((c, n) for n, c in img.getcolors(maxcolors=1 << 16))
    assert palette.dim(palette.BUSY, state.brightness) not in colours
    box = ink_box(img)
    assert box[1] > 100, "the clock is centred, not up in the header"


def test_pulse_stays_within_the_designs_opacity_range():
    """`pulse 2s ease-in-out` between 1 and 0.7 opacity."""
    amounts = [pulse_amount(t / 20) for t in range(41)]
    assert min(amounts) == 0.0
    assert abs(max(amounts) - 0.3) < 1e-9
    assert pulse_amount(0.0) == pulse_amount(2.0), "period is 2s"


def test_pulse_is_off_by_default_and_costs_nothing():
    renderer = MonoRenderer(config())
    assert renderer.config.display.pulse is False
    assert renderer.animating is False


def test_pulse_when_enabled_changes_the_headline_over_time():
    renderer = MonoRenderer(config(pulse=True))
    state = make_state(mic_on=True)
    assert renderer.animating is True
    renderer.tick(0.0)
    lit = renderer.render(state)
    renderer.tick(1.0)  # half a period later: dimmest point
    faded = renderer.render(state)
    assert ImageChops.difference(lit, faded).getbbox() is not None
    lit_reds = dict((c, n) for n, c in lit.getcolors(maxcolors=1 << 16))
    assert palette.BUSY in lit_reds


def test_rotate_180_flips_the_panel():
    state = make_state()
    upright = render(state)
    flipped = render(state, rotate=180)
    assert ImageChops.difference(upright.rotate(180), flipped).getbbox() is None
