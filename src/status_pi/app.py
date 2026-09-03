"""The orchestrator: one asyncio process that owns every moving part.

Tick loop, in short: recompute the display state, redraw only if something
that affects pixels changed, and push only the framebuffer rows that differ.
Idle cost is therefore a wakeup per second and nothing else.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import Config, default_state_dir
from .render.fb import open_display
from .render import make_renderer
from .runtime import RuntimeState
from .sources.cal import make_calendar
from .sources.ha import HomeAssistant
from . import state as state_module

log = logging.getLogger(__name__)

IDLE_TICK = 1.0  # seconds between wakeups when nothing is moving
FAST_TICK = 0.125  # while a marquee is scrolling


def load_timezone(name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001 - missing tzdata, bad name
        log.warning("timezone %r unusable (%s); using system local time", name, exc)
        return datetime.now(timezone.utc).astimezone().tzinfo


class Watchdog:
    """systemd sd_notify, implemented in a dozen lines so we do not need the
    python3-systemd package on the device."""

    def __init__(self):
        self.address = os.environ.get("NOTIFY_SOCKET")
        self._sock = None
        if self.address:
            if self.address.startswith("@"):  # abstract namespace
                self.address = "\0" + self.address[1:]
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

    def notify(self, message: str) -> None:
        if not self._sock:
            return
        try:
            self._sock.sendto(message.encode(), self.address)
        except OSError:
            pass

    def ready(self) -> None:
        self.notify("READY=1")

    def ping(self) -> None:
        self.notify("WATCHDOG=1")

    def status(self, text: str) -> None:
        self.notify("STATUS=%s" % text)


class StatusPi:
    def __init__(self, config_path=None, simulate: bool = False):
        self.config = Config.load(config_path)
        self.simulate = simulate
        self.state_dir = default_state_dir()
        self.tz = load_timezone(self.config.timezone)
        self.runtime = RuntimeState.load(self.state_dir / "runtime.json")
        self.renderer = make_renderer(self.config)
        self.display = open_display(
            self.config, simulate=simulate,
            preview_path=self.state_dir / "preview.png")
        self.ha = HomeAssistant(self.config, on_change=self.wake)
        self.calendar = make_calendar(
            self.config, tz=self.tz,
            cache_path=self.state_dir / "calendar.json", on_change=self.wake)
        self.watchdog = Watchdog()
        self.state = None
        self.started_at = time.time()
        self.frames = 0
        self.rows_written = 0
        self._wake = asyncio.Event()
        self._stopping = False
        #: asyncio holds only a weak reference to a running task, so a task
        #: nobody keeps can be garbage collected part-way through.  These are
        #: the one-shot reconfiguration tasks; hold them until they finish.
        self._tasks = set()

    # -- lifecycle ---------------------------------------------------------
    def spawn(self, coro) -> None:
        """Run a one-shot background task and keep it alive until it ends."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def wake(self) -> None:
        """Nudge the tick loop; called whenever a source or the web UI
        changes something the panel should show immediately."""
        self._wake.set()

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def compute(self):
        return state_module.compute(
            self.now(),
            config=self.config,
            runtime=self.runtime,
            events=self.calendar.events,
            mic_on=self.ha.mic_on,
            ha_connected=self.ha.connected,
            cal_ok=self.calendar.ok or bool(self.calendar.events),
        )

    def draw(self, force: bool = False) -> bool:
        """Recompute, redraw if needed, push to the panel.  Returns True if
        anything reached the display."""
        new_state = self.compute()
        self.renderer.tick(time.time())
        # A countdown changes its own headline, so the state key already
        # catches it; only an animation (a marquee, or the mono style's
        # pulse) needs redrawing on an otherwise unchanged state.
        changed = self.state is None or new_state.key() != self.state.key()
        self.state = new_state
        if not (changed or self.renderer.animating or force):
            return False
        image = self.renderer.render(new_state)
        self.rows_written += self.display.blit(image)
        self.frames += 1
        return True

    async def tick_forever(self) -> None:
        while not self._stopping:
            if self.runtime.tick(time.time()):
                self.runtime.save()
            self.draw()
            self.watchdog.ping()
            delay = FAST_TICK if self.renderer.animating else IDLE_TICK
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            else:
                self._wake.clear()

    async def run(self) -> None:
        log.info("status-pi starting (display=%s)", type(self.display).__name__)
        self.ha.start()
        self.calendar.start()
        from .web.server import start_web

        runner = await start_web(self)
        self.watchdog.ready()
        self.watchdog.status("running")
        try:
            await self.tick_forever()
        finally:
            self._stopping = True
            await self.ha.stop()
            await self.calendar.stop()
            if runner is not None:
                await runner.cleanup()
            self.runtime.save()
            self.display.close()
            log.info("status-pi stopped after %d frames", self.frames)

    def stop(self) -> None:
        self._stopping = True
        self.wake()

    # -- used by the web UI ------------------------------------------------
    def apply_config(self, updates: dict) -> Config:
        """Validate, persist and hot-reload a config change without a restart."""
        previous_provider = self.config.calendar.provider
        new_config = self.config.merge(updates)
        new_config.save()
        self.config = new_config
        self.tz = load_timezone(new_config.timezone)
        self.renderer = make_renderer(new_config)
        self.ha.config = new_config
        if new_config.calendar.provider != previous_provider:
            self.spawn(self._swap_calendar())
        else:
            self.calendar.config = new_config
            self.calendar.tz = self.tz
        # Reconnect HA in case the URL, token or entity moved.
        self.spawn(self._restart_ha())
        self.calendar.refresh_soon()
        self.state = None
        self.wake()
        return new_config

    async def _swap_calendar(self) -> None:
        """Tear down the old feed and build the one the new provider names."""
        await self.calendar.stop()
        self.calendar = make_calendar(
            self.config, tz=self.tz,
            cache_path=self.state_dir / "calendar.json", on_change=self.wake)
        self.calendar.events = []
        self.calendar.start()
        self.wake()

    async def _restart_ha(self) -> None:
        await self.ha.stop()
        self.ha.connected = False
        self.ha.state = None
        self.ha.start()

    def describe(self) -> dict:
        state = self.state or self.compute()
        return {
            "now": self.now().isoformat(),
            "uptime": int(time.time() - self.started_at),
            "frames": self.frames,
            "display": {
                "kind": type(self.display).__name__,
                "style": getattr(self.renderer, "style", "mono"),
                "device": getattr(self.display, "device", None),
                "width": self.display.width,
                "height": self.display.height,
                "rows_written": self.rows_written,
            },
            "state": {
                "mode": state.mode,
                "headline": state.headline,
                "context": state.context,
                "subline": state.subline,
                "clock": state.clock,
                "date": state.date,
                "brightness": state.brightness,
                "mic_degraded": state.mic_degraded,
                "warnings": state.warnings,
            },
            "home_assistant": self.ha.describe(),
            "calendar": self.calendar.describe(),
            "status": {
                "text": self.runtime.status.text,
                "color": self.runtime.status.color,
                "expires_in": self.runtime.status.seconds_left(),
            },
            "timer": {
                "running": self.runtime.timer.running,
                "paused": self.runtime.timer.paused,
                "label": self.runtime.timer.label,
                "duration": self.runtime.timer.duration,
                "seconds_left": self.runtime.timer.seconds_left()
                if self.runtime.timer.active else 0,
            },
            "events": [
                {
                    "uid": e.uid, "title": e.title,
                    "start": e.start.isoformat(), "end": e.end.isoformat(),
                    "all_day": e.all_day,
                }
                for e in self.calendar.events[:20]
            ],
        }

    def preview_png(self) -> bytes:
        """Exactly what the panel is showing, for the web UI."""
        png = getattr(self.display, "png", b"")
        if png:
            return png
        import io

        buffer = io.BytesIO()
        self.renderer.render(self.state or self.compute()).save(buffer, format="PNG")
        return buffer.getvalue()
