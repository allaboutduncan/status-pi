"""Calendar event model, kept free of any parsing dependencies so the state
machine (and its tests) can run without icalendar installed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Event:
    uid: str
    title: str
    start: datetime  # timezone-aware
    end: datetime  # timezone-aware
    all_day: bool = False
    accepted: bool = True

    def is_current(self, now: datetime) -> bool:
        return self.start <= now < self.end

    def starts_within(self, now: datetime, seconds: int) -> bool:
        delta = (self.start - now).total_seconds()
        return 0 <= delta <= seconds


def current_event(events, now: datetime):
    for event in sorted(events, key=lambda e: e.start):
        if event.is_current(now):
            return event
    return None


def next_event(events, now: datetime):
    upcoming = [e for e in events if e.start > now]
    return min(upcoming, key=lambda e: e.start) if upcoming else None
