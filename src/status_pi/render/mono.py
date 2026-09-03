"""The typographic panel style: Roboto Mono on a near-black ground.

A direct port of the Claude design (Status Pi Display.dc.html): a flex column
with 16px padding -- muted header row, a huge centred status word, a meeting
line under it, and a small tracked subline pinned to the bottom.

Two things CSS gives free and Pillow does not, so they are done by hand here:
letter-spacing (drawn glyph by glyph) and flexbox space-between (measured and
positioned).
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from . import palette
from .fonts import load

PADDING = 16

#: sizes and tracking from the design, except the header row: the design's
#: 11px was too small to read across a room, so the whole row is doubled.
HEADER_SIZE, HEADER_TRACK = 22, 2
HEADLINE_SIZE, HEADLINE_TRACK = 80, 4
#: 80px is the design size and the maximum; a longer custom status steps
#: down through these rather than losing its second half to an ellipsis.
HEADLINE_SIZES = (80, 64, 52, 44, 36, 30, 26)
CONTEXT_SIZE, CONTEXT_TRACK = 16, 0
SUBLINE_SIZE, SUBLINE_TRACK = 13, 2

HEADLINE_GAP = 12  # margin-bottom on the status word
CONTEXT_MAX_WIDTH = 440
#: a meeting title too wide for one line wraps rather than being cut short
CONTEXT_MAX_LINES = 2
CONTEXT_LINE_HEIGHT = round(CONTEXT_SIZE * 1.35)
#: scaled with the header row so the indicators stay in proportion to it
DOT_SIZE, DOT_GAP = 12, 12

PULSE_PERIOD = 2.0  # seconds, matching `pulse 2s ease-in-out infinite`
PULSE_MIN_OPACITY = 0.7


def tracked_width(text: str, font, tracking: int) -> float:
    """Width of `text` including letter-spacing.

    CSS adds tracking after every character, including the last; that trailing
    space is what makes a tracked line look off-centre if you ignore it, so it
    is left out here before centring.
    """
    if not text:
        return 0.0
    return font.getlength(text) + tracking * (len(text) - 1)


def draw_tracked(draw, xy, text: str, font, fill, tracking: int = 0):
    """Draw text with letter-spacing, from a left/top anchor.

    Glyph origins are snapped to whole pixels: a browser can afford subpixel
    positioning, but on a 480x320 panel it just smears 11px type.
    """
    if not text:
        return
    x, y = xy
    y = round(y)
    if not tracking:
        draw.text((round(x), y), text, font=font, fill=fill, anchor="lt")
        return
    for char in text:
        draw.text((round(x), y), char, font=font, fill=fill, anchor="lt")
        x += font.getlength(char) + tracking


def ellipsize(text: str, font, tracking: int, max_width: float) -> str:
    """Trim to fit, the way `text-overflow: ellipsis` would."""
    if not text or tracked_width(text, font, tracking) <= max_width:
        return text
    ellipsis = "…"
    trimmed = text
    while trimmed and tracked_width(trimmed + ellipsis, font, tracking) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed.rstrip() + ellipsis) if trimmed else ellipsis


def headline_tracking(size: int) -> int:
    """The design's 4px at 80px is 0.05em, so it scales with the type."""
    return max(1, round(size * HEADLINE_TRACK / HEADLINE_SIZE))


def fit_headline(text: str, max_width: float):
    """Largest headline font at which `text` fits: (font, size, tracking).

    "BUSY" and "FREE" always land at the design's 80px; "BACK AT 3PM" gets
    smaller type instead of becoming "BACK AT...", which would throw away the
    part that matters.
    """
    for size in HEADLINE_SIZES:
        font = load(size, bold=True)
        tracking = headline_tracking(size)
        if tracked_width(text, font, tracking) <= max_width:
            return font, size, tracking
    size = HEADLINE_SIZES[-1]
    return load(size, bold=True), size, headline_tracking(size)


