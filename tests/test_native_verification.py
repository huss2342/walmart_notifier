"""Native input protocol and lease safety; never injects real input."""

import io
import json
import struct
import threading
import time

import pytest
from native_verification.contracts import DesktopError, Target
from native_verification.controller import NativeController
from native_verification.host import read_frame, run_session, validate_launch, validate_request


class Desktop:
    def __init__(self):
        self.reason = None
        self.down = 0
        self.up = 0
        self.fail_press = False
        self.fail_release = False

    def probe(self):
        return {"ready": True, "reason": None, "idleMs": 6000}

    def prepare(self, viewport, point):
        return Target(1, 2, 100, 100, (0, 0, 800, 600), 99)

    def press(self, target):
        self.down += 1
        if self.fail_press:
            raise DesktopError("input-failed")
        return target

    def abort_reason(self, target):
        return self.reason

    def release(self):
        self.up += 1
        if self.fail_release:
            raise DesktopError("release-failed")

    def close(self):
        pass


class Watchdog:
    def __init__(self):
        self.armed = False
        self.disarms = 0
        self.pulses = 0
        self.fail_arm = False
        self.fail_disarm = False
        self.releases = 0

    def start(self):
        pass

    def arm(self):
        if self.fail_arm:
            raise DesktopError("watchdog-unavailable")
        self.armed = True

    def pulse(self):
        self.pulses += 1

    def disarm(self):
        if self.fail_disarm:
            raise DesktopError("watchdog-unavailable")
        self.disarms += 1
        self.armed = False

    def close(self):
        if self.armed:
            self.releases += 1


PREPARE = {"viewport": {"width": 800, "height": 600, "dpr": 1}, "point": {"x": 100, "y": 100}}


@pytest.fixture
def setup_controller():
    desktop, watchdog = Desktop(), Watchdog()
    now = [100.0]
    controller = NativeController(desktop, lambda: watchdog, lambda: now[0], start_polling=False)
    yield controller, desktop, watchdog, now
    controller.close()


def press(controller):
    prepared = controller.handle("prepare", PREPARE)
    return controller.handle("press", {"token": prepared["token"]})


def test_one_press_owned_release_and_no_reuse(setup_controller):
    controller, desktop, watchdog, now = setup_controller
    state = press(controller)
    assert state["pressConfirmed"] and watchdog.armed
    now[0] += 0.6
    state = controller.handle("release", {})
    assert state["releaseConfirmed"] and state["holdMs"] == 600
    controller.handle("release", {})
    assert desktop.up == 1 and watchdog.disarms == 1
    with pytest.raises(DesktopError, match="already-used"):
        controller.handle("prepare", PREPARE)


def test_preparation_expires_without_press(setup_controller):
    controller, desktop, _, now = setup_controller
    prepared = controller.handle("prepare", PREPARE)
    now[0] += 2.01
    with pytest.raises(DesktopError, match="target-expired"):
        controller.handle("press", {"token": prepared["token"]})
    assert desktop.down == 0 and not controller.state()["pressSent"]


@pytest.mark.parametrize(
    "reason", ["user-input", "focus-changed", "desktop-locked", "window-changed"]
)
def test_poll_aborts_and_releases_owned_input(setup_controller, reason):
    controller, desktop, watchdog, _ = setup_controller
    press(controller)
    desktop.reason = reason
    controller.tick()
    assert controller.state()["reason"] == reason
    assert controller.state()["releaseConfirmed"] and not watchdog.armed


def test_deadline_releases_at_thirty_seconds(setup_controller):
    controller, desktop, _, now = setup_controller
    press(controller)
    now[0] += 30
    controller.tick()
    assert controller.state()["reason"] == "max-duration"
    assert desktop.up == 1


def test_failed_press_is_released_and_not_replayed(setup_controller):
    controller, desktop, watchdog, _ = setup_controller
    desktop.fail_press = True
    with pytest.raises(DesktopError, match="input-failed"):
        press(controller)
    state = controller.state()
    assert state["pressSent"] and not state["pressConfirmed"] and state["releaseConfirmed"]
    assert desktop.down == 1 and desktop.up == 1 and not watchdog.armed


