"""Step-by-step Home Assistant check for `python -m status_pi --check-ha`.

The panel can only say "connected" or "disconnected", and the web UI only has
room for a little more.  When something is wrong you want to know *which*
step failed -- the network, the token, or the entity -- so this walks them in
order and stops at the first real problem.

It also lists likely camera/microphone entities, which is the fiddly part of
setting the device up in the first place.
"""

from __future__ import annotations

import asyncio
import re

from .config import Config
from .sources.ha import websocket_url

#: entity domains that can plausibly carry a camera/mic state
CANDIDATE_DOMAINS = ("binary_sensor", "sensor", "switch", "input_boolean", "device_tracker")
#: words that suggest an entity is about a camera, mic or meeting
CANDIDATE_WORDS = re.compile(
    r"camera|webcam|\bcam\b|micro|\bmic\b|meeting|busy|call|teams|zoom|webex|"
    r"presence|in_use|conference",
    re.IGNORECASE,
)

OK = "  ok  "
BAD = " fail "


def _line(mark: str, text: str) -> None:
    print("[%s] %s" % (mark, text))


async def _api(session, url: str, token: str, path: str):
    headers = {"Authorization": "Bearer %s" % token}
    async with session.get(url.rstrip("/") + path, headers=headers) as response:
        body = await response.json() if response.content_type == "application/json" else None
        return response.status, body


def _rank(entity: dict, wanted: str = "") -> int:
    """Higher is more likely to be the entity we are looking for."""
    entity_id = entity.get("entity_id", "")
    name = (entity.get("attributes") or {}).get("friendly_name", "")
    score = 0
    if CANDIDATE_WORDS.search(entity_id):
        score += 3
    if CANDIDATE_WORDS.search(str(name)):
        score += 2
    if entity.get("state") in ("on", "off"):
        score += 2
    if entity_id.startswith("binary_sensor."):
        score += 1
    if wanted and wanted.split(".")[-1] in entity_id:
        score += 4
    return score


def _print_candidates(states, wanted: str = "") -> None:
    candidates = [
        s for s in states
        if s.get("entity_id", "").split(".")[0] in CANDIDATE_DOMAINS and _rank(s, wanted) >= 3
    ]
    candidates.sort(key=lambda s: _rank(s, wanted), reverse=True)
    if not candidates:
        print("      no obvious camera/microphone entities found.")
        print("      In Home Assistant: Developer Tools -> States, and look for the")
        print("      entity your camera automation triggers on.")
        return
    print("      likely entities (most likely first):")
    for entity in candidates[:12]:
        name = (entity.get("attributes") or {}).get("friendly_name", "")
        print("        %-44s %-12s %s" % (entity["entity_id"], entity.get("state"), name))