def _is_separator(word: str) -> bool:
    """A token that is punctuation only, and so reads as belonging to the
    word after it rather than the one before."""
    return bool(word) and all(char in "-–—·:|/" for char in word)


def _split_word(word: str, font, tracking: int, max_width: float):
    """Break a single word too wide for any line into pieces that fit."""
    pieces, current = [], ""
    for char in word:
        if current and tracked_width(current + char, font, tracking) > max_width:
            pieces.append(current)
            current = char
        else:
            current += char
    if current:
        pieces.append(current)
    return pieces


def wrap_lines(text: str, font, tracking: int, max_width: float, max_lines: int):
    """Greedy word wrap, with the last line ellipsised if it still overflows.

    A long meeting title is worth a second line -- "Quarterly planning with
    the platform team" says nothing useful once it has been cut down to
    "Quarterly planning with the...".
    """
    words = (text or "").split()
    if not words:
        return []

    # A lone separator must not be left stranded at the end of a line
    # ("...platform team -" / "until 10:36"), so glue it to what follows.
    joined = []
    for word in words:
        if joined and _is_separator(joined[-1]):
            joined[-1] = "%s %s" % (joined[-1], word)
        else:
            joined.append(word)

    tokens = []
    for word in joined:
        if tracked_width(word, font, tracking) <= max_width:
            tokens.append(word)
        else:
            tokens.extend(_split_word(word, font, tracking, max_width))

    lines, current = [], ""
    while tokens:
        candidate = ("%s %s" % (current, tokens[0])) if current else tokens[0]
        if tracked_width(candidate, font, tracking) <= max_width:
            current = candidate
            tokens.pop(0)
            continue
        lines.append(current)
        current = ""
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
        tokens = []

    if tokens and lines:
        # Fold what is left back onto the last line so the ellipsis marks a
        # real truncation rather than a tidy-looking break.
        lines[-1] = ellipsize(" ".join([lines[-1]] + tokens), font, tracking, max_width)
    return lines


def pulse_amount(now: float) -> float:
    """How far to fade the headline towards the background right now.

    `ease-in-out` between full and 70% opacity; returns a blend amount, so 0
    is fully lit.
    """
    phase = (now % PULSE_PERIOD) / PULSE_PERIOD
    eased = 0.5 - 0.5 * math.cos(2 * math.pi * phase)
    return (1.0 - PULSE_MIN_OPACITY) * eased