def test_watchdog_failure_prevents_press(setup_controller):
    controller, desktop, watchdog, _ = setup_controller
    watchdog.fail_arm = True
    with pytest.raises(DesktopError, match="watchdog-unavailable"):
        press(controller)
    assert desktop.down == 0 and not controller.state()["pressSent"]
    with pytest.raises(DesktopError, match="already-used"):
        controller.handle("prepare", PREPARE)


def test_watchdog_startup_cannot_extend_preparation_lease(setup_controller):
    controller, desktop, watchdog, now = setup_controller

    def slow_arm():
        watchdog.armed = True
        now[0] += 2.01

    watchdog.arm = slow_arm
    with pytest.raises(DesktopError, match="target-expired"):
        press(controller)
    assert desktop.down == 0 and not controller.state()["pressSent"]


@pytest.mark.parametrize("reason", ["target-expired", "user-input"])
def test_prepress_abort_disarms_without_releasing_unrelated_user_input(setup_controller, reason):
    controller, desktop, watchdog, now = setup_controller

    def arm_then_change():
        watchdog.armed = True
        if reason == "target-expired":
            now[0] += 2.01
        else:
            desktop.reason = "user-input"

    watchdog.arm = arm_then_change
    with pytest.raises(DesktopError, match=reason):
        press(controller)
    controller.close()
    assert desktop.down == desktop.up == watchdog.releases == 0
    assert watchdog.disarms == 1 and not watchdog.armed
    assert not controller.state()["pressSent"]


def test_uncertain_arm_ack_with_confirmed_no_press_is_disarmed(setup_controller):
    controller, desktop, watchdog, _ = setup_controller

    def arm_lost_ack():
        watchdog.armed = True
        raise DesktopError("watchdog-unavailable")

    watchdog.arm = arm_lost_ack
    with pytest.raises(DesktopError, match="watchdog-unavailable"):
        press(controller)
    controller.close()
    assert desktop.down == desktop.up == watchdog.releases == 0
    assert watchdog.disarms == 1 and not watchdog.armed


def test_no_press_disarm_failure_is_not_reported_as_confirmed_cleanup(setup_controller):
    controller, desktop, watchdog, _ = setup_controller

    def arm_then_user_moves():
        watchdog.armed = True
        desktop.reason = "user-input"

    watchdog.arm = arm_then_user_moves
    watchdog.fail_disarm = True
    with pytest.raises(DesktopError, match="watchdog-unavailable"):
        press(controller)
    assert not controller.state()["pressSent"]
    assert not controller.state()["releaseConfirmed"]
    assert watchdog.armed
    watchdog.fail_disarm = False
    controller.close()
    assert watchdog.disarms == 1 and watchdog.releases == 0


def test_failed_release_keeps_watchdog_armed_until_confirmed(setup_controller):
    controller, desktop, watchdog, _ = setup_controller
    press(controller)
    desktop.fail_release = True
    with pytest.raises(DesktopError, match="release-failed"):
        controller.handle("release", {})
    assert watchdog.armed and watchdog.disarms == 0
    desktop.fail_release = False
    controller.close()
    assert controller.state()["releaseConfirmed"] and watchdog.disarms == 1


def test_real_polling_thread_releases_without_protocol_traffic():
    desktop, watchdog = Desktop(), Watchdog()
    controller = NativeController(desktop, lambda: watchdog)
    try:
        press(controller)
        desktop.reason = "user-input"
        deadline = time.monotonic() + 0.5
        while not controller.state()["releaseConfirmed"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert controller.state()["releaseConfirmed"]
    finally:
        controller.close()


def frame(value):
    payload = json.dumps(value).encode()
    return struct.pack("<I", len(payload)) + payload


class PartialReader(io.BytesIO):
    def read(self, count=-1):
        return super().read(min(count, 2))


def test_framing_handles_partial_reads_and_clean_eof():
    value = {"v": 1, "id": "one", "op": "probe", "payload": {}}
    stream = PartialReader(frame(value))
    assert read_frame(stream) == value
    assert read_frame(stream) is None


@pytest.mark.parametrize(
    "wire", [b"\x01", struct.pack("<I", 16385), struct.pack("<I", 0), struct.pack("<I", 4) + b"{}"]
)
def test_truncated_or_oversized_frames_fail_closed(wire):
    with pytest.raises(DesktopError, match="invalid-request"):
        read_frame(io.BytesIO(wire))


@pytest.mark.parametrize(
    "change",
    [
        {"extra": True},
        {"v": True},
        {"id": "../bad"},
        {"op": "click"},
        {"payload": {"extra": 1}},
        {"payload": []},
    ],
)
def test_strict_requests_reject_unknown_or_wrong_values(change):
    value = {"v": 1, "id": "one", "op": "probe", "payload": {}}
    value.update(change)
    with pytest.raises(DesktopError, match="invalid-request"):
        validate_request(value)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, 10**400])
