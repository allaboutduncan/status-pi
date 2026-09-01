"""Runtime state the device owns: the custom status and the countdown timer.

Kept separate from config.yaml and persisted to the state directory so a
reboot (or a service restart from the web UI) does not silently drop a
running timer or a status you set an hour ago.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import default_state_dir


@dataclass
class CustomStatus:
    text: str = ""
    color: str = "amber"
    #: unix timestamp, or None for "until I clear it"
    expires_at: float | None = None

    def active(self, now: float | None = None) -> bool:
        if not self.text.strip():
            return False
        if self.expires_at is None:
            return True
        return (now if now is not None else time.time()) < self.expires_at

    def seconds_left(self, now: float | None = None):
        if self.expires_at is None:
            return None
        return max(0, int(self.expires_at - (now if now is not None else time.time())))


@dataclass
class Timer:
    """A countdown.  `ends_at` drives a running timer; `remaining` holds the
    frozen value while paused, so pause/resume needs no wall-clock bookkeeping."""

    duration: int = 0
    ends_at: float | None = None
    remaining: int | None = None
    label: str = ""

    @property
    def running(self) -> bool:
        return self.ends_at is not None

    @property
    def paused(self) -> bool:
        return self.ends_at is None and self.remaining is not None

    @property
    def active(self) -> bool:
        return self.running or self.paused

    def seconds_left(self, now: float | None = None) -> int:
        if self.ends_at is not None:
            return max(0, int(round(self.ends_at - (now if now is not None else time.time()))))
        return int(self.remaining or 0)

    def finished(self, now: float | None = None) -> bool:
        return self.ends_at is not None and self.seconds_left(now) <= 0

    def start(self, seconds: int, label: str = "", now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self.duration = max(1, int(seconds))
        self.ends_at = now + self.duration
        self.remaining = None
        self.label = label

    def pause(self, now: float | None = None) -> None:
        if self.running:
            self.remaining = self.seconds_left(now)
            self.ends_at = None

    def resume(self, now: float | None = None) -> None:
        if self.paused and self.remaining:
            now = now if now is not None else time.time()
            self.ends_at = now + self.remaining
            self.remaining = None

    def stop(self) -> None:
        self.ends_at = None
        self.remaining = None
        self.duration = 0
        self.label = ""


@dataclass
class RuntimeState:
    status: CustomStatus = field(default_factory=CustomStatus)
    timer: Timer = field(default_factory=Timer)
    #: set when a timer reaches zero; cleared when the user acknowledges or
    #: starts another timer.  Drives the "DONE" flash on the panel.
    timer_done_at: float | None = None
    path: Path = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, path: Path | None = None) -> "RuntimeState":
        path = Path(path) if path else default_state_dir() / "runtime.json"
        state = cls(path=path)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return state
            state.status = CustomStatus(**(raw.get("status") or {}))
            state.timer = Timer(**(raw.get("timer") or {}))
            state.timer_done_at = raw.get("timer_done_at")
        return state

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = {
            "status": asdict(self.status),
            "timer": asdict(self.timer),
            "timer_done_at": self.timer_done_at,
        }
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self.path)

    def tick(self, now: float | None = None) -> bool:
        """Expire finished timers and stale statuses.  Returns True if
        anything changed (so the caller knows to persist)."""
        now = now if now is not None else time.time()
        changed = False
        if self.timer.finished(now):
            self.timer.stop()
            self.timer_done_at = now
            changed = True
        if self.status.text and not self.status.active(now):
            self.status = CustomStatus()
            changed = True
        # Stop nagging about a finished timer after a couple of minutes.
        if self.timer_done_at and now - self.timer_done_at > 120:
            self.timer_done_at = None
            changed = True
        return changed
