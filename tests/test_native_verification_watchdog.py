"""Independent watchdog state machine; no real input is sent by tests."""

import sys
from io import BytesIO
from types import SimpleNamespace

import pytest
from native_verification.contracts import DesktopError
from native_verification.watchdog import ReleaseWatchdog, _watch_loop


class Script:
    def __init__(self, entries, *, parent_alive=True, release_ok=True):
        self.entries = iter(entries)
        self.time = 0.0
        self.parent_alive = parent_alive
        self.release_ok = release_ok
        self.releases = 0
        self.messages = []

    def read(self, timeout):
        delay, value = next(self.entries, (timeout, None))
        self.time += delay
        return value

    def release(self):
        self.releases += 1
        return self.release_ok

    def run(self):
        return _watch_loop(self.read, self.messages.append, self.release,
                           lambda: self.parent_alive, lambda: self.time)


def test_unarmed_eof_never_sends_input():
    script = Script([(0, "eof")])
    assert script.run() is True
    assert script.messages == ["ready"] and script.releases == 0


@pytest.mark.parametrize("ending", ["eof", "close", "invalid"])
def test_armed_parent_disconnect_or_bad_protocol_releases(ending):
    script = Script([(0, "arm"), (0.1, ending)])
    assert script.run() is True
    assert script.releases == 1 and "armed" in script.messages


def test_missed_heartbeat_releases_even_with_live_parent():
    script = Script([(0, "arm"), (1.6, None)])
    assert script.run() is True
    assert script.releases == 1


def test_heartbeat_never_extends_absolute_thirty_second_ceiling():
    script = Script([(0, "arm")] + [(0.25, "pulse")] * 130)
    assert script.run() is True
    assert 30 <= script.time <= 30.25 and script.releases == 1


def test_confirmed_release_disarms_then_eof_does_not_send_extra_up():
    script = Script([(0, "arm"), (0.1, "disarm"), (0, "eof")])
    assert script.run() is True
    assert script.messages == ["ready", "armed", "disarmed"]
    assert script.releases == 0


def test_crashed_parent_is_detected_without_waiting_for_pipe_eof():
    script = Script([(0, "arm"), (0.1, None)])
    def parent_alive():
        return script.time == 0
    assert _watch_loop(script.read, script.messages.append, script.release,
                       parent_alive, lambda: script.time) is True
    assert script.releases == 1


def test_repeated_arm_is_rejected_and_cannot_reset_hard_deadline():
    script = Script([(0, "arm"), (0.1, "arm")])
    assert script.run() is True
    assert script.releases == 1


def test_failed_release_is_retried_boundedly_and_never_claims_success():
    script = Script([(0, "arm"), (0.1, "eof")], release_ok=False)
    assert script.run() is False
    assert 1 < script.releases <= 5 and "failed" in script.messages


def test_release_exceptions_are_retried_in_the_independent_process():
    script = Script([(0, "arm"), (0.1, "eof")])
    attempts = []
    def release():
        attempts.append(True)
        if len(attempts) < 3:
            raise DesktopError("release-failed")
        return True
    assert _watch_loop(script.read, script.messages.append, release,
                       lambda: True, lambda: script.time)
    assert len(attempts) == 3 and script.messages[-1] == "released"


def test_failed_cross_process_release_gate_never_acknowledges_arm():
    script = Script([(0, "arm")])
    assert not _watch_loop(script.read, script.messages.append, script.release,
                           lambda: True, lambda: script.time, arm_guard=lambda: False)
    assert script.messages == ["ready", "failed"] and script.releases == 0


def test_parent_death_while_acquiring_release_gate_exits_without_arming_or_up():
    script = Script([(0, "arm")])

    def acquire_gate():
        script.parent_alive = False
        return True

    assert _watch_loop(script.read, script.messages.append, script.release,
                       lambda: script.parent_alive, lambda: script.time,
                       arm_guard=acquire_gate)
    assert script.messages == ["ready"] and script.releases == 0


def test_idempotent_confirmed_no_press_disarm_never_sends_up():
    script = Script([(0, "arm"), (0.4, "disarm"), (0.1, "disarm"), (0, "eof")])
    assert script.run()
    assert script.messages == ["ready", "armed", "disarmed", "disarmed"]
    assert script.releases == 0


def test_already_queued_confirmed_release_disarm_wins_over_expired_heartbeat():
    script = Script([(0, "arm"), (1.6, "disarm"), (0, "eof")])
    assert script.run()
    assert script.messages == ["ready", "armed", "disarmed"]
    assert script.releases == 0


def test_lost_arm_ack_then_confirmed_no_press_disarm_drains_late_arm_ack(monkeypatch):
    import native_verification.watchdog as module

    watchdog = ReleaseWatchdog()
    watchdog.process = SimpleNamespace(stdin=BytesIO(), poll=lambda: None)
    monkeypatch.setattr(module, "_ARM_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(module, "_ARM_SECONDS", 0.01)
    with pytest.raises(DesktopError, match="watchdog-unavailable"):
        watchdog.arm()
    watchdog.messages.put("armed")
    watchdog.messages.put("disarmed")
    watchdog.disarm()
    assert not watchdog.armed
    assert watchdog.process.stdin.getvalue() == b"arm\ndisarm\n"


def test_arm_and_disarm_ack_budgets_fit_inside_child_heartbeat_window():
    import native_verification.watchdog as module

    assert module._ARM_SECONDS + module._DISARM_SECONDS < module._HEARTBEAT_SECONDS


@pytest.mark.skipif(sys.platform != "win32", reason="Harmless Windows child startup only")
def test_real_watchdog_unarmed_start_and_close_never_injects_input():
    watchdog = ReleaseWatchdog()
    try:
        watchdog.start()
        assert watchdog.process.poll() is None
        assert not watchdog.armed
    finally:
        watchdog.close()
    assert watchdog.process.returncode == 0


def test_missing_arm_ack_fails_boundedly_and_preserves_suspected_arm(monkeypatch):
    import native_verification.watchdog as module

    class FakeProcess:
        def __init__(self):
            self.stdin = BytesIO()

        def poll(self):
            return None

    monkeypatch.setattr(module, "_ARM_SECONDS", 0.01)
    watchdog = ReleaseWatchdog()
    watchdog.process = FakeProcess()
    with pytest.raises(DesktopError, match="watchdog-unavailable"):
        watchdog.arm()
    assert watchdog.armed and watchdog.used
    assert watchdog.process.stdin.getvalue() == b"arm\n"


def test_watchdog_disarm_requires_acknowledgement_before_clearing_armed(monkeypatch):
    import native_verification.watchdog as module

    monkeypatch.setattr(module, "_DISARM_SECONDS", 0.01)
    watchdog = ReleaseWatchdog()
    watchdog.process = SimpleNamespace(stdin=BytesIO(), poll=lambda: None)
    watchdog.armed = True
    with pytest.raises(DesktopError, match="watchdog-unavailable"):
        watchdog.disarm()
    assert watchdog.armed


def test_watchdog_fails_closed_without_starting_on_unsupported_platform(monkeypatch):
    import native_verification.watchdog as module

    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    watchdog = ReleaseWatchdog()
    with pytest.raises(DesktopError, match="unsupported-platform"):
        watchdog.start()
    assert watchdog.process is None
