"""Colours for the panel, and the dimmer used for quiet hours."""

from __future__ import annotations

RGB = tuple

BLACK = (0, 0, 0)
WHITE = (235, 235, 235)

BUSY = (255, 42, 32)
FREE = (46, 230, 96)
CUSTOM = (255, 176, 32)
TIMER = (80, 190, 255)
INFO = (150, 158, 170)

# Unlit dots: visible enough to read as a physical matrix, dark enough not to
# glow across a dark room.
OFF_DOT = (26, 26, 30)

NAMED = {
    "red": BUSY,
    "green": FREE,
    "amber": CUSTOM,
    "blue": TIMER,
    "white": WHITE,
}


def dim(color, factor: float):
    """Scale a colour towards black. Quiet hours use this instead of the
    backlight, which is hard-wired on for the Waveshare (A) panel."""
    factor = max(0.0, min(1.0, factor))
    return tuple(int(round(c * factor)) for c in color)


def resolve(name: str, default=CUSTOM):
    return NAMED.get((name or "").lower(), default)
