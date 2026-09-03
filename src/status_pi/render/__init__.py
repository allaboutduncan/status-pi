"""Rendering: pick a panel style and hand back something with .render()."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

STYLES = ("mono", "matrix")


def make_renderer(config):
    """Build the renderer named by `display.style`.

    Both styles take a DisplayState and return a 480x320 image; the app does
    not care which one it is holding.
    """
    style = (getattr(config.display, "style", "mono") or "mono").lower()
    if style not in STYLES:
        log.warning("unknown display style %r, using mono", style)
        style = "mono"
    if style == "matrix":
        from .screens import Renderer

        return Renderer(config)
    from .mono import MonoRenderer

    return MonoRenderer(config)
