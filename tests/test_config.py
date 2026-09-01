import yaml

from status_pi.config import Config


def test_defaults_are_usable_without_a_file(tmp_path):
    config = Config.load(tmp_path / "missing.yaml")
    assert config.display.width == 480 and config.display.height == 320
    assert config.display.framebuffer == "/dev/fb1"
    assert config.ha_configured is False and config.calendar_configured is False


def test_round_trip_through_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    config = Config.from_dict({
        "timezone": "Europe/London",
        "home_assistant": {"url": "http://ha:8123", "token": "abc", "entity": "binary_sensor.cam"},
        "display": {"quiet_hours": {"start": "23:30", "brightness": 0.1}},
    })
    config.save(path)
    reloaded = Config.load(path)
    assert reloaded.timezone == "Europe/London"
    assert reloaded.home_assistant.entity == "binary_sensor.cam"
    assert reloaded.display.quiet_hours.start == "23:30"
    assert reloaded.display.quiet_hours.enabled is True, "untouched defaults survive"


def test_secrets_are_never_sent_to_the_browser():
    config = Config.from_dict({
        "home_assistant": {"token": "supersecret"},
        "calendar": {"ics_url": "https://calendar.google.com/private-xyz/basic.ics"},
        "web": {"auth_token": "hunter2"},
    })
    payload = config.to_dict(redact_secrets=True)
    dumped = yaml.safe_dump(payload)
    assert "supersecret" not in dumped
    assert "private-xyz" not in dumped
    assert "hunter2" not in dumped
    assert payload["home_assistant"]["token"] == "********"


def test_blank_secret_in_an_update_keeps_the_stored_one():
    """The UI cannot show the token, so it posts an empty string when the
    field is untouched.  That must not wipe the credential."""
    config = Config.from_dict({"home_assistant": {"token": "keepme", "url": "http://old"}})
    updated = config.merge({"home_assistant": {"token": "", "url": "http://new"}})
    assert updated.home_assistant.token == "keepme"
    assert updated.home_assistant.url == "http://new"


def test_a_real_new_secret_replaces_the_old_one():
    config = Config.from_dict({"home_assistant": {"token": "old"}})
    assert config.merge({"home_assistant": {"token": "new"}}).home_assistant.token == "new"


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("timezone: UTC\nnonsense: 12\n")
    assert Config.load(path).timezone == "UTC"


def test_example_config_matches_the_schema():
    """setup/config.example.yaml is what a fresh install starts from, so it
    must not drift from the dataclasses."""
    import pathlib

    example = pathlib.Path(__file__).resolve().parents[1] / "setup" / "config.example.yaml"
    data = yaml.safe_load(example.read_text(encoding="utf-8"))
    config = Config.from_dict(data)
    assert config.home_assistant.busy_states == ["on"]
    assert config.calendar.needs_action_is_accepted is True
    assert config.web.port == 8080
    known = set(config.to_dict())
    assert set(data) <= known, "example config has keys the code does not read"
