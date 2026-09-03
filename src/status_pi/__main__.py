"""Entry point: python -m status_pi [--sim] [--config PATH]."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="status-pi", description=__doc__)
    parser.add_argument("--config", help="path to config.yaml")
    parser.add_argument("--sim", action="store_true",
                        help="render to PNG instead of the panel (development)")
    parser.add_argument("--probe", action="store_true",
                        help="report what the framebuffer looks like and exit")
    parser.add_argument("--check-ha", action="store_true",
                        help="test the Home Assistant link step by step and exit")
    parser.add_argument("--watch", type=int, metavar="SECONDS", default=0,
                        help="with --check-ha, stream entity changes for a while")
    parser.add_argument("--check-calendar", action="store_true",
                        help="fetch the calendar and show what reaches the panel")
    parser.add_argument("--raw", action="store_true",
                        help="with --check-calendar, print the untouched response")
    parser.add_argument("--test-pattern", action="store_true",
                        help="draw a ruler on the panel to measure what the bezel hides")
    parser.add_argument("--frames", metavar="DIR",
                        help="write one PNG per display state to DIR and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def probe(config_path):
    from .config import Config
    from .render.fb import probe as probe_fb

    config = Config.load(config_path)
    info = probe_fb(config.display.framebuffer)
    if info["width"] is None:
        print("%s: no framebuffer found." % info["device"])
        print("Check: dmesg | grep -i fb_ili9486   and   ls -l /dev/fb*")
        return 1
    print("%(device)s  %(width)sx%(height)s  %(bpp)sbpp  stride=%(stride)s" % info)
    if info["bpp"] != 16:
        print("warning: status-pi renders RGB565 and expects 16bpp")
        return 1
    return 0


def test_pattern(config_path, simulate=False):
    """Put a ruler on the panel so the bezel can be measured, not guessed."""
    from .config import Config, default_state_dir
    from .render.calibrate import test_pattern as build
    from .render.fb import open_display

    config = Config.load(config_path)
    display = open_display(config, simulate=simulate,
                           preview_path=default_state_dir() / "preview.png")
    image = build(config.display.width, config.display.height)
    if config.display.rotate == 180:
        image = image.rotate(180)
    display.blit(image)
    display.close()
    print("Ruler drawn on %s." % type(display).__name__)
    print()
    print("Read the smallest number fully visible on each edge, then set those")
    print("as the margins in Settings -> Display (or margin_left / margin_top /")
    print("margin_right / margin_bottom in config.yaml).")
    print()
    print("Stop the service first, or it will paint over this:")
    print("    sudo systemctl stop status-pi")
    print("    sudo -u status-pi /opt/status-pi/venv/bin/python -m status_pi --test-pattern")
    print("    sudo systemctl start status-pi")
    return 0


def sample_frames(directory, config_path):
    """Every display state as a PNG, for reviewing the layout off-device."""
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from .config import Config
    from .events import Event
    from .render import make_renderer
    from .runtime import CustomStatus, RuntimeState
    from . import state as state_module

    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    config = Config.load(config_path)
    renderer = make_renderer(config)
    now = datetime(2026, 9, 1, 9, 41, tzinfo=timezone.utc)
    events = [
        Event("a", "Sprint Review", now - timedelta(minutes=11), now + timedelta(minutes=49)),
        Event("b", "Weekly 1-1 with Dana", now + timedelta(hours=2), now + timedelta(hours=3)),
    ]

    def frame(name, runtime, when=now, **kwargs):
        kwargs.setdefault("cal_ok", True)
        state = state_module.compute(when, config=config, runtime=runtime, **kwargs)
        renderer.render(state).save(out / ("%s.png" % name))
        print("wrote %s.png  [%s] %s" % (name, state.mode, state.headline))

    long_title = [Event(
        "c", "Quarterly planning with the platform team",
        now - timedelta(minutes=5), now + timedelta(minutes=55))]

    idle = RuntimeState()
    frame("busy", idle, events=events, mic_on=True, ha_connected=True)
    frame("busy-long-title", idle, events=long_title, mic_on=True, ha_connected=True)
    frame("free", idle, events=events[1:], mic_on=False, ha_connected=True)
    frame("free-no-meetings", idle, events=[], mic_on=False, ha_connected=True)
    frame("ha-offline", idle, events=events, mic_on=None, ha_connected=False)

    custom = RuntimeState()
    custom.status = CustomStatus("Deep Work", "amber", None)
    frame("custom", custom, events=events[1:], mic_on=False, ha_connected=True)
    custom.status = CustomStatus("Back at 3pm", "blue", None)
    frame("custom-long", custom, events=events[1:], mic_on=False, ha_connected=True)

    timed = RuntimeState()
    timed.timer.start(754, "focus", now=now.timestamp())
    frame("timer", timed, events=events[1:], mic_on=False, ha_connected=True)
    frame("timer-under-busy", timed, events=events, mic_on=True, ha_connected=True)

    frame("night", idle, when=now.replace(hour=23), events=[], mic_on=False, ha_connected=True)
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if args.probe:
        return probe(args.config)
    if args.check_ha:
        import asyncio as _asyncio

        from .config import Config
        from .diagnose import check_ha

        return _asyncio.run(check_ha(Config.load(args.config), watch=args.watch))
    if args.test_pattern:
        return test_pattern(args.config, args.sim)
    if args.check_calendar:
        import asyncio as _asyncio

        from .config import Config
        from .diagnose import check_calendar

        return _asyncio.run(check_calendar(Config.load(args.config), raw=args.raw))
    if args.frames:
        return sample_frames(args.frames, args.config)

    from .app import StatusPi

    async def run():
        app = StatusPi(config_path=args.config, simulate=args.sim)
        loop = asyncio.get_running_loop()
        for name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    loop.add_signal_handler(sig, app.stop)
                except NotImplementedError:  # Windows
                    signal.signal(sig, lambda *_: app.stop())
        await app.run()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
