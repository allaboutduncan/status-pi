import time

from status_pi.runtime import CustomStatus, RuntimeState, Timer


def test_timer_pause_resume_preserves_remaining():
    timer = Timer()
    timer.start(300, now=1000)
    assert timer.seconds_left(now=1060) == 240
    timer.pause(now=1060)
    assert timer.paused and not timer.running
    assert timer.seconds_left(now=9999) == 240, "a paused timer must not drain"
    timer.resume(now=5000)
    assert timer.seconds_left(now=5060) == 180


def test_finished_timer_is_cleared_and_flagged():
    state = RuntimeState()
    state.timer.start(10, now=1000)
    assert state.tick(now=1011) is True
    assert state.timer.active is False
    assert state.timer_done_at == 1011
    # the DONE flash does not linger for ever
    assert state.tick(now=1011 + 121) is True
    assert state.timer_done_at is None


def test_expired_status_is_dropped_on_tick():
    state = RuntimeState()
    state.status = CustomStatus("Lunch", "amber", 1000)
    assert state.tick(now=1001) is True
    assert state.status.text == ""


def test_state_survives_a_restart(tmp_path):
    path = tmp_path / "runtime.json"
    state = RuntimeState(path=path)
    state.status = CustomStatus("Deep Work", "blue", None)
    state.timer.start(600, "focus")
    state.save()

    reloaded = RuntimeState.load(path)
    assert reloaded.status.text == "Deep Work"
    assert reloaded.timer.label == "focus"
    assert 0 < reloaded.timer.seconds_left() <= 600


def test_corrupt_state_file_does_not_crash(tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text("{not json")
    state = RuntimeState.load(path)
    assert state.status.text == ""
