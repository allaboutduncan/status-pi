"""End-to-end: a real StatusPi (simulated panel) behind the real web app.

The device has no buttons, so these routes are the only way to control it --
they are worth testing against the actual app object rather than a mock.
"""

import asyncio
import json

import pytest

pytest.importorskip("aiohttp")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from status_pi.app import StatusPi  # noqa: E402
from status_pi.web.server import build_app  # noqa: E402


@pytest.fixture
def device(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(json.dumps({
        "timezone": "UTC",
        "home_assistant": {"url": "http://ha", "token": "secret-token",
                           "entity": "binary_sensor.cam"},
        "calendar": {"ics_url": "https://calendar.google.com/private-xyz/basic.ics"},
    }))
    monkeypatch.setenv("STATUS_PI_STATE_DIR", str(tmp_path))
    return StatusPi(config_path=config, simulate=True)


def run(device, coro_factory):
    async def main():
        async with TestClient(TestServer(build_app(device))) as client:
            return await coro_factory(client)

    return asyncio.run(main())


def test_state_and_preview(device):
    async def scenario(client):
        state = await (await client.get("/api/state")).json()
        device.draw(force=True)
        preview = await client.get("/preview.png")
        return state, preview.status, await preview.read()

    state, status, png = run(device, scenario)
    assert state["state"]["headline"] in ("FREE", "BUSY")
    assert state["home_assistant"]["entity"] == "binary_sensor.cam"
    assert status == 200 and png.startswith(b"\x89PNG")


def test_setting_a_status_reaches_the_panel(device):
    async def scenario(client):
        await client.post("/api/status", json={"text": "Deep Work", "color": "blue"})
        return await (await client.get("/api/state")).json()

    state = run(device, scenario)
    assert state["status"]["text"] == "Deep Work"
    device.draw(force=True)
    assert device.state.headline == "DEEP WORK"


def test_status_is_capped_to_what_the_panel_can_hold(device):
    async def scenario(client):
        await client.post("/api/status", json={"text": "x" * 200})
        return await (await client.get("/api/state")).json()

    assert len(run(device, scenario)["status"]["text"]) == 24


def test_timer_lifecycle(device):
    async def scenario(client):
        started = await (await client.post(
            "/api/timer", json={"action": "start", "seconds": 600, "label": "focus"})).json()
        paused = await (await client.post("/api/timer", json={"action": "pause"})).json()
        stopped = await (await client.post("/api/timer", json={"action": "stop"})).json()
        rejected = await client.post("/api/timer", json={"action": "start", "seconds": 0})
        return started, paused, stopped, rejected.status

    started, paused, stopped, rejected = run(device, scenario)
    assert started["running"] and started["label"] == "focus"
    assert paused["paused"] and paused["seconds_left"] > 0
    assert not stopped["running"] and not stopped["paused"]
    assert rejected == 400


def test_config_round_trip_never_leaks_secrets(device):
    async def scenario(client):
        shown = await (await client.get("/api/config")).json()
        # the UI posts empty secrets when the user does not retype them
        await client.post("/api/config", json={
            "timezone": "Europe/London",
            "home_assistant": {"token": "", "entity": "binary_sensor.mic"},
            "calendar": {"ics_url": ""},
        })
        return shown, await (await client.get("/api/config")).json()

    shown, after = run(device, scenario)
    assert shown["home_assistant"]["token"] == "********"
    assert "private-xyz" not in json.dumps(shown)
    assert after["timezone"] == "Europe/London"
    assert device.config.home_assistant.token == "secret-token", "secret kept"
    assert device.config.home_assistant.entity == "binary_sensor.mic"
    assert device.config.path.exists(), "changes are persisted, not just in memory"


def test_hiding_an_event_persists_to_config(device):
    async def scenario(client):
        return await (await client.post("/api/calendar/hide", json={"uid": "abc"})).json()

    assert run(device, scenario)["hidden_uids"] == ["abc"]
    assert device.config.calendar.hidden_uids == ["abc"]


def test_auth_token_locks_the_ui_down(device):
    device.config.web.auth_token = "hunter2"

    async def scenario(client):
        denied = await client.get("/api/state")
        allowed = await client.get("/api/state", headers={"X-Auth-Token": "hunter2"})
        health = await client.get("/healthz")
        return denied.status, allowed.status, health.status

    assert run(device, scenario) == (401, 200, 200)


def test_switching_calendar_provider_actually_swaps_the_feed(device):
    """asyncio keeps only a weak reference to a running task, so the swap
    task must be held or it can be collected part-way through -- leaving the
    old feed in place, with no events and no error to explain it."""
    from status_pi.sources.cal import HACalendarFeed, IcsCalendarFeed

    assert isinstance(device.calendar, IcsCalendarFeed)

    async def scenario(client):
        await client.post("/api/config", json={
            "calendar": {"provider": "ha", "ha_entity": "calendar.work"}})
        # let the swap task run to completion
        await asyncio.sleep(0.05)
        return await (await client.get("/api/state")).json()

    state = run(device, scenario)
    assert isinstance(device.calendar, HACalendarFeed)
    assert state["calendar"]["provider"] == "ha"
    assert device.config.calendar.ha_entity == "calendar.work"


def test_spawned_tasks_are_held_until_they_finish(device):
    """A regression guard for the same weak-reference trap."""
    async def scenario(client):
        started = asyncio.Event()
        finished = asyncio.Event()

        async def slow():
            started.set()
            await asyncio.sleep(0.05)
            finished.set()

        device.spawn(slow())
        await started.wait()
        assert device._tasks, "the task must be referenced while it runs"
        await asyncio.wait_for(finished.wait(), 1)
        await asyncio.sleep(0)
        return True

    assert run(device, scenario)
    assert device._tasks == set(), "and released once it is done"
