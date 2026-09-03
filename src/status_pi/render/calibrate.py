"""A ruler for the panel, for finding what the bezel covers.

The framebuffer is the full 480x320, but the plastic frame overlaps a few
millimetres of glass -- unevenly, and differently on every unit. Rather than
guess, draw nested rectangles at known insets and read off the first one that
is completely visible.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from . import palette
from .fonts import load

#: insets to draw, in pixels
STEPS = (0, 5, 10, 15, 20, 30)

COLOURS = (
    (255, 42, 32),    # 0  - the very edge
    (255, 136, 0),    # 5
    (255, 214, 0),    # 10
    (46, 230, 96),    # 15
    (80, 190, 255),   # 20
    (190, 140, 255),  # 30
)


def test_pattern(width: int = 480, height: int = 320) -> Image.Image:
    """Nested labelled rectangles, plus edge markers for an uneven bezel."""
    img = Image.new("RGB", (width, height), palette.BLACK)
    draw = ImageDraw.Draw(img)
    label = load(13, bold=True)
    small = load(11)

    for index, (inset, colour) in enumerate(zip(STEPS, COLOURS)):
        draw.rectangle((inset, inset, width - 1 - inset, height - 1 - inset),
                       outline=colour)
        text = str(inset)
        # Labels are staggered along each edge rather than stacked in the
        # corners, or the tightest few -- exactly the ones being measured --
        # would overlap into an unreadable smear.
        down = 44 + index * 30
        across = 74 + index * 52
        draw.text((inset + 3, down), text, font=label, fill=colour, anchor="lm")
        draw.text((width - inset - 4, down), text, font=label, fill=colour,
                  anchor="rm")
        draw.text((across, inset + 2), text, font=label, fill=colour, anchor="mt")
        draw.text((across, height - inset - 3), text, font=label, fill=colour,
                  anchor="ms")

    centre_y = height // 2
    draw.line((0, centre_y, width, centre_y), fill=(60, 62, 70))
    draw.line((width // 2, 0, width // 2, height), fill=(60, 62, 70))

    lines = [
        "PANEL RULER",
        "Read the smallest number fully visible",
        "on each edge -- that is its margin.",
    ]
    y = centre_y - 26
    for index, line in enumerate(lines):
        font = label if index == 0 else small
        draw.text((width // 2, y), line, font=font, fill=palette.WHITE,
                  anchor="mt")
        y += 18 if index == 0 else 14
    return img
