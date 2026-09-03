"""Font loading for the typographic panel style.

Roboto Mono is bundled (SIL Open Font License, see fonts/OFL.txt) so the
device does not need a working network or a particular apt package to draw
its own screen.  If the bundled files are ever missing we fall back to a
system monospace face rather than refusing to render.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

log = logging.getLogger(__name__)

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

REGULAR = "RobotoMono-Regular.ttf"
BOLD = "RobotoMono-Bold.ttf"

#: monospace faces commonly present on Raspberry Pi OS, in preference order
SYSTEM_FALLBACKS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "C:/Windows/Fonts/consola.ttf",
)


def _candidates(bold: bool):
    yield FONT_DIR / (BOLD if bold else REGULAR)
    yield FONT_DIR / REGULAR  # bundled regular beats any system face
    for path in SYSTEM_FALLBACKS:
        yield Path(path)


@lru_cache(maxsize=32)
def load(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """A monospace font at `size` pixels.  Cached: glyph rasterisation is the
    expensive part of drawing a frame on a Zero 2 W."""
    for path in _candidates(bold):
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size)
        except OSError as exc:  # pragma: no cover - unreadable font file
            log.warning("could not load %s: %s", path, exc)
    log.error("no usable monospace font found; falling back to Pillow's bitmap font")
    return ImageFont.load_default()


def available() -> bool:
    """True when the bundled Roboto Mono is present."""
    return (FONT_DIR / REGULAR).exists()
