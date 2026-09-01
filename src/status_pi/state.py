"""Reduce every input into the single DisplayState the renderer draws.

Priority, as specified: the microphone always wins.

    1. Home Assistant says the mic/camera is live  -> BUSY
    2. A custom status is set and unexpired        -> that text
    3. A countdown timer is running                -> the countdown
    4. Otherwise                                   -> FREE + next meeting

If Home Assistant is unreachable we substitute rule 1 with "a meeting is
happening right now" and mark the mic signal as unavailable in the header,
rather than claiming FREE we cannot vouch for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime

from .events import current_event, next_event
from .render import palette

MODE_BUSY = "busy"
MODE_FREE = "free"
MODE_CUSTOM = "custom"
MODE_TIMER = "timer"
MODE_DONE = "done"


@dataclass
class DisplayState:
    mode: str = MODE_FREE
    headline: str = "FREE"
    color: tuple = palette.FREE
    #: meeting context, the wide line under the headline
    context: str = ""
    #: small line at the bottom: running timer, status expiry, warnings
    subline: str = ""
    clock: str = ""
    date: str = ""
    brightness: float = 1.0
    clock_only: bool = False
    ha_ok: bool = False
    #: mic state unknown -> we are inferring busy/free from the calendar
    mic_degraded: bool = False
    cal_ok: bool = False
    #: True while something on screen needs a fast tick (scroll, countdown)
    animated: bool = False
    warnings: list = field(default_factory=list)

    def key(self):
        """Everything that affects pixels, for cheap change detection."""
        return (
            self.mode, self.headline, self.color, self.context, self.subline,
            self.clock, self.date, self.brightness, self.clock_only,
            self.ha_ok, self.mic_degraded, self.cal_ok,
        )


def _fmt_time(when: datetime, config) -> str:
    if config.display.time_24h:
        return when.strftime("%H:%M")
    return when.strftime("%I:%M").lstrip("0") + when.strftime("%p").lower()


def _fmt_countdown(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _parse_hhmm(value, fallback: dtime) -> dtime:
    try:
        hour, minute = str(value).split(":")
        return dtime(int(hour) % 24, int(minute) % 60)
    except (ValueError, AttributeError):
        return fallback


def in_quiet_hours(now: datetime, quiet) -> bool:
    if not quiet.enabled:
        return False
    start = _parse_hhmm(quiet.start, dtime(22, 0))
    end = _parse_hhmm(quiet.end, dtime(7, 0))
    current = now.time()
    if start == end:
        return False
    if start < end:  # e.g. 01:00 -> 06:00
        return start <= current < end
    return current >= start or current < end  # wraps midnight


def _fmt_date(now: datetime) -> str:
    return "{} {} {}".format(now.strftime("%a"), now.day, now.strftime("%b"))


def compute(now: datetime, *, config, runtime, events=None, mic_on=None,
            ha_connected: bool = False, cal_ok: bool = False) -> DisplayState:
    events = events or []
    state = DisplayState(ha_ok=ha_connected, cal_ok=cal_ok)
    stamp = now.timestamp()

    state.clock = _fmt_time(now, config)
    if config.display.show_seconds:
        state.clock += now.strftime(":%S")
    state.date = _fmt_date(now)

    quiet = in_quiet_hours(now, config.display.quiet_hours)
    state.brightness = (
        config.display.quiet_hours.brightness if quiet else config.display.brightness
    )
    state.clock_only = quiet and config.display.quiet_hours.clock_only

    now_event = current_event(events, now)
    upcoming = next_event(events, now)

    # -- rule 1: the microphone always wins --------------------------------
    if ha_connected and mic_on is not None:
        busy = bool(mic_on)
    else:
        busy = now_event is not None
        state.mic_degraded = True
        if config.ha_configured:
            state.warnings.append("home assistant unreachable")

    timer = runtime.timer
    status = runtime.status
    status_on = status.active(stamp)

    if busy:
        state.mode = MODE_BUSY
        state.headline = "BUSY"
        state.color = palette.BUSY
    elif status_on:
        state.mode = MODE_CUSTOM
        state.headline = status.text.upper()
        state.color = palette.resolve(status.color)
    elif timer.active:
        state.mode = MODE_TIMER
        state.headline = _fmt_countdown(timer.seconds_left(stamp))
        state.color = palette.TIMER
        state.animated = not timer.paused
    elif runtime.timer_done_at:
        state.mode = MODE_DONE
        state.headline = "DONE"
        state.color = palette.TIMER
    else:
        state.mode = MODE_FREE
        state.headline = "FREE"
        state.color = palette.FREE

    # -- meeting context ---------------------------------------------------
    if now_event is not None:
        state.context = "{} - until {}".format(now_event.title, _fmt_time(now_event.end, config))
    elif upcoming is not None:
        horizon = config.calendar.lookahead_hours * 3600
        if (upcoming.start - now).total_seconds() <= horizon:
            state.context = "next {} {}".format(_fmt_time(upcoming.start, config), upcoming.title)
        else:
            state.context = "nothing scheduled today"
    elif cal_ok:
        state.context = "no meetings"

    # -- secondary line ----------------------------------------------------
    bits = []
    if timer.active and state.mode != MODE_TIMER:
        prefix = "paused" if timer.paused else "timer"
        bits.append("{} {}".format(prefix, _fmt_countdown(timer.seconds_left(stamp))))
        state.animated = state.animated or not timer.paused
    if state.mode == MODE_TIMER and timer.label:
        bits.append(timer.label)
    if state.mode != MODE_CUSTOM and status_on:
        bits.append(status.text)
    if status_on and status.expires_at:
        left = status.seconds_left(stamp)
        if left is not None and left < 3600:
            bits.append("({}m left)".format(left // 60))
    state.subline = "   ".join(bits)

    return state
