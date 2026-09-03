"""The Home Assistant calendar provider.

This is the route in when a Google Workspace account will not hand out a
private iCal address, so it needs to cope with whatever shape Home Assistant
sends: no uid, all-day dates, naive datetimes, unordered events.
"""

from datetime import datetime, timezone

import pytest

from status_pi.config import Config
from status_pi.sources.cal import (
    HACalendarFeed, IcsCalendarFeed, NullCalendarFeed, make_calendar,
)

TZ = timezone.utc


@pytest.fixture
def feed():
    config = Config.from_dict({
        "calendar": {"provider": "ha", "ha_entity": "calendar.work"},
        "home_assistant": {"url": "http://ha", "token": "t", "entity": "binary_sensor.cam"},
    })
    return HACalendarFeed(config, tz=TZ)


def test_timed_events_are_parsed(feed):
    events = feed.parse([
        {"summary": "Sprint Review",
         "start": {"dateTime": "2026-09-01T09:30:00+00:00"},
         "end": {"dateTime": "2026-09-01T10:30:00+00:00"}},
    ])
    assert len(events) == 1
    assert events[0].title == "Sprint Review"
    assert events[0].start == datetime(2026, 9, 1, 9, 30, tzinfo=TZ)
    assert events[0].all_day is False


def test_events_are_sorted_by_start(feed):
    events = feed.parse([
        {"summary": "Later", "start": {"dateTime": "2026-09-01T15:00:00+00:00"},
         "end": {"dateTime": "2026-09-01T16:00:00+00:00"}},
        {"summary": "Earlier", "start": {"dateTime": "2026-09-01T09:00:00+00:00"},
         "end": {"dateTime": "2026-09-01T10:00:00+00:00"}},
    ])
    assert [e.title for e in events] == ["Earlier", "Later"]


def test_missing_uid_gets_a_stable_one(feed):
    """Home Assistant does not promise a uid, but the tick loop compares them
    to decide whether anything changed, so they must not wobble."""
    payload = [{"summary": "Standup", "start": {"dateTime": "2026-09-01T09:00:00+00:00"},
                "end": {"dateTime": "2026-09-01T09:15:00+00:00"}}]
    first, second = feed.parse(payload), feed.parse(payload)
    assert first[0].uid == second[0].uid
    assert first[0].uid


def test_all_day_events_follow_the_same_setting_as_ics(feed):
    payload = [{"summary": "Public Holiday", "start": {"date": "2026-09-01"},
                "end": {"date": "2026-09-02"}}]
    assert feed.parse(payload) == []
    feed.config.calendar.include_all_day = True
    events = feed.parse(payload)
    assert events[0].all_day is True and events[0].title == "Public Holiday"


def test_naive_datetimes_are_given_a_timezone(feed):
    events = feed.parse([{"summary": "Floating",
                          "start": {"dateTime": "2026-09-01T09:00:00"},
                          "end": {"dateTime": "2026-09-01T10:00:00"}}])
    assert events[0].start.tzinfo is not None


def test_zero_length_and_untitled_events_are_survivable(feed):
    events = feed.parse([
        {"start": {"dateTime": "2026-09-01T09:00:00+00:00"},
         "end": {"dateTime": "2026-09-01T09:00:00+00:00"}},
        {"summary": "Broken", "start": None, "end": None},
    ])
    assert len(events) == 1
    assert events[0].title == "(no title)"
    assert events[0].end > events[0].start


def test_hidden_uids_are_respected(feed):
    payload = [{"uid": "abc", "summary": "Hidden",
                "start": {"dateTime": "2026-09-01T09:00:00+00:00"},
                "end": {"dateTime": "2026-09-01T10:00:00+00:00"}}]
    assert len(feed.parse(payload)) == 1
    feed.config.calendar.hidden_uids = ["abc"]
    assert feed.parse(payload) == []


@pytest.mark.parametrize("provider,expected", [
    ("ics", IcsCalendarFeed), ("ha", HACalendarFeed),
    ("none", NullCalendarFeed), ("nonsense", IcsCalendarFeed),
])
def test_factory_picks_the_provider(provider, expected):
    config = Config.from_dict({"calendar": {"provider": provider}})
    assert isinstance(make_calendar(config), expected)


def test_disabling_the_calendar_leaves_a_working_panel():
    """'none' must behave like a real feed that simply never has events, so
    nothing downstream needs to special-case it."""
    config = Config.from_dict({"calendar": {"provider": "none"}})
    feed = make_calendar(config)
    feed.start()  # a no-op, but must not raise
    assert feed.events == []
    assert feed.describe()["configured"] is False
