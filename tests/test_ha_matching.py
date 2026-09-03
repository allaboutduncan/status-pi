"""Deciding "am I on a call?" from whatever entity Home Assistant offers.

Two shapes matter in practice: a binary camera sensor (on/off), and macOS's
"active audio input" sensor, which reports the name of whichever microphone
is live and "Inactive" when none is.
"""

import pytest

from status_pi.config import Config
from status_pi.sources.ha import HomeAssistant, websocket_url


def client(**ha):
    ha.setdefault("url", "http://ha")
    ha.setdefault("token", "t")
    ha.setdefault("entity", "sensor.x")
    link = HomeAssistant(Config.from_dict({"home_assistant": ha}))
    link.connected = True
    return link


# -- binary sensors, the original behaviour --------------------------------

@pytest.mark.parametrize("state,expected", [("on", True), ("off", False)])
def test_binary_camera_sensor(state, expected):
    link = client(busy_states=["on"])
    link.state = state
    assert link.mic_on is expected


def test_extra_busy_states_can_be_listed():
    link = client(busy_states=["on", "recording"])
    link.state = "recording"
    assert link.mic_on is True


# -- macOS active audio input ----------------------------------------------

AUDIO = dict(entity="sensor.phillip_duncan_macbook_pro_active_audio_input",
             busy_match="none_of", busy_states=["Inactive"])


@pytest.mark.parametrize("state", [
    "MacBook Pro Microphone",
    "Shure MV7",
    "Jabra Evolve2 65",
    "External Microphone",
])
def test_any_live_microphone_means_busy(state):
    """The whole point of none_of: a microphone you have never owned yet
    still reads as BUSY, with no config change."""
    link = client(**AUDIO)
    link.state = state
    assert link.mic_on is True


def test_inactive_means_free():
    link = client(**AUDIO)
    link.state = "Inactive"
    assert link.mic_on is False


def test_idle_value_is_matched_case_insensitively():
    link = client(**AUDIO)
    for state in ("inactive", "INACTIVE", " Inactive "):
        link.state = state
        assert link.mic_on is False, state


# -- "we do not know" is not the same as "free" ----------------------------

@pytest.mark.parametrize("state", ["unknown", "unavailable", "", "   ", None])
def test_missing_data_is_unknown_not_free(state):
    """None makes the state machine fall back to the calendar and flag the
    mic as degraded; False would claim FREE we cannot vouch for."""
    link = client(**AUDIO)
    link.state = state
    assert link.mic_on is None


def test_disconnected_is_unknown_whatever_the_last_state_was():
    link = client(**AUDIO)
    link.state = "Shure MV7"
    link.connected = False
    assert link.mic_on is None


def test_unknown_is_unknown_under_any_of_too():
    link = client(busy_states=["on"])
    link.state = "unavailable"
    assert link.mic_on is None


def test_default_match_mode_is_exact():
    assert Config.from_dict({}).home_assistant.busy_match == "any_of"


def test_an_unrecognised_match_mode_falls_back_to_exact():
    link = client(busy_match="nonsense", busy_states=["on"])
    link.state = "on"
    assert link.mic_on is True
    link.state = "off"
    assert link.mic_on is False


@pytest.mark.parametrize("url,expected", [
    ("http://192.168.68.82:8123", "ws://192.168.68.82:8123/api/websocket"),
    ("https://ha.example.com", "wss://ha.example.com/api/websocket"),
    ("http://ha:8123/", "ws://ha:8123/api/websocket"),
    ("ha.local:8123", "ws://ha.local:8123/api/websocket"),
])
def test_websocket_url_forms(url, expected):
    assert websocket_url(url) == expected


# -- the whole chain: sensor value -> panel pixels -------------------------

def test_a_live_microphone_paints_the_panel_red():
    """End to end for the real setup: the macOS active-audio-input sensor
    reporting a live microphone must reach the panel as BUSY in red."""
    from datetime import datetime, timezone

    from status_pi.render import make_renderer, palette
    from status_pi.runtime import RuntimeState
    from status_pi import state as S

    config = Config.from_dict({
        "home_assistant": {
            "url": "http://192.168.68.82:8123", "token": "t",
            "entity": "sensor.phillip_duncan_macbook_pro_active_audio_input",
            "busy_match": "none_of", "busy_states": ["Inactive"],
        },
    })
    link = HomeAssistant(config)
    link.connected = True
    link.state = "MacBook Pro Microphone"

    now = datetime(2026, 9, 3, 14, 0, tzinfo=timezone.utc)
    display = S.compute(now, config=config, runtime=RuntimeState(), events=[],
                        mic_on=link.mic_on, ha_connected=True, cal_ok=True)
    assert display.mode == S.MODE_BUSY
    assert display.headline == "BUSY"
    assert display.color == palette.BUSY == (255, 42, 32)
    assert display.mic_degraded is False

    image = make_renderer(config).render(display)
    painted = dict((colour, count) for count, colour in image.getcolors(maxcolors=1 << 16))
    assert painted.get(palette.BUSY, 0) > 500, "BUSY is drawn in red"


def test_going_idle_returns_the_panel_to_free():
    from datetime import datetime, timezone

    from status_pi.render import palette
    from status_pi.runtime import RuntimeState
    from status_pi import state as S

    config = Config.from_dict({
        "home_assistant": {
            "url": "http://ha", "token": "t",
            "entity": "sensor.phillip_duncan_macbook_pro_active_audio_input",
            "busy_match": "none_of", "busy_states": ["Inactive"],
        },
    })
    link = HomeAssistant(config)
    link.connected = True
    link.state = "Inactive"

    now = datetime(2026, 9, 3, 14, 0, tzinfo=timezone.utc)
    display = S.compute(now, config=config, runtime=RuntimeState(), events=[],
                        mic_on=link.mic_on, ha_connected=True, cal_ok=True)
    assert display.mode == S.MODE_FREE
    assert display.color == palette.FREE
