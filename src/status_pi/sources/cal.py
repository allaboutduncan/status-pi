"""Google Calendar over the secret iCal (.ics) address.

The feed is fetched on a timer, expanded through its recurrence rules and
filtered down to meetings actually worth showing: not cancelled, not
transparent, and -- when require_accepted is on -- ones you accepted or
organised.

Two known weaknesses of the secret feed are handled here rather than hidden:
it can lag real changes by up to a few hours (we surface the fetch time so
the web UI can show it), and its PARTSTAT is inconsistent (hence
needs_action_is_accepted, on by default, so a meeting is never silently
dropped).  If the lag becomes a problem the fix is to swap this module for
the Google Calendar API; nothing above it would change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from ..events import Event

log = logging.getLogger(__name__)

WINDOW_BEHIND = timedelta(hours=6)
WINDOW_AHEAD = timedelta(hours=36)


def _text(value, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


def _attendee_status(component, email: str):
    """PARTSTAT for `email` on this event, or None if they are not listed."""
    if not email:
        return None
    email = email.strip().lower()
    organiser = _text(component.get("ORGANIZER")).lower().replace("mailto:", "")
    if organiser == email:
        return "ORGANIZER"
    attendees = component.get("ATTENDEE")
    if attendees is None:
        return None
    if not isinstance(attendees, list):
        attendees = [attendees]
    for attendee in attendees:
        address = str(attendee).lower().replace("mailto:", "")
        if address != email:
            continue
        params = getattr(attendee, "params", {}) or {}
        return str(params.get("PARTSTAT", "NEEDS-ACTION")).upper()
    return None


def _as_aware(value, tz) -> datetime:
    """iCal gives plain dates for all-day events and naive datetimes for
    floating ones; normalise everything to an aware datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=tz)
    return datetime(value.year, value.month, value.day, tzinfo=tz)


def parse_ics(data, config, now: datetime, tz=timezone.utc):
    """Parse and filter an iCal feed into the events we will display."""
    import icalendar
    import recurring_ical_events

    calendar = icalendar.Calendar.from_ical(data)
    occurrences = recurring_ical_events.of(calendar).between(
        now - WINDOW_BEHIND, now + WINDOW_AHEAD)

    cfg = config.calendar
    events = []
    for component in occurrences:
        uid = _text(component.get("UID"), "?")
        if uid in (cfg.hidden_uids or []):
            continue
        if _text(component.get("STATUS")).upper() == "CANCELLED":
            continue
        if _text(component.get("TRANSP")).upper() == "TRANSPARENT":
            continue

        start_prop = component.get("DTSTART")
        end_prop = component.get("DTEND") or component.get("DTSTART")
        if start_prop is None:
            continue
        all_day = not isinstance(start_prop.dt, datetime)
        if all_day and not cfg.include_all_day:
            continue

        partstat = _attendee_status(component, cfg.email)
        accepted = True
        if cfg.require_accepted and partstat is not None:
            if partstat == "DECLINED":
                continue
            if partstat == "NEEDS-ACTION" and not cfg.needs_action_is_accepted:
                continue
            accepted = partstat in ("ACCEPTED", "ORGANIZER")

        start = _as_aware(start_prop.dt, tz)
        end = _as_aware(end_prop.dt, tz)
        if end <= start:
            end = start + timedelta(minutes=30)
        events.append(Event(
            uid=uid,
            title=_text(component.get("SUMMARY"), "(no title)"),
            start=start,
            end=end,
            all_day=all_day,
            accepted=accepted,
        ))
    events.sort(key=lambda e: e.start)
    return events


class BaseCalendarFeed:
    """Polling, caching and health reporting, shared by every provider.

    Subclasses only implement `fetch()`.  Keeping the last good copy on disk
    means a boot with no network still shows this morning's meetings instead
    of an empty panel.
    """

    name = "calendar"

    def __init__(self, config, tz=timezone.utc, cache_path=None, on_change=None):
        self.config = config
        self.tz = tz
        self.cache_path = cache_path
        self.on_change = on_change
        self.events = []
        self.ok = False
        self.last_fetch = None
        self.last_error = None
        self._task = None
        self._stop = asyncio.Event()
        self._load_cache()

    # -- to implement ------------------------------------------------------
    async def fetch(self):
        raise NotImplementedError

    # -- public ------------------------------------------------------------
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name=self.name)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    def refresh_soon(self) -> None:
        """Ask for an immediate re-fetch, e.g. after a config change."""
        self._stop.set()

    def describe(self) -> dict:
        return {
            "provider": self.config.calendar.provider,
            "configured": self.config.calendar_configured,
            "ok": self.ok,
            "events": len(self.events),
            "last_fetch": self.last_fetch,
            "last_error": self.last_error,
            "stale_seconds": None if not self.last_fetch else int(time.time() - self.last_fetch),
        }

    # -- internals ---------------------------------------------------------
    async def _run(self) -> None:
        while True:
            interval = max(60, self.config.calendar.refresh_seconds)
            if self.config.calendar_configured:
                try:
                    await self.fetch_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - never let the loop die
                    self.ok = False
                    self.last_error = str(exc)
                    log.warning("calendar fetch failed: %s", exc)
                    interval = min(interval, 120)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                self._stop = asyncio.Event()  # refresh_soon: loop straight round
            except asyncio.TimeoutError:
                pass

    async def fetch_once(self) -> None:
        events = await self.fetch()
        changed = [(e.uid, e.start) for e in events] != [(e.uid, e.start) for e in self.events]
        self.events = events
        self.ok = True
        self.last_error = None
        self.last_fetch = time.time()
        self._save_cache()
        log.info("%s: %d events", self.name, len(events))
        if changed and self.on_change:
            self.on_change()

    def _load_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.events = [
                Event(
                    uid=item["uid"],
                    title=item["title"],
                    start=datetime.fromisoformat(item["start"]),
                    end=datetime.fromisoformat(item["end"]),
                    all_day=item.get("all_day", False),
                    accepted=item.get("accepted", True),
                )
                for item in raw.get("events", [])
            ]
            self.last_fetch = raw.get("fetched_at")
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            self.events = []

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        payload = {
            "fetched_at": self.last_fetch,
            "events": [
                {
                    "uid": e.uid, "title": e.title,
                    "start": e.start.isoformat(), "end": e.end.isoformat(),
                    "all_day": e.all_day, "accepted": e.accepted,
                }
                for e in self.events
            ],
        }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except OSError as exc:
            log.debug("could not write calendar cache: %s", exc)


