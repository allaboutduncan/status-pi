"""Dot-matrix text rendering.

Everything on screen is drawn as an LED matrix: a fixed grid of dots that are
either lit or unlit.  Text is first rasterised into a 1-pixel-per-dot bitmap,
then scaled up onto the grid, so the panel reads like a physical matrix rather
than a normal dashboard.

The hot path is deliberately cheap for a Pi Zero 2 W: per frame each region
costs one NEAREST resize, one mask multiply and two pastes, regardless of how
many dots are lit.
"""

from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw

from .. import font5x7

CHAR_SPACING = 1  # blank columns between glyphs


def text_size(text: str, rows: int = 7, spacing: int = CHAR_SPACING):
    """Size in dots of `text` rendered at `rows` tall."""
    text = font5x7.normalise(text)
    if not text:
        return (0, rows)
    cols = len(text) * font5x7.FONT_WIDTH + (len(text) - 1) * spacing
    return (cols, rows)


def text_image(text: str, rows: int = 7, spacing: int = CHAR_SPACING) -> Image.Image:
    """Rasterise `text` to an 'L' image, one pixel per dot, 255 where lit."""
    text = font5x7.normalise(text)
    cols, rows = text_size(text, rows, spacing)
    img = Image.new("L", (max(cols, 1), rows), 0)
    if not text:
        return img
    px = img.load()
    x = 0
    for char in text:
        for column in font5x7.glyph(char):
            for row in range(rows):
                if column >> row & 1:
                    px[x, row] = 255
            x += 1
        x += spacing
    return img


@lru_cache(maxsize=32)
def _dot_mask(pitch: int, dot: int, shape: str) -> Image.Image:
    """A single dot centred in its cell, as an 'L' tile."""
    tile = Image.new("L", (pitch, pitch), 0)
    draw = ImageDraw.Draw(tile)
    pad = (pitch - dot) / 2
    box = (pad, pad, pad + dot - 1, pad + dot - 1)
    if shape == "square":
        draw.rectangle(box, fill=255)
    else:
        draw.ellipse(box, fill=255)
    return tile


@lru_cache(maxsize=16)
def _grid_mask(cols: int, rows: int, pitch: int, dot: int, shape: str) -> Image.Image:
    """The full lattice of dot shapes for a region, used both to paint the
    unlit background and to punch lit text into dot shapes."""
    mask = Image.new("L", (cols * pitch, rows * pitch), 0)
    tile = _dot_mask(pitch, dot, shape)
    for row in range(rows):
        for col in range(cols):
            mask.paste(tile, (col * pitch, row * pitch))
    return mask


class DotGrid:
    """A rectangular region of the panel drawn as a grid of dots."""

    def __init__(self, box, pitch: int, dot: int | None = None, shape: str = "round"):
        self.box = box
        x0, y0, x1, y1 = box
        self.pitch = pitch
        self.dot = dot if dot is not None else max(2, int(pitch * 0.78))
        self.shape = shape
        self.cols = max(1, (x1 - x0) // pitch)
        self.rows = max(1, (y1 - y0) // pitch)
        # Centre the lattice in its box so leftover pixels split evenly.
        self.origin = (
            x0 + ((x1 - x0) - self.cols * pitch) // 2,
            y0 + ((y1 - y0) - self.rows * pitch) // 2,
        )

    @property
    def size(self):
        return (self.cols * self.pitch, self.rows * self.pitch)

    def mask(self) -> Image.Image:
        return _grid_mask(self.cols, self.rows, self.pitch, self.dot, self.shape)

    def draw_background(self, img: Image.Image, off_color) -> None:
        """Paint the unlit lattice.  One paste for the whole region."""
        if off_color:
            img.paste(off_color, self.origin, self.mask())

    def draw(self, img, content: Image.Image, on_color, col: int = 0, row: int = 0):
        """Blit a 1-pixel-per-dot `content` bitmap at dot coordinates."""
        if content.width == 0 or content.height == 0:
            return
        scaled = content.resize(
            (content.width * self.pitch, content.height * self.pitch), Image.NEAREST
        )
        layer = Image.new("L", self.size, 0)
        layer.paste(scaled, (col * self.pitch, row * self.pitch))
        img.paste(on_color, self.origin, ImageChops.multiply(layer, self.mask()))

    def draw_text(self, img, text, on_color, col: int = 0, row: int = 0, rows: int = 7):
        self.draw(img, text_image(text, rows=rows), on_color, col, row)

    def centre_col(self, width: int) -> int:
        return (self.cols - width) // 2

    def fits(self, text: str, rows: int = 7) -> bool:
        return text_size(text, rows)[0] <= self.cols


class Marquee:
    """Whole-dot horizontal scroller, the way a real matrix scrolls.

    Holds at each end for a beat so a title can actually be read, and reports
    whether it is currently animating so the app can pick its tick rate.
    """

    def __init__(self, gap: int = 6, hold_ticks: int = 12):
        self.gap = gap
        self.hold_ticks = hold_ticks
        self._text = None
        self._offset = 0
        self._hold = 0
        self.animating = False

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._offset = 0
            self._hold = self.hold_ticks

    def advance(self, content_cols: int, view_cols: int) -> int:
        """Step the scroll one tick and return the column offset to draw at."""
        span = content_cols - view_cols
        if span <= 0:
            self.animating = False
            return 0
        self.animating = True
        if self._hold > 0:
            self._hold -= 1
        else:
            self._offset += 1
            if self._offset > span:
                self._offset = 0
                self._hold = self.hold_ticks
            elif self._offset == span:
                self._hold = self.hold_ticks
        return -self._offset
