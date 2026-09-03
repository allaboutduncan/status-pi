"""Colours for the panel, and the dimmer used for quiet hours."""

from __future__ import annotations

RGB = tuple

#: panel background -- not quite black, which reads as less harsh on an LCD
BACKGROUND = (10, 10, 14)  # #0a0a0e
BLACK = (0, 0, 0)
WHITE = (232, 232, 232)  # #e8e8e8

BUSY = (255, 42, 32)  # #ff2a20
FREE = (46, 230, 96)  # #2ee660
CUSTOM = (255, 136, 0)  # #ff8800
TIMER = (80, 190, 255)  # #50beff
INFO = (150, 158, 170)  # #969eaa

#: header health indicators
HEALTH_OK = FREE
HEALTH_BAD = (255, 136, 136)  # #ff8888

# Unlit dots: visible enough to read as a physical matrix, dark enough not to
# glow across a dark room.
OFF_DOT = (26, 26, 30)

NAMED = {
    "red": BUSY,
    "green": FREE,
    "amber": CUSTOM,
    "orange": CUSTOM,
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


def blend(color, other, amount: float):
    """Mix `color` towards `other`.  Used for the pulse, which is opacity in
    the design and therefore a blend towards the background here."""
    amount = max(0.0, min(1.0, amount))
    return tuple(
        int(round(c * (1 - amount) + o * amount)) for c, o in zip(color, other)
    )
