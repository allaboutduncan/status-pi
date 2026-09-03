"""Configuration: load, validate, hot-reload, save atomically.

Config is what the user sets (credentials, layout preferences).  Anything the
device decides for itself at runtime -- the current custom status, a running
timer -- lives in runtime.py instead, so a config write never clobbers state.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path

import yaml

SECRET_FIELDS = {"token", "ics_url", "auth_token"}


def _default_config_path() -> Path:
    env = os.environ.get("STATUS_PI_CONFIG")
    if env:
        return Path(env)
    etc = Path("/etc/status-pi/config.yaml")
    if etc.exists():
        return etc
    return Path.cwd() / "config.yaml"


def default_state_dir() -> Path:
    env = os.environ.get("STATUS_PI_STATE_DIR")
    if env:
        return Path(env)
    var = Path("/var/lib/status-pi")
    if var.exists():
        return var
    return Path.cwd() / "var"


@dataclass
class HAConfig:
    url: str = ""
    token: str = ""
    entity: str = ""
    #: entity states that mean "microphone/camera live"
    busy_states: list = field(default_factory=lambda: ["on"])


@dataclass
class CalendarConfig:
    #: where meetings come from:
    #:   "ics"  - a Google secret iCal URL (needs the Workspace admin to have
    #:            enabled private iCal addresses)
    #:   "ha"   - a calendar entity in Home Assistant, read over the
    #:            connection we already hold open
    #:   "none" - no calendar; the panel shows status, clock and timers only
    provider: str = "ics"
    #: which calendar entity to read when provider is "ha"
    ha_entity: str = ""
    ics_url: str = ""
    email: str = ""
    refresh_seconds: int = 300
    #: only show events you have accepted (or organise)
    require_accepted: bool = True
    #: Google's secret feed is inconsistent about PARTSTAT; when it says
    #: NEEDS-ACTION, treat that as accepted rather than hiding the meeting.
    needs_action_is_accepted: bool = True
    include_all_day: bool = False
    #: how long before a meeting the next-up line starts showing it
    lookahead_hours: int = 12
    hidden_uids: list = field(default_factory=list)


@dataclass
class QuietHours:
    enabled: bool = True
    start: str = "22:00"
    end: str = "07:00"
    brightness: float = 0.25
    #: drop everything but the clock during quiet hours
    clock_only: bool = False


@dataclass
class DisplayConfig:
    framebuffer: str = "/dev/fb1"
    width: int = 480
    height: int = 320
    brightness: float = 1.0
    #: 0/180 flip the rendered image; use this if the ribbon ends up on the
    #: wrong side once the panel is on the wall.
    rotate: int = 0
    time_24h: bool = True
    show_seconds: bool = False
    quiet_hours: QuietHours = field(default_factory=QuietHours)


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    #: optional shared secret; empty means open on the LAN
    auth_token: str = ""


@dataclass
class Config:
    timezone: str = "UTC"
    home_assistant: HAConfig = field(default_factory=HAConfig)
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    web: WebConfig = field(default_factory=WebConfig)
    path: Path = field(default=None, repr=False, compare=False)

    # -- (de)serialisation -------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict, path: Path | None = None) -> "Config":
        return _build(cls, data or {}, path=path)

    def to_dict(self, redact_secrets: bool = False) -> dict:
        data = {k: v for k, v in asdict(self).items() if k != "path"}
        if redact_secrets:
            data = _redact(data)
        return data

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = Path(path) if path else _default_config_path()
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        else:
            raw = {}
        return cls.from_dict(raw, path=path)

    def save(self, path: Path | None = None) -> Path:
        """Write via temp file + replace so a crash mid-write cannot leave a
        truncated config on the SD card."""
        target = Path(path or self.path or _default_config_path())
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(
            yaml.safe_dump(self.to_dict(), sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        os.replace(tmp, target)
        self.path = target
        return target

    def merge(self, updates: dict) -> "Config":
        """Apply a partial update (as posted by the web UI) and return a new
        Config.  Empty strings for secrets mean 'leave unchanged'."""
        current = self.to_dict()
        _deep_merge(current, updates or {}, keep_empty_secrets=True)
        return Config.from_dict(current, path=self.path)

    # -- convenience -------------------------------------------------------
    @property
    def ha_configured(self) -> bool:
        ha = self.home_assistant
        return bool(ha.url and ha.token and ha.entity)

    @property
    def calendar_configured(self) -> bool:
        provider = (self.calendar.provider or "ics").lower()
        if provider == "none":
            return False
        if provider == "ha":
            return bool(self.calendar.ha_entity and self.ha_configured)
        return bool(self.calendar.ics_url)


#: nested dataclasses by field name -- `from __future__ import annotations`
#: turns the field types into strings, so resolve them explicitly.
_NESTED = {
    "home_assistant": HAConfig,
    "calendar": CalendarConfig,
    "display": DisplayConfig,
    "web": WebConfig,
    "quiet_hours": QuietHours,
}


def _build(cls, data: dict, path=None):
    known = {f.name for f in fields(cls)}
    kwargs = {}
    for key, value in (data or {}).items():
        if key not in known or key == "path":
            continue
        if key in _NESTED:
            kwargs[key] = _build(_NESTED[key], value or {})
        else:
            kwargs[key] = value
    obj = cls(**kwargs)
    if path is not None and "path" in known:
        obj.path = path
    return obj


def _deep_merge(base: dict, updates: dict, keep_empty_secrets: bool = False) -> dict:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value, keep_empty_secrets)
        else:
            if keep_empty_secrets and key in SECRET_FIELDS and value == "":
                continue
            base[key] = value
    return base


def _redact(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        if isinstance(value, dict):
            out[key] = _redact(value)
        elif key in SECRET_FIELDS:
            out[key] = "********" if value else ""
        else:
            out[key] = value
    return out
