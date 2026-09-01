"""The priority rules are the heart of the device, so they get the most tests.

Rule under test throughout: the microphone always wins.
"""

from datetime import datetime, timedelta, timezone

import pytest

from status_pi import state as S
from status_pi.config import Config
from status_pi.events import Event
from status_pi.runtime import CustomStatus, RuntimeState

NOW = datetime(2026, 9, 1, 9, 41, tzinfo=timezone.utc)


@pytest.fixture
def config():
    return Config.from_dict({
        "home_assistant": {"url": "http://ha", "token": "t", "entity": "binary_sensor.cam"},
        "calendar": {"ics_url": "http://ics"},
    })


@pytest.fixture
def meetings():
    return [
        Event("a", "Sprint Review", NOW - timedelta(minutes=11), NOW + timedelta(minutes=49)),
        Event("b", "1-1 with Dana", NOW + timedelta(hours=2), NOW + timedelta(hours=3)),
    ]


def compute(config, runtime=None, **kwargs):
    kwargs.setdefault("ha_connected", True)
    kwargs.setdefault("cal_ok", True)
    return S.compute(NOW, config=config, runtime=runtime or RuntimeState(), **kwargs)


def test_mic_on_is_busy(config, meetings):
    state = compute(config, events=meetings, mic_on=True)
    assert state.mode == S.MODE_BUSY
    assert state.headline == "BUSY"
    assert "Sprint Review" in state.context


def test_mic_off_is_free_even_during_a_meeting(config, meetings):
    """The calendar says we are in a meeting; the mic says we are not.  The
    mic is the truth -- that is the whole point of the device."""
    state = compute(config, events=meetings, mic_on=False)
    assert state.mode == S.MODE_FREE
    assert state.headline == "FREE"


def test_mic_beats_custom_status_and_timer(config, meetings):
    runtime = RuntimeState()
    runtime.status = CustomStatus("Deep Work", "amber", None)
    runtime.timer.start(600, now=NOW.timestamp())
    state = compute(config, runtime, events=meetings, mic_on=True)
    assert state.headline == "BUSY"
    # both are demoted to the secondary line rather than lost
    assert "Deep Work" in state.subline
    assert "timer" in state.subline


def test_custom_status_beats_timer(config):
    runtime = RuntimeState()
    runtime.status = CustomStatus("Deep Work", "amber", None)
    runtime.timer.start(600, now=NOW.timestamp())
    state = compute(config, runtime, events=[], mic_on=False)
    assert state.mode == S.MODE_CUSTOM
    assert state.headline == "DEEP WORK"


def test_timer_shows_when_nothing_else_claims_the_screen(config):
    runtime = RuntimeState()
    runtime.timer.start(754, "focus", now=NOW.timestamp())
    state = compute(config, runtime, events=[], mic_on=False)
    assert state.mode == S.MODE_TIMER
    assert state.headline == "12:34"
    assert state.animated is True


def test_expired_custom_status_is_ignored(config):
    runtime = RuntimeState()
    runtime.status = CustomStatus("Deep Work", "amber", NOW.timestamp() - 1)
    state = compute(config, runtime, events=[], mic_on=False)
    assert state.mode == S.MODE_FREE


def test_ha_offline_falls_back_to_the_calendar(config, meetings):
    state = compute(config, events=meetings, mic_on=None, ha_connected=False)
    assert state.mode == S.MODE_BUSY, "a meeting is in progress"
    assert state.mic_degraded is True
    assert state.warnings


def test_ha_offline_with_no_meeting_is_free(config, meetings):
    state = compute(config, events=meetings[1:], mic_on=None, ha_connected=False)
    assert state.mode == S.MODE_FREE
    assert state.mic_degraded is True


def test_unavailable_entity_is_treated_as_unknown(config, meetings):
    """HA connected but the entity is 'unavailable': mic_on is None, so we
    must not silently report FREE."""
    state = compute(config, events=meetings, mic_on=None, ha_connected=True)
    assert state.mic_degraded is True


def test_next_meeting_is_shown_when_free(config, meetings):
    state = compute(config, events=meetings[1:], mic_on=False)
    assert state.context == "next 11:41 1-1 with Dana"


def test_countdown_formatting():
    assert S._fmt_countdown(59) == "0:59"
    assert S._fmt_countdown(754) == "12:34"
    assert S._fmt_countdown(3661) == "1:01:01"
    assert S._fmt_countdown(-5) == "0:00"


@pytest.mark.parametrize(
    "hour,expected",
    [(9, False), (21, False), (22, True), (23, True), (2, True), (6, True), (7, False)],
)
def test_quiet_hours_wrap_midnight(config, hour, expected):
    quiet = config.display.quiet_hours  # 22:00 -> 07:00
    assert S.in_quiet_hours(NOW.replace(hour=hour), quiet) is expected


def test_quiet_hours_dim_the_panel(config):
    state = S.compute(NOW.replace(hour=23), config=config, runtime=RuntimeState(),
                      events=[], mic_on=False, ha_connected=True)
    assert state.brightness == config.display.quiet_hours.brightness


def test_status_expiry_is_announced_when_close(config):
    runtime = RuntimeState()
    runtime.status = CustomStatus("Back soon", "amber", NOW.timestamp() + 600)
    state = compute(config, runtime, events=[], mic_on=False)
    assert "10m left" in state.subline


def test_key_changes_only_when_pixels_would(config, meetings):
    """The tick loop skips redrawing when the key is unchanged, so seconds
    must not leak into it while show_seconds is off."""
    first = compute(config, events=meetings, mic_on=True)
    later = S.compute(NOW + timedelta(seconds=20), config=config,
                      runtime=RuntimeState(), events=meetings, mic_on=True,
                      ha_connected=True, cal_ok=True)
    assert first.key() == later.key()