class IcsCalendarFeed(BaseCalendarFeed):
    """Google Calendar via its secret iCal address."""

    name = "calendar(ics)"

    async def fetch(self):
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(self.config.calendar.ics_url) as response:
                response.raise_for_status()
                data = await response.read()
        now = datetime.now(self.tz)
        return await asyncio.get_running_loop().run_in_executor(
            None, parse_ics, data, self.config, now, self.tz)


class HACalendarFeed(BaseCalendarFeed):
    """A calendar entity in Home Assistant, read over its REST API.

    This is the way in when Google Workspace will not hand out a private iCal
    address: whatever Home Assistant can see -- a Google account it holds
    OAuth for, CalDAV, a local calendar -- the panel can show, using the same
    URL and token we already have.

    Home Assistant has already applied its own filtering by the time we see
    these events, so there is no PARTSTAT to inspect here.
    """

    name = "calendar(ha)"

    async def fetch(self):
        import aiohttp

        ha = self.config.home_assistant
        entity = self.config.calendar.ha_entity
        now = datetime.now(self.tz)
        params = {
            "start": (now - WINDOW_BEHIND).isoformat(),
            "end": (now + WINDOW_AHEAD).isoformat(),
        }
        url = "%s/api/calendars/%s" % (ha.url.rstrip("/"), entity)
        headers = {"Authorization": "Bearer %s" % ha.token}
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, params=params) as response:
                if response.status == 404:
                    raise RuntimeError("calendar entity %s does not exist" % entity)
                response.raise_for_status()
                payload = await response.json()
        return self.parse(payload)

    def parse(self, payload):
        events = []
        for item in payload or []:
            start, start_all_day = self._moment(item.get("start"))
            end, _ = self._moment(item.get("end"))
            if start is None:
                continue
            if start_all_day and not self.config.calendar.include_all_day:
                continue
            if end is None or end <= start:
                end = start + timedelta(minutes=30)
            title = _text(item.get("summary"), "(no title)")
            # Home Assistant does not promise a uid, so derive a stable one.
            uid = _text(item.get("uid")) or "%s@%s" % (start.isoformat(), title)
            if uid in (self.config.calendar.hidden_uids or []):
                continue
            events.append(Event(uid=uid, title=title, start=start, end=end,
                                all_day=start_all_day, accepted=True))
        events.sort(key=lambda e: e.start)
        return events

    def _moment(self, value):
        """HA sends {"dateTime": ...} for timed events and {"date": ...} for
        all-day ones.  Returns (datetime, is_all_day)."""
        if not isinstance(value, dict):
            return None, False
        if value.get("dateTime"):
            moment = datetime.fromisoformat(value["dateTime"])
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=self.tz)
            return moment.astimezone(self.tz), False
        if value.get("date"):
            day = datetime.fromisoformat(value["date"])
            return day.replace(tzinfo=self.tz), True
        return None, False


class NullCalendarFeed(BaseCalendarFeed):
    """No calendar at all: the panel is a pure status board."""

    name = "calendar(off)"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = []

    def start(self) -> None:
        return

    async def stop(self) -> None:
        return

    async def fetch(self):
        return []


PROVIDERS = {"ics": IcsCalendarFeed, "ha": HACalendarFeed, "none": NullCalendarFeed}


def make_calendar(config, tz=timezone.utc, cache_path=None, on_change=None):
    """Build the feed named by `calendar.provider`, defaulting to iCal."""
    provider = (config.calendar.provider or "ics").lower()
    feed = PROVIDERS.get(provider, IcsCalendarFeed)
    if provider not in PROVIDERS:
        log.warning("unknown calendar provider %r, using ics", provider)
    return feed(config, tz=tz, cache_path=cache_path, on_change=on_change)