class MonoRenderer:
    """Draws a DisplayState in the typographic style."""

    style = "mono"

    def __init__(self, config):
        self.config = config
        self.width = config.display.width
        self.height = config.display.height
        #: this style has no marquee, so nothing scrolls
        self.scrolling = False
        self._clock = 0.0

    # -- helpers -----------------------------------------------------------
    def _dim(self, color, state):
        return palette.dim(color, state.brightness)

    def tick(self, now: float) -> None:
        """Drive the pulse animation from the app's clock."""
        self._clock = now

    @property
    def animating(self) -> bool:
        return bool(self.config.display.pulse)

    # -- bands -------------------------------------------------------------
    def _draw_header(self, draw, state, top: int) -> int:
        font = load(HEADER_SIZE)
        muted = self._dim(palette.INFO, state)
        left, right = PADDING, self.width - PADDING

        clock_w = tracked_width(state.clock, font, HEADER_TRACK)
        date_w = tracked_width(state.date, font, HEADER_TRACK)
        dots_w = DOT_SIZE * 2 + DOT_GAP

        # flexbox space-between across three children
        slack = (right - left) - (clock_w + date_w + dots_w)
        gap = max(0.0, slack / 2)

        draw_tracked(draw, (left, top), state.clock, font, muted, HEADER_TRACK)
        draw_tracked(draw, (left + clock_w + gap, top), state.date, font, muted,
                     HEADER_TRACK)

        row_height = font.getbbox("0")[3]
        dot_top = top + (row_height - DOT_SIZE) / 2
        ha_ok = state.ha_ok and not state.mic_degraded
        for index, ok in enumerate((ha_ok, state.cal_ok)):
            colour = palette.HEALTH_OK if ok else palette.HEALTH_BAD
            x = right - dots_w + index * (DOT_SIZE + DOT_GAP)
            draw.ellipse((x, dot_top, x + DOT_SIZE - 1, dot_top + DOT_SIZE - 1),
                         fill=self._dim(colour, state))
        return top + int(row_height)

    def _draw_centre(self, draw, state, top: float, bottom: float) -> None:
        context_font = load(CONTEXT_SIZE)
        headline_font, headline_h, tracking = fit_headline(
            state.headline, self.width - 2 * PADDING)

        colour = self._dim(state.color, state)
        if self.config.display.pulse:
            colour = palette.blend(
                colour, self._dim(palette.BACKGROUND, state), pulse_amount(self._clock))

        # Nothing scrolls in this style: the headline shrinks to fit, and a
        # meeting title too wide for one line wraps onto a second.
        headline = ellipsize(state.headline, headline_font, tracking,
                             self.width - 2 * PADDING)
        lines = wrap_lines(state.context, context_font, CONTEXT_TRACK,
                           CONTEXT_MAX_WIDTH, CONTEXT_MAX_LINES)

        glyph_h = context_font.getbbox("Ag")[3]
        context_h = (len(lines) - 1) * CONTEXT_LINE_HEIGHT + glyph_h if lines else 0
        block_h = headline_h + (HEADLINE_GAP + context_h if lines else 0)
        y = top + ((bottom - top) - block_h) / 2

        x = (self.width - tracked_width(headline, headline_font, tracking)) / 2
        draw_tracked(draw, (x, y), headline, headline_font, colour, tracking)

        if lines:
            y += headline_h + HEADLINE_GAP
            white = self._dim(palette.WHITE, state)
            for line in lines:
                x = (self.width - tracked_width(line, context_font, CONTEXT_TRACK)) / 2
                draw_tracked(draw, (x, y), line, context_font, white, CONTEXT_TRACK)
                y += CONTEXT_LINE_HEIGHT

    def _draw_subline(self, draw, state, bottom: float) -> None:
        if not state.subline:
            return
        font = load(SUBLINE_SIZE)
        colour = palette.TIMER if "timer" in state.subline.lower() else palette.INFO
        text = ellipsize(state.subline, font, SUBLINE_TRACK, self.width - 2 * PADDING)
        height = font.getbbox("Ag")[3]
        x = (self.width - tracked_width(text, font, SUBLINE_TRACK)) / 2
        draw_tracked(draw, (x, bottom - height), text, font,
                     self._dim(colour, state), SUBLINE_TRACK)

    # -- entry point -------------------------------------------------------
    def render(self, state) -> Image.Image:
        img = Image.new("RGB", (self.width, self.height),
                        self._dim(palette.BACKGROUND, state))
        draw = ImageDraw.Draw(img)

        if state.clock_only:
            font = load(HEADLINE_SIZE, bold=True)
            x = (self.width - tracked_width(state.clock, font, HEADLINE_TRACK)) / 2
            y = (self.height - HEADLINE_SIZE) / 2
            draw_tracked(draw, (x, y), state.clock, font,
                         self._dim(palette.WHITE, state), HEADLINE_TRACK)
        else:
            header_bottom = self._draw_header(draw, state, PADDING)
            subline_font = load(SUBLINE_SIZE)
            subline_h = subline_font.getbbox("Ag")[3] if state.subline else 0
            bottom = self.height - PADDING
            self._draw_centre(draw, state, header_bottom, bottom - subline_h)
            self._draw_subline(draw, state, bottom)

        if self.config.display.rotate == 180:
            img = img.rotate(180)
        return img
