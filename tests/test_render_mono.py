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
    CONTEXT_MAX_LINES, CONTEXT_MAX_WIDTH, CONTEXT_TRACK, HEADER_SIZE,
    HEADLINE_SIZE, HEADLINE_TRACK, MonoRenderer, PADDING, ellipsize,
    fit_headline, load, pulse_amount, tracked_width, wrap_lines,
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


def test_content_stays_inside_the_16px_padding():
    """Glyph origins sit on the padding box; ink can start a pixel inside it
    because of the leading glyph's side bearing, exactly as it would in CSS."""
    state = make_state()
    state.subline = "timer 12:34"
    box = ink_box(render(state))
    assert PADDING <= box[0] <= PADDING + 2
    assert PADDING <= box[1] <= PADDING + 2
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


def test_nothing_scrolls_in_this_style():
    """No marquee, so the tick loop stays at 1Hz."""
    renderer = MonoRenderer(config())
    assert renderer.scrolling is False
    assert renderer.animating is False


def test_ellipsize_trims_to_fit():
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


# -- the header row, doubled from the design's 11px ------------------------

def test_header_is_twice_the_designs_size():
    assert HEADER_SIZE == 22


def test_header_still_fits_with_seconds_showing():
    """The widest the row gets: an 8-character clock, a long date and the two
    indicators, all on one line."""
    font = load(HEADER_SIZE)
    clock = tracked_width("09:41:30", font, 2)
    date = tracked_width("Wed 30 Sep", font, 2)
    dots = 12 * 2 + 12
    assert clock + date + dots < 480 - 2 * PADDING


# -- meeting titles wrap rather than being cut ----------------------------

def wrap(text, max_width=CONTEXT_MAX_WIDTH, lines=CONTEXT_MAX_LINES):
    return wrap_lines(text, load(16), CONTEXT_TRACK, max_width, lines)


def test_a_title_that_fits_stays_on_one_line():
    assert wrap("Sprint Review - until 10:30") == ["Sprint Review - until 10:30"]


def test_a_long_title_wraps_instead_of_truncating():
    """The whole point: 'Quarterly planning with the platform team' says
    nothing useful once it is cut to 'Quarterly planning with the...'."""
    lines = wrap("Quarterly planning with the platform team - until 16:00")
    assert len(lines) == 2
    assert "".join(lines).replace("- ", "").count("platform team") == 1
    assert "16:00" in lines[-1], "the end time survives the wrap"


def test_wrapping_never_exceeds_the_line_limit_or_the_width():
    font = load(16)
    text = ("next 09:00 Incident review and postmortem for the outage last "
            "Thursday afternoon in the main conference room")
    lines = wrap(text)
    assert len(lines) <= CONTEXT_MAX_LINES
    assert all(tracked_width(l, font, CONTEXT_TRACK) <= CONTEXT_MAX_WIDTH for l in lines)
    assert lines[-1].endswith("…"), "what did not fit is marked as cut"


def test_a_separator_is_never_left_dangling():
    """'...platform team -' / 'until 16:00' reads badly; the dash belongs
    with the words it introduces."""
    lines = wrap("Quarterly planning with the platform team - until 16:00")
    assert not lines[0].rstrip().endswith("-")
    assert lines[1].startswith("-")


def test_an_unbroken_word_is_split_rather_than_looping():
    font = load(16)
    lines = wrap("Supercalifragilisticexpialidocious" * 4)
    assert 0 < len(lines) <= CONTEXT_MAX_LINES
    assert all(tracked_width(l, font, CONTEXT_TRACK) <= CONTEXT_MAX_WIDTH for l in lines)


def test_empty_context_produces_no_lines():
    assert wrap("") == [] and wrap("   ") == []


def ink_bands(img):
    """Contiguous runs of rows containing ink -- one per line of text."""
    bg = Image.new("RGB", img.size, palette.BACKGROUND)
    mask = ImageChops.difference(img.convert("RGB"), bg).convert("L")
    mask = mask.point(lambda v: 255 if v > 8 else 0)
    lit = [mask.crop((0, y, img.width, y + 1)).getbbox() is not None
           for y in range(img.height)]
    bands, start = [], None
    for y, on in enumerate(lit + [False]):
        if on and start is None:
            start = y
        elif not on and start is not None:
            bands.append((start, y))
            start = None
    return bands


def test_a_wrapped_title_is_drawn_on_two_lines():
    """Measured on the pixels, not just the wrap helper: header, headline and
    one context line makes three bands of ink; a wrapped title makes four."""
    short = make_state()
    short.context = "Sprint Review - until 10:30"
    tall = make_state()
    tall.context = "Quarterly planning with the platform team - until 16:00"
    assert len(wrap(tall.context)) == 2
    assert len(ink_bands(render(short))) == 3
    assert len(ink_bands(render(tall))) == 4


def test_a_wrapped_title_still_fits_the_panel():
    state = make_state()
    state.context = "Quarterly planning with the platform team - until 16:00"
    state.subline = "timer 12:34"
    box = ink_box(render(state))
    assert box[3] <= 320 - PADDING
    assert box[1] >= PADDING