async def check_ha(config: Config, watch: int = 0) -> int:
    import aiohttp

    ha = config.home_assistant
    if not ha.url:
        _line(BAD, "no Home Assistant URL configured")
        return 1
    print("Home Assistant: %s" % ha.url)
    print("Entity:         %s\n" % (ha.entity or "(not set)"))

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # 1. is anything listening?
        try:
            async with session.get(ha.url.rstrip("/") + "/", allow_redirects=True) as response:
                _line(OK, "reachable (HTTP %s)" % response.status)
        except Exception as exc:  # noqa: BLE001
            _line(BAD, "cannot reach %s: %s" % (ha.url, exc))
            print("      Check the IP and port, and that the Pi is on the same network.")
            return 1

        # 2. does the token work?
        if not ha.token:
            _line(BAD, "no access token configured")
            print("      Home Assistant -> your profile -> Security -> Long-lived access tokens")
            return 1
        try:
            status, body = await _api(session, ha.url, ha.token, "/api/")
        except Exception as exc:  # noqa: BLE001
            _line(BAD, "API request failed: %s" % exc)
            return 1
        if status == 401:
            _line(BAD, "token rejected (HTTP 401)")
            print("      The token is wrong, expired, or was revoked. Create a new one at")
            print("      Home Assistant -> your profile -> Security -> Long-lived access tokens,")
            print("      then paste it into Settings in the status-pi web UI.")
            return 1
        if status != 200:
            _line(BAD, "unexpected API response (HTTP %s)" % status)
            return 1
        _line(OK, "token accepted")

        # 3. does the entity exist, and does it look usable?
        status, states = await _api(session, ha.url, ha.token, "/api/states")
        states = states or []
        _line(OK, "%d entities visible" % len(states))
        entity = next((s for s in states if s.get("entity_id") == ha.entity), None)
        if not ha.entity:
            _line(BAD, "no entity configured")
            _print_candidates(states)
            return 1
        if entity is None:
            _line(BAD, "entity %s does not exist" % ha.entity)
            _print_candidates(states, ha.entity)
            return 1
        state = entity.get("state")
        _line(OK, "entity %s = %r" % (ha.entity, state))
        _explain_verdict(config, state)
        await _show_history(session, config)

        # 4. calendar entities, for the "ha" calendar provider
        status, calendars = await _api(session, ha.url, ha.token, "/api/calendars")
        if status == 200 and calendars:
            print("      calendar entities you could use as a meeting source:")
            for calendar in calendars:
                print("        %-44s %s" % (calendar.get("entity_id"), calendar.get("name", "")))
        elif status == 200:
            print("      no calendar entities in Home Assistant yet -- add one if you want")
            print("      meeting titles on the panel (Settings -> Devices & services).")

        # 5. the path the app actually uses
        try:
            async with session.ws_connect(websocket_url(ha.url), heartbeat=30) as ws:
                greeting = await ws.receive_json()
                if greeting.get("type") != "auth_required":
                    _line(BAD, "unexpected WebSocket greeting: %s" % greeting.get("type"))
                    return 1
                await ws.send_json({"type": "auth", "access_token": ha.token})
                reply = await ws.receive_json()
                if reply.get("type") != "auth_ok":
                    _line(BAD, "WebSocket auth rejected: %s" % reply.get("message", reply))
                    return 1
                await ws.send_json({
                    "id": 1, "type": "subscribe_trigger",
                    "trigger": {"platform": "state", "entity_id": ha.entity},
                })
                result = await ws.receive_json()
                if not result.get("success"):
                    _line(BAD, "subscribe failed: %s" % result.get("error"))
                    return 1
                _line(OK, "websocket subscribed -- this is what status-pi uses")

                if watch:
                    print("\nWatching %s for %ds. Turn your camera on and off now."
                          % (ha.entity, watch))
                    deadline = asyncio.get_running_loop().time() + watch
                    seen = 0
                    while asyncio.get_running_loop().time() < deadline:
                        remaining = deadline - asyncio.get_running_loop().time()
                        try:
                            message = await asyncio.wait_for(ws.receive_json(), remaining)
                        except asyncio.TimeoutError:
                            break
                        trigger = ((message.get("event") or {}).get("variables") or {}).get("trigger") or {}
                        to_state = trigger.get("to_state") or {}
                        if "state" in to_state:
                            seen += 1
                            busy = to_state["state"] in ha.busy_states
                            print("  %s -> %-12s panel would show %s"
                                  % (to_state.get("last_changed", "")[11:19],
                                     to_state["state"], "BUSY" if busy else "FREE"))
                    if not seen:
                        print("  no changes seen. If the camera was toggled, this entity is")
                        print("  not the one your automation reacts to.")
        except Exception as exc:  # noqa: BLE001
            _line(BAD, "websocket failed: %s" % exc)
            return 1

    print("\nAll checks passed.")
    return 0


async def check_calendar(config: Config, raw: bool = False) -> int:
    """Fetch the configured calendar and show what survives each stage.

    "0 events" can mean the request failed, the entity is empty, or every
    event was filtered out; this prints enough to tell those apart.
    """
    import json

    from .sources.cal import make_calendar

    provider = (config.calendar.provider or "ics").lower()
    print("Provider: %s" % provider)
    if provider == "none":
        _line(OK, "calendar is switched off; the panel shows status only")
        return 0
    if provider == "ha":
        print("Entity:   %s" % (config.calendar.ha_entity or "(not set)"))
    else:
        print("Feed:     %s" % ("set" if config.calendar.ics_url else "(not set)"))
    print()

    if not config.calendar_configured:
        _line(BAD, "calendar is not fully configured")
        if provider == "ha" and not config.ha_configured:
            print("      The 'ha' provider also needs the Home Assistant URL, token")
            print("      and camera entity filled in -- run --check-ha.")
        return 1

    feed = make_calendar(config, tz=_timezone(config))

    if raw and provider == "ha":
        payload = await _raw_ha_calendar(config)
        print("Raw response from Home Assistant (first 3 events):")
        print(json.dumps(payload[:3], indent=2) if payload else "  []")
        print()

    try:
        events = await feed.fetch()
    except Exception as exc:  # noqa: BLE001
        _line(BAD, "fetch failed: %s" % exc)
        return 1
    _line(OK, "fetch succeeded")

    if feed.note:
        _line(BAD, feed.note)
    if not events:
        _line(BAD, "no events to show")
        print("      Nothing is wrong with the connection -- the window we ask for")
        print("      is 6 hours back to 36 hours ahead, so an empty result usually")
        print("      means there is genuinely nothing in it. Try --raw to see what")
        print("      Home Assistant actually returned.")
        return 1

    now = _now(config)
    horizon = config.calendar.lookahead_hours
    _line(OK, "%d events" % len(events))
    for event in events[:10]:
        hours = (event.start - now).total_seconds() / 3600
        if event.start <= now < event.end:
            when = "NOW"
        elif hours < 0:
            when = "past"
        elif hours <= horizon:
            when = "in %.1fh" % hours
        else:
            when = "in %.1fh (beyond the %dh lookahead)" % (hours, horizon)
        print("        %-19s %-32s %s"
              % (event.start.strftime("%a %d %b %H:%M"), event.title[:32], when))
    return 0


