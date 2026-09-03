"""Home Assistant link: a long-lived WebSocket subscription to one entity.

Rather than polling, we open a WebSocket, authenticate with a long-lived
access token and subscribe to a state trigger for the single entity that
tracks the work laptop's camera/microphone.  State changes are then pushed to
us within milliseconds, and the connection costs nothing while idle.

`connected` is part of the contract: when it is False the state machine stops
trusting `mic_on` and falls back to the calendar.
"""

from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)

RECONNECT_MIN = 2
RECONNECT_MAX = 60
PING_INTERVAL = 30


def websocket_url(base_url: str) -> str:
    url = (base_url or "").rstrip("/")
    if url.startswith("https://"):
        url = "wss://" + url[len("https://"):]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://"):]
    elif not url.startswith(("ws://", "wss://")):
        url = "ws://" + url
    if not url.endswith("/api/websocket"):
        url += "/api/websocket"
    return url


class HomeAssistant:
    """Tracks one entity over the WebSocket API and reconnects forever."""

    def __init__(self, config, on_change=None):
        self.config = config
        self.on_change = on_change
        self.connected = False
        self.state = None  # raw entity state string, e.g. "on"
        #: connected, but the configured entity is not in Home Assistant
        self.entity_missing = False
        self.last_change = None
        self.last_error = None
        self._task = None
        self._stop = asyncio.Event()
        self._msg_id = 0

    # -- public ------------------------------------------------------------
    @property
    def mic_on(self):
        """True/False when we know, None when we do not.

        Two ways to read the entity, because the useful ones are not all
        binary.  A camera binary_sensor is `any_of ["on"]`; an "active audio
        input" sensor reports whichever microphone is live and "Inactive"
        when none is, which is `none_of ["Inactive"]`.
        """
        if not self.connected or self.state is None:
            return None
        state = str(self.state).strip()
        if state.lower() in ("unknown", "unavailable", ""):
            return None
        ha = self.config.home_assistant
        listed = {str(value).strip().lower() for value in (ha.busy_states or [])}
        if (ha.busy_match or "any_of").lower() == "none_of":
            return state.lower() not in listed
        return state.lower() in listed

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="home-assistant")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    def describe(self) -> dict:
        return {
            "configured": self.config.ha_configured,
            "connected": self.connected,
            "entity": self.config.home_assistant.entity,
            "state": self.state,
            "mic_on": self.mic_on,
            "entity_missing": self.entity_missing,
            "last_change": self.last_change,
            "last_error": self.last_error,
        }

    # -- internals ---------------------------------------------------------
    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _set_state(self, value) -> None:
        if value != self.state:
            log.info("%s -> %s", self.config.home_assistant.entity, value)
            self.state = value
            self.last_change = time.time()
            if self.on_change:
                self.on_change()

    async def _run(self) -> None:
        backoff = RECONNECT_MIN
        while not self._stop.is_set():
            if not self.config.ha_configured:
                await asyncio.sleep(5)
                continue
            try:
                await self._session()
                backoff = RECONNECT_MIN
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - never let the loop die
                self.last_error = str(exc)
                log.warning("home assistant: %s (retry in %ss)", exc, backoff)
            finally:
                if self.connected:
                    self.connected = False
                    if self.on_change:
                        self.on_change()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(RECONNECT_MAX, backoff * 2)

    async def _session(self) -> None:
        import aiohttp

        ha = self.config.home_assistant
        url = websocket_url(ha.url)
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(url, heartbeat=PING_INTERVAL) as ws:
                await self._authenticate(ws, ha.token)
                await self._subscribe(ws, ha.entity)
                await self._fetch_initial(session, ha)
                self.connected = True
                if not self.entity_missing:
                    self.last_error = None
                log.info("home assistant connected (%s)", url)
                if self.on_change:
                    self.on_change()
                async for message in ws:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        break
                    self._handle(message.json())

    async def _authenticate(self, ws, token: str) -> None:
        greeting = await ws.receive_json()
        if greeting.get("type") != "auth_required":
            raise RuntimeError("unexpected greeting: %s" % greeting.get("type"))
        await ws.send_json({"type": "auth", "access_token": token})
        reply = await ws.receive_json()
        if reply.get("type") != "auth_ok":
            raise RuntimeError("authentication rejected (%s)" % reply.get("message", reply.get("type")))

    async def _subscribe(self, ws, entity: str) -> None:
        await ws.send_json({
            "id": self._next_id(),
            "type": "subscribe_trigger",
            "trigger": {"platform": "state", "entity_id": entity},
        })
        reply = await ws.receive_json()
        if not reply.get("success", False):
            raise RuntimeError("subscribe failed: %s" % reply.get("error"))

    async def _fetch_initial(self, session, ha) -> None:
        """The trigger only fires on change, so read the current value once.

        A missing entity is reported, not raised: the subscription is valid
        either way, and reconnecting in a loop would only hide the real
        problem (usually a typo in the entity id)."""
        url = "%s/api/states/%s" % (ha.url.rstrip("/"), ha.entity)
        headers = {"Authorization": "Bearer %s" % ha.token}
        async with session.get(url, headers=headers) as response:
            if response.status == 404:
                self.entity_missing = True
                self.last_error = "entity %s does not exist" % ha.entity
                log.warning("%s -- check Settings, or run --check-ha", self.last_error)
                self._set_state(None)
                return
            response.raise_for_status()
            payload = await response.json()
        self.entity_missing = False
        self._set_state(payload.get("state"))

    def _handle(self, message: dict) -> None:
        if message.get("type") != "event":
            return
        variables = (message.get("event") or {}).get("variables") or {}
        trigger = variables.get("trigger") or {}
        new_state = trigger.get("to_state") or {}
        if "state" in new_state:
            self._set_state(new_state["state"])
