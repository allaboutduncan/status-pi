"""Getting pixels onto the panel.

The Waveshare 3.5" (A) is an ILI9486 driven by the fbtft waveshare35a
overlay, which exposes it as an ordinary Linux framebuffer -- so we convert
the rendered image to RGB565 and write it, with no X server, compositor or
browser anywhere in the picture.

The panel's SPI bus runs at 16MHz, so a full 480x320x16bpp frame costs
roughly 150ms.  We therefore only ever write the rows that actually changed;
a ticking clock or a scrolling marquee touches a small band and costs a few
milliseconds.
"""

from __future__ import annotations

import logging
import mmap
import os
from pathlib import Path

log = logging.getLogger(__name__)

try:  # numpy makes the RGB565 conversion far faster; it is optional
    import numpy as _np
except ImportError:  # pragma: no cover - only on machines without numpy
    _np = None


def to_rgb565(img) -> bytes:
    """Pack an RGB image into little-endian RGB565."""
    if _np is not None:
        arr = _np.asarray(img.convert("RGB"), dtype=_np.uint16)
        packed = (
            ((arr[:, :, 0] & 0xF8) << 8)
            | ((arr[:, :, 1] & 0xFC) << 3)
            | (arr[:, :, 2] >> 3)
        )
        return packed.astype("<u2").tobytes()
    rgb = img.convert("RGB")
    pixels = rgb.get_flattened_data() if hasattr(rgb, "get_flattened_data")         else rgb.getdata()
    out = bytearray(img.width * img.height * 2)
    i = 0
    for r, g, b in pixels:
        value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[i] = value & 0xFF
        out[i + 1] = value >> 8
        i += 2
    return bytes(out)


def _sysfs(device: str, name: str):
    node = Path("/sys/class/graphics") / Path(device).name / name
    try:
        return node.read_text().strip()
    except OSError:
        return None


def probe(device: str = "/dev/fb1") -> dict:
    """Read geometry from sysfs rather than hard-coding it, so a panel that
    comes up rotated or at a different depth is reported instead of silently
    producing garbage."""
    info = {"device": device, "width": None, "height": None, "bpp": None, "stride": None}
    size = _sysfs(device, "virtual_size")
    if size and "," in size:
        width, height = size.split(",")[:2]
        info["width"], info["height"] = int(width), int(height)
    bpp = _sysfs(device, "bits_per_pixel")
    if bpp:
        info["bpp"] = int(bpp)
    stride = _sysfs(device, "stride")
    if stride:
        info["stride"] = int(stride)
    return info


class FramebufferError(RuntimeError):
    pass


class Framebuffer:
    """Writes changed row-spans of an RGB image to a 16bpp framebuffer."""

    def __init__(self, device: str = "/dev/fb1", width: int = 480, height: int = 320):
        self.device = device
        info = probe(device)
        self.width = info["width"] or width
        self.height = info["height"] or height
        self.bpp = info["bpp"] or 16
        if self.bpp != 16:
            raise FramebufferError(
                "%s is %s bpp; status-pi renders RGB565" % (device, self.bpp))
        self.stride = info["stride"] or self.width * 2
        self.row_bytes = self.width * 2
        try:
            self._fd = os.open(device, os.O_RDWR)
            self._map = mmap.mmap(self._fd, self.stride * self.height)
        except OSError as exc:
            raise FramebufferError("cannot open %s: %s" % (device, exc)) from exc
        self._previous = None
        log.info("framebuffer %s %sx%s @%sbpp stride=%s",
                 device, self.width, self.height, self.bpp, self.stride)

    def blit(self, img) -> int:
        """Push an image; returns the number of rows actually written."""
        if img.size != (self.width, self.height):
            img = img.resize((self.width, self.height))
        data = to_rgb565(img)
        written = 0
        for start, end in self._changed_rows(data):
            chunk = data[start * self.row_bytes: end * self.row_bytes]
            if self.stride == self.row_bytes:
                offset = start * self.stride
                self._map[offset: offset + len(chunk)] = chunk
            else:  # padded stride: copy row by row
                for row in range(start, end):
                    src = (row - start) * self.row_bytes
                    dst = row * self.stride
                    self._map[dst: dst + self.row_bytes] = chunk[src: src + self.row_bytes]
            written += end - start
        if written:
            self._map.flush()
        self._previous = data
        return written

    def _changed_rows(self, data: bytes):
        """Contiguous spans of rows that differ from the last frame."""
        if self._previous is None or len(self._previous) != len(data):
            return [(0, self.height)]
        spans = []
        start = None
        for row in range(self.height):
            lo = row * self.row_bytes
            hi = lo + self.row_bytes
            if data[lo:hi] != self._previous[lo:hi]:
                if start is None:
                    start = row
            elif start is not None:
                spans.append((start, row))
                start = None
        if start is not None:
            spans.append((start, self.height))
        return spans

    def clear(self) -> None:
        self._map[:] = bytes(len(self._map))
        self._map.flush()
        self._previous = None

    def close(self) -> None:
        try:
            self._map.close()
            os.close(self._fd)
        except (OSError, ValueError):
            pass


class SimFramebuffer:
    """Stand-in used with --sim on a development machine, and by the web UI's
    live preview: keeps the last frame as PNG bytes."""

    def __init__(self, path=None, width: int = 480, height: int = 320):
        self.width = width
        self.height = height
        self.path = Path(path) if path else None
        self.png = b""
        self.frames = 0

    def blit(self, img) -> int:
        import io

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        self.png = buffer.getvalue()
        self.frames += 1
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_bytes(self.png)
            os.replace(tmp, self.path)
        return self.height

    def clear(self) -> None:
        self.png = b""

    def close(self) -> None:
        pass


def open_display(config, simulate: bool = False, preview_path=None):
    """Real framebuffer when we can get one, simulator otherwise."""
    width, height = config.display.width, config.display.height
    if simulate:
        return SimFramebuffer(preview_path, width, height)
    try:
        return Framebuffer(config.display.framebuffer, width, height)
    except FramebufferError as exc:
        log.warning("%s -- falling back to simulated display", exc)
        return SimFramebuffer(preview_path, width, height)