def _timezone(config):
    from .app import load_timezone

    return load_timezone(config.timezone)


def _now(config):
    from datetime import datetime

    return datetime.now(_timezone(config))


async def _raw_ha_calendar(config):
    """The untouched JSON, for when the parsed result looks wrong."""
    import aiohttp

    from .sources.cal import WINDOW_AHEAD, WINDOW_BEHIND

    now = _now(config)
    ha = config.home_assistant
    url = "%s/api/calendars/%s" % (ha.url.rstrip("/"), config.calendar.ha_entity)
    params = {"start": (now - WINDOW_BEHIND).isoformat(),
              "end": (now + WINDOW_AHEAD).isoformat()}
    headers = {"Authorization": "Bearer %s" % ha.token}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.get(url, headers=headers, params=params) as response:
            print("GET %s -> HTTP %s" % (response.url, response.status))
            if response.status != 200:
                return []
            return await response.json()


#: states that mean "no microphone is live" on the sensors people actually use
IDLE_HINTS = ("inactive", "off", "idle", "none", "not in use")


def _explain_verdict(config, state) -> None:
    """Say what the panel would do with this state, and -- when the entity is
    not a plain on/off sensor -- what to configure so it does the right thing."""
    from .sources.ha import HomeAssistant

    ha = config.home_assistant
    link = HomeAssistant(config)
    link.connected = True
    link.state = state
    verdict = link.mic_on
    mode = (ha.busy_match or "any_of").lower()
    rule = ("anything except %s" if mode == "none_of" else "one of %s") % (
        ", ".join(repr(v) for v in ha.busy_states) or "(nothing)")

    if verdict is None:
        _line(BAD, "state is not usable, so the panel falls back to the calendar")
        return
    _line(OK, "panel would show %s  (busy when the state is %s)"
          % ("BUSY" if verdict else "FREE", rule))

    if mode == "none_of" or str(state).lower() in ("on", "off"):
        return
    # A non-binary entity under exact matching: listing every possible device
    # name is hopeless, so point at the inverse rule instead.
    idle = next((h for h in IDLE_HINTS if h in str(state).lower()), None)
    print()
    print("      This entity does not report on/off, so listing every value that")
    print("      means busy is a losing game -- plug in a different headset and it")
    print("      breaks. Set the match the other way round instead, in Settings:")
    print("        Busy when the state is: anything except")
    print("        States: %s" % (idle.title() if idle else "Inactive"))
    if not idle:
        print("      (%r looks like a live input; the idle value is whatever" % state)
        print("       it shows when nothing is using the mic -- see the history below.)")


async def _show_history(session, config, hours: int = 24) -> None:
    """Distinct values this entity has taken recently.

    This is the quickest way to see which states mean busy and which mean
    idle, without having to sit and watch it change.
    """
    from datetime import datetime, timedelta, timezone

    ha = config.home_assistant
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    path = "/api/history/period/%s?filter_entity_id=%s&minimal_response" % (since, ha.entity)
    try:
        status, payload = await _api(session, ha.url, ha.token, path)
    except Exception:  # noqa: BLE001 - history is a nicety, never fatal
        return
    if status != 200 or not payload:
        return
    rows = payload[0] if isinstance(payload[0], list) else payload
    seen = {}
    for row in rows:
        value = row.get("state")
        if value is None:
            continue
        seen[value] = seen.get(value, 0) + 1
    if len(seen) <= 1:
        return
    print()
    print("      values seen in the last %dh (most frequent first):" % hours)
    for value, count in sorted(seen.items(), key=lambda kv: -kv[1])[:8]:
        print("        %-44s %d changes" % (repr(value), count))
