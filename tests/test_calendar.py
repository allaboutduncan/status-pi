"""Filtering rules for the Google secret iCal feed."""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("icalendar")
pytest.importorskip("recurring_ical_events")

from status_pi.config import Config  # noqa: E402
from status_pi.sources.cal import parse_ics  # noqa: E402

NOW = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
ME = "me@example.com"


def ics(*events):
    body = "".join(events)
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
            + body + "END:VCALENDAR\r\n").encode()


def event(uid, summary, start, minutes=30, partstat="ACCEPTED", extra=""):
    end = start + timedelta(minutes=minutes)
    fmt = "%Y%m%dT%H%M%SZ"
    attendee = ""
    if partstat:
        attendee = "ATTENDEE;PARTSTAT=%s:mailto:%s\r\n" % (partstat, ME)
    return ("BEGIN:VEVENT\r\nUID:%s\r\nSUMMARY:%s\r\nDTSTART:%s\r\nDTEND:%s\r\n%s%s"
            "END:VEVENT\r\n" % (uid, summary, start.strftime(fmt), end.strftime(fmt),
                                attendee, extra))


@pytest.fixture
def config():
    return Config.from_dict({"calendar": {"ics_url": "http://x", "email": ME}})


def parse(data, config):
    return parse_ics(data, config, NOW, timezone.utc)


def test_accepted_meeting_is_kept(config):
    events = parse(ics(event("a", "Standup", NOW + timedelta(hours=1))), config)
    assert [e.title for e in events] == ["Standup"]


def test_declined_meeting_is_dropped(config):
    data = ics(event("a", "Optional Sync", NOW + timedelta(hours=1), partstat="DECLINED"))
    assert parse(data, config) == []


def test_needs_action_is_kept_by_default(config):
    """Google's feed reports NEEDS-ACTION for plenty of accepted meetings, so
    the safe default is to show them rather than silently hide a meeting."""
    data = ics(event("a", "Maybe", NOW + timedelta(hours=1), partstat="NEEDS-ACTION"))
    assert len(parse(data, config)) == 1


def test_needs_action_can_be_hidden(config):
    config.calendar.needs_action_is_accepted = False
    data = ics(event("a", "Maybe", NOW + timedelta(hours=1), partstat="NEEDS-ACTION"))
    assert parse(data, config) == []


def test_cancelled_and_transparent_are_dropped(config):
    data = ics(
        event("a", "Cancelled", NOW + timedelta(hours=1), extra="STATUS:CANCELLED\r\n"),
        event("b", "Focus block", NOW + timedelta(hours=2), extra="TRANSP:TRANSPARENT\r\n"),
        event("c", "Real meeting", NOW + timedelta(hours=3)),
    )
    assert [e.title for e in parse(data, config)] == ["Real meeting"]


def test_all_day_events_are_hidden_unless_asked_for(config):
    data = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\nBEGIN:VEVENT\r\n"
            "UID:d\r\nSUMMARY:Public Holiday\r\nDTSTART;VALUE=DATE:20260901\r\n"
            "DTEND;VALUE=DATE:20260902\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n").encode()
    assert parse(data, config) == []
    config.calendar.include_all_day = True
    assert [e.title for e in parse(data, config)] == ["Public Holiday"]


def test_recurring_meetings_are_expanded(config):
    data = ics(event("r", "Daily Standup", NOW - timedelta(days=30),
                     extra="RRULE:FREQ=DAILY\r\n"))
    events = parse(data, config)
    assert len(events) >= 2, "the window covers today and tomorrow"
    assert all(e.title == "Daily Standup" for e in events)


def test_hidden_uids_are_respected(config):
    config.calendar.hidden_uids = ["a"]
    data = ics(event("a", "Wrongly shown", NOW + timedelta(hours=1)),
               event("b", "Keep", NOW + timedelta(hours=2)))
    assert [e.title for e in parse(data, config)] == ["Keep"]


def test_events_come_back_in_start_order(config):
    data = ics(event("b", "Second", NOW + timedelta(hours=3)),
               event("a", "First", NOW + timedelta(hours=1)))
    assert [e.title for e in parse(data, config)] == ["First", "Second"]
