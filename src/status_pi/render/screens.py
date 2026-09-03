"""Compose the 480x320 panel image.

The whole screen is one uniform lattice of dots (120 x 80 cells at 4px pitch).
Big text is drawn the way a real LED matrix draws big text: the 5x7 glyph
bitmap is scaled up by an integer factor so each lit dot becomes a block of
lit dots.  Nothing here knows about framebuffers -- render() just returns an
image.
"""

from __future__ import annotations

from PIL import Image

from . import palette
from .matrix import DotGrid, Marquee, text_image, text_size

PITCH = 4  # base cell size in pixels -> 120 x 80 cells on a 480x320 panel
DOT = 3

# Vertical bands, in grid cells.
HEADER_ROW = 1
RULE_ROW = 9
HEADLINE_TOP = 12
HEADLINE_ROWS = 35
CONTEXT_TOP = 50
CONTEXT_ROWS = 14
SUBLINE_ROW = 68

HEADLINE_SCALES = (5, 4, 3, 2)
CONTEXT_SCALES = (2, 1)

#: 3x3 solid block / hollow ring, used as the health indicators
_OK_DOT = ((1, 1, 1), (1, 1, 1), (1, 1, 1))
_BAD_DOT = ((1, 1, 1), (1, 0, 1), (1, 1, 1))


def _bitmap(rows) -> Image.Image:
    img = Image.new("L", (len(rows[0]), len(rows)), 0)
    img.putdata([255 if v else 0 for row in rows for v in row])
    return img


def _scaled(img: Image.Image, scale: int) -> Image.Image:
    if scale <= 1:
        return img
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST)


def fit_scale(text: str, cols: int, scales, rows: int = 7):
    """Largest scale from `scales` at which `text` fits in `cols` cells.

    Returns (scale, width_in_cells, fits).  When nothing fits, the smallest
    scale is returned with fits=False so the caller can scroll it instead.
    """
    width = text_size(text, rows)[0]
    for scale in scales:
        if width * scale <= cols:
            return scale, width * scale, True
    smallest = scales[-1]
    return smallest, width * smallest, False


class Renderer:
    """Stateful because the marquees remember where they have scrolled to."""

    style = "matrix"

    def __init__(self, config):
        self.config = config
        self.width = config.display.width
        self.height = config.display.height
        self.grid = DotGrid((0, 0, self.width, self.height), PITCH, DOT)
        self.headline_marquee = Marquee(hold_ticks=10)
        self.context_marquee = Marquee(hold_ticks=10)
        self.subline_marquee = Marquee(hold_ticks=10)
        self._background = None

    # -- helpers -----------------------------------------------------------
    def _new_frame(self, state) -> Image.Image:
        img = Image.new("RGB", (self.width, self.height), palette.BLACK)
        off = palette.dim(palette.OFF_DOT, state.brightness)
        self.grid.draw_background(img, off)
        return img

    def _draw(self, img, text, color, *, row, scale=1, col=None, marquee=None,
              max_cols=None, rows=7):
        """Draw one line of matrix text, centred unless it has to scroll."""
        if not text:
            return
        max_cols = max_cols or self.grid.cols
        content = _scaled(text_image(text, rows=rows), scale)
        if marquee is not None and content.width > max_cols:
            marquee.set_text(text)
            offset = marquee.advance(content.width, max_cols)
            self.grid.draw(img, content, color, col=offset, row=row)
            return
        if marquee is not None:
            marquee.set_text(text)
            marquee.animating = False
        start = col if col is not None else self.grid.centre_col(content.width)
        self.grid.draw(img, content, color, col=max(0, start), row=row)

    # -- bands -------------------------------------------------------------
    def _draw_header(self, img, state):
        info = palette.dim(palette.INFO, state.brightness)
        white = palette.dim(palette.WHITE, state.brightness)
        self.grid.draw(img, text_image(state.clock), white, col=1, row=HEADER_ROW)

        indicators = 9  # cells reserved on the right for the two health dots
        date_cols = text_size(state.date)[0]
        self.grid.draw(
            img,
            text_image(state.date),
            info,
            col=self.grid.cols - indicators - 2 - date_cols,
            row=HEADER_ROW,
        )

        # Health: filled block = good, hollow ring = degraded/unconfigured.
        ha_ok = state.ha_ok and not state.mic_degraded
        self.grid.draw(
            img, _bitmap(_OK_DOT if ha_ok else _BAD_DOT),
            palette.dim(palette.FREE if ha_ok else palette.BUSY, state.brightness),
            col=self.grid.cols - 9, row=HEADER_ROW + 2,
        )
        self.grid.draw(
            img, _bitmap(_OK_DOT if state.cal_ok else _BAD_DOT),
            palette.dim(palette.FREE if state.cal_ok else palette.BUSY, state.brightness),
            col=self.grid.cols - 4, row=HEADER_ROW + 2,
        )

    def _draw_rule(self, img, state):
        rule = Image.new("L", (self.grid.cols, 1), 255)
        self.grid.draw(img, rule, palette.dim((60, 62, 70), state.brightness),
                       col=0, row=RULE_ROW)

    def _draw_headline(self, img, state):
        text = state.headline
        scale, width, fits = fit_scale(text, self.grid.cols, HEADLINE_SCALES)
        rows_tall = 7 * scale
        row = HEADLINE_TOP + max(0, (HEADLINE_ROWS - rows_tall) // 2)
        color = palette.dim(state.color, state.brightness)
        self._draw(img, text, color, row=row, scale=scale,
                   marquee=self.headline_marquee if not fits else None)

    def _draw_context(self, img, state):
        if not state.context:
            return
        scale, _, fits = fit_scale(state.context, self.grid.cols, CONTEXT_SCALES)
        rows_tall = 7 * scale
        row = CONTEXT_TOP + max(0, (CONTEXT_ROWS - rows_tall) // 2)
        color = palette.dim(palette.WHITE, state.brightness * 0.85)
        self._draw(img, state.context, color, row=row, scale=scale,
                   marquee=self.context_marquee)

    def _draw_subline(self, img, state):
        if not state.subline:
            return
        color = palette.dim(palette.TIMER if "timer" in state.subline.lower()
                            else palette.INFO, state.brightness)
        self._draw(img, state.subline, color, row=SUBLINE_ROW,
                   marquee=self.subline_marquee)

    def _draw_clock_only(self, img, state):
        """Quiet-hours minimal face: just the time, centred and dim."""
        scale, _, fits = fit_scale(state.clock, self.grid.cols, HEADLINE_SCALES)
        row = (self.grid.rows - 7 * scale) // 2
        self._draw(img, state.clock, palette.dim(palette.WHITE, state.brightness),
                   row=row, scale=scale, marquee=None if fits else self.headline_marquee)

    # -- entry point -------------------------------------------------------
    def render(self, state) -> Image.Image:
        img = self._new_frame(state)
        if state.clock_only:
            self._draw_clock_only(img, state)
        else:
            self._draw_header(img, state)
            self._draw_rule(img, state)
            self._draw_headline(img, state)
            self._draw_context(img, state)
            self._draw_subline(img, state)
        if self.config.display.rotate == 180:
            img = img.rotate(180)
        return img

    @property
    def scrolling(self) -> bool:
        return any(m.animating for m in
                   (self.headline_marquee, self.context_marquee, self.subline_marquee))

    @property
    def animating(self) -> bool:
        """This style animates only when text is too wide to sit still."""
        return self.scrolling

    def tick(self, now: float) -> None:
        """No time-based animation here; the marquees step per frame."""