def test_prepare_rejects_nonfinite_or_invalid_geometry(bad):
    payload = {"viewport": {"width": bad, "height": 600, "dpr": 1}, "point": {"x": 1, "y": 1}}
    with pytest.raises(DesktopError, match="invalid-request"):
        validate_request({"v": 1, "id": "one", "op": "prepare", "payload": payload})


def test_launch_requires_manifest_exact_origin_and_real_parent(tmp_path):
    path = tmp_path / "host.json"
    origin = "chrome-extension://oidgbnehgnikmniolojjkmhlpifdoiil/"
    path.write_text(
        json.dumps(
            {
                "name": "com.reviewer_item_relay.verification",
                "type": "stdio",
                "allowed_origins": [origin],
            }
        )
    )
    assert validate_launch([origin, "--parent-window=123", "--host-manifest", str(path)]) == 123
    for args in [
        [origin, "--parent-window=0"],
        [origin.replace("oidgb", "aaaaa"), "--parent-window=123"],
        [origin, "--parent-window=123", "extra"],
    ]:
        with pytest.raises(DesktopError):
            validate_launch(args + ["--host-manifest", str(path)])


def test_eof_after_press_releases_and_returns_only_safe_state(setup_controller):
    controller, desktop, _, _ = setup_controller
    press(controller)
    run_session(io.BytesIO(), io.BytesIO(), controller)
    assert desktop.up == 1 and controller.state()["releaseConfirmed"]


def test_protocol_error_releases_active_input(setup_controller):
    controller, desktop, _, _ = setup_controller
    press(controller)
    output = io.BytesIO()
    run_session(
        io.BytesIO(frame({"v": 1, "id": "one", "op": "shell", "payload": {}})), output, controller
    )
    assert desktop.up == 1
    response = read_frame(io.BytesIO(output.getvalue()))
    assert response["error"] == "invalid-request" and "state" in response


@pytest.mark.parametrize("payload", [b'{"v":1,"v":1}', b'{"x":NaN}', b'"\\ud800"'])
def test_duplicate_keys_and_nonfinite_or_unpaired_unicode_fail(payload):
    with pytest.raises(DesktopError, match="invalid-request"):
        read_frame(io.BytesIO(struct.pack("<I", len(payload)) + payload))


def test_concurrent_release_is_idempotent(setup_controller):
    controller, desktop, _, _ = setup_controller
    press(controller)
    threads = [threading.Thread(target=lambda: controller.handle("release", {})) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert desktop.up == 1


def test_heartbeat_failure_releases_and_retains_safe_reason(setup_controller):
    controller, desktop, watchdog, now = setup_controller
    press(controller)

    def fail():
        raise OSError("must never be logged")

    watchdog.pulse = fail
    now[0] += 0.25
    controller.tick()
    state = controller.state()
    assert state["reason"] == "watchdog-unavailable"
    assert state["releaseConfirmed"] and desktop.up == 1


def test_broken_output_pipe_still_releases_owned_press(setup_controller):
    controller, desktop, _, _ = setup_controller
    press(controller)

    class BrokenWriter:
        def write(self, _):
            raise BrokenPipeError()

    with pytest.raises(BrokenPipeError):
        run_session(
            io.BytesIO(frame({"v": 1, "id": "one", "op": "status", "payload": {}})),
            BrokenWriter(),
            controller,
        )
    assert controller.state()["releaseConfirmed"] and desktop.up == 1
