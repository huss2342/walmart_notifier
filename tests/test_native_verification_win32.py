"""Native input safety behavior uses a fake OS; these tests never inject input."""

import subprocess
import sys
import threading

import pytest
from native_verification.contracts import DesktopError
from native_verification.win32 import WindowsDesktop


class FakeOS:
    def __init__(self):
        self.valid = True
        self.foreground = 10
        self.unlocked = True
        self.stamp = 100
        self.idle = 6000
        self.held = set()
        self.widgets = [(11, (100, 200, 1100, 900))]
        self.position = (5, 5)
        self.hit = 10
        self.lease = False
        self.busy = False
        self.events = []
        self.down_ok = True
        self.up_ok = True
        self.last_input_stamp = None
        self.input_after_down = False

    def valid_chrome_window(self, window):
        return self.valid and window == 10

    def foreground_window(self):
        return self.foreground

    def desktop_unlocked(self):
        return self.unlocked

    def input_info(self):
        return self.stamp, self.idle

    def held_buttons(self):
        return self.held.copy()

    def renderer_windows(self, window):
        return self.widgets.copy()

    def cursor_position(self):
        return self.position

    def point_root(self, x, y):
        return self.hit

    def acquire_lease(self):
        if self.busy:
            return False
        self.lease = True
        return True

    def release_lease(self):
        self.lease = False

    def move(self, x, y):
        self.events.append(("move", x, y))
        self.position = (x, y)
        self.stamp += 1
        self.last_input_stamp = self.stamp
        return True

    def down(self):
        self.events.append(("down",))
        self.stamp += 1
        self.last_input_stamp = self.stamp
        if self.down_ok:
            self.held.add(1)
        if self.input_after_down:
            self.stamp += 1
        return self.down_ok

    def up(self):
        self.events.append(("up",))
        if self.up_ok:
            self.held.discard(1)
        return self.up_ok


VIEWPORT = {"width": 1000, "height": 700, "dpr": 1}
POINT = {"x": 400, "y": 300}


def make_desktop():
    os_api = FakeOS()
    return WindowsDesktop(10, _api=os_api), os_api


def assert_error(code, action):
    with pytest.raises(DesktopError) as caught:
        action()
    assert caught.value.code == code


def test_native_point_maps_only_to_unique_chrome_renderer_and_owned_lease():
    desktop, os_api = make_desktop()
    target = desktop.prepare(VIEWPORT, POINT)
    assert (target.x, target.y) == (500, 500)
    assert target.widget == 11 and os_api.lease
    pressed = desktop.press(target)
    assert desktop.abort_reason(pressed) is None
    desktop.release()
    desktop.close()
    assert os_api.events == [("move", 500, 500), ("down",), ("up",)]
    assert not os_api.lease and os_api.held == set()


@pytest.mark.parametrize("field,value,reason", [
    ("valid", False, "invalid-window"),
    ("unlocked", False, "desktop-locked"),
    ("foreground", 20, "focus-changed"),
    ("idle", 4999, "user-busy"),
    ("held", {2}, "button-held"),
])
def test_busy_or_unsafe_desktop_never_receives_input(field, value, reason):
    desktop, os_api = make_desktop()
    setattr(os_api, field, value)
    assert desktop.probe()["reason"] == reason
    assert_error(reason, lambda: desktop.prepare(VIEWPORT, POINT))
    assert not os_api.events and not os_api.lease


def test_no_cross_process_lease_means_no_movement():
    desktop, os_api = make_desktop()
    os_api.busy = True
    assert_error("helper-busy", lambda: desktop.prepare(VIEWPORT, POINT))
    assert os_api.events == []


@pytest.mark.parametrize("widgets", [[], [(11, (0, 0, 500, 300))],
    [(11, (100, 200, 1100, 900)), (12, (100, 200, 1100, 900))]])
def test_missing_mismatched_or_ambiguous_renderer_fails_closed(widgets):
    desktop, os_api = make_desktop()
    os_api.widgets = widgets
    assert_error("viewport-mismatch", lambda: desktop.prepare(VIEWPORT, POINT))
    assert os_api.events == [] and not os_api.lease


def test_css_zoom_maps_to_physical_pixels_including_negative_monitor_origin():
    desktop, os_api = make_desktop()
    os_api.widgets = [(11, (-1500, 100, 0, 1150))]
    target = desktop.prepare({**VIEWPORT, "dpr": 1.5}, POINT)
    assert (target.x, target.y) == (-900, 550)
    desktop.close()


def test_obscured_point_never_moves_or_presses():
    desktop, os_api = make_desktop()
    os_api.hit = 20
    assert_error("target-obscured", lambda: desktop.prepare(VIEWPORT, POINT))
    assert not os_api.events


def test_user_input_between_prepare_and_press_cancels_without_down():
    desktop, os_api = make_desktop()
    target = desktop.prepare(VIEWPORT, POINT)
    os_api.stamp += 1
    assert_error("user-input", lambda: desktop.press(target))
    desktop.close()
    assert not any(event[0] == "down" for event in os_api.events)


@pytest.mark.parametrize("change,reason", [
    (lambda api: setattr(api, "foreground", 20), "focus-changed"),
    (lambda api: setattr(api, "unlocked", False), "desktop-locked"),
    (lambda api: setattr(api, "widgets", [(11, (101, 200, 1101, 900))]), "window-changed"),
    (lambda api: setattr(api, "position", (501, 500)), "user-input"),
    (lambda api: setattr(api, "hit", 20), "target-obscured"),
])
def test_hold_is_aborted_when_desktop_target_changes(change, reason):
    desktop, os_api = make_desktop()
    target = desktop.press(desktop.prepare(VIEWPORT, POINT))
    change(os_api)
    assert desktop.abort_reason(target) == reason
    desktop.close()
    assert os_api.events[-1] == ("up",)


def test_failed_down_is_suspected_and_always_released():
    desktop, os_api = make_desktop()
    target = desktop.prepare(VIEWPORT, POINT)
    os_api.down_ok = False
    assert_error("input-failed", lambda: desktop.press(target))
    desktop.release()
    assert os_api.events[-1] == ("up",)


def test_failed_release_retains_lease_until_confirmed_and_retries_on_close():
    desktop, os_api = make_desktop()
    desktop.press(desktop.prepare(VIEWPORT, POINT))
    os_api.up_ok = False
    assert_error("release-failed", desktop.release)
    assert os_api.lease
    os_api.up_ok = True
    desktop.close()
    assert not os_api.lease and os_api.events[-2:] == [("up",), ("up",)]


def test_one_press_per_desktop_and_no_unowned_release():
    desktop, os_api = make_desktop()
    desktop.release()
    assert not os_api.events
    target = desktop.prepare(VIEWPORT, POINT)
    desktop.press(target)
    desktop.release()
    assert_error("already-used", lambda: desktop.press(target))
    assert sum(event[0] == "down" for event in os_api.events) == 1
    desktop.close()


def test_idle_is_reported_even_before_the_extension_focuses_chrome():
    desktop, os_api = make_desktop()
    os_api.foreground = 20
    assert desktop.probe() == {"ready": False, "reason": "focus-changed", "idleMs": 6000}


def test_prepared_target_expires_before_a_late_press():
    os_api = FakeOS()
    clock = [0]
    desktop = WindowsDesktop(10, _api=os_api, _clock=lambda: clock[0])
    target = desktop.prepare(VIEWPORT, POINT)
    clock[0] = 2
    assert_error("target-expired", lambda: desktop.press(target))
    desktop.close()
    assert not any(event[0] == "down" for event in os_api.events)


def test_input_mutex_stays_owned_until_close_after_confirmed_release():
    desktop, os_api = make_desktop()
    desktop.press(desktop.prepare(VIEWPORT, POINT))
    desktop.release()
    assert os_api.lease
    desktop.close()
    assert not os_api.lease


def test_user_event_just_after_down_ack_is_not_mistaken_for_owned_input():
    desktop, os_api = make_desktop()
    target = desktop.prepare(VIEWPORT, POINT)
    os_api.input_after_down = True
    target = desktop.press(target)
    assert desktop.abort_reason(target) == "user-input"
    desktop.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Harmless real Windows mutex probe only")
def test_real_mutex_serializes_threads_and_survives_cleanup_thread_change():
    from native_verification.win32 import _InputLease, _Win32API

    first, second = _Win32API(), _Win32API()
    gate = _InputLease(first.kernel, "Local\\ReviewerItemRelayNativeRelease-v1")
    try:
        assert first.acquire_lease()
        assert not second.acquire_lease()
        cleanup = threading.Thread(target=first.release_lease)
        cleanup.start()
        cleanup.join(timeout=2)
        assert not cleanup.is_alive()
        assert gate.acquire()
        assert not second.acquire_lease()
        gate.close()
        assert second.acquire_lease()
    finally:
        first.release_lease()
        second.release_lease()
        gate.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Harmless cross-process Windows mutex only")
def test_real_named_input_mutex_blocks_a_second_process():
    from native_verification.win32 import _Win32API

    api = _Win32API()
    script = ("from native_verification.win32 import _Win32API; "
              "api=_Win32API(); print(int(api.acquire_lease())); api.release_lease()")
    try:
        assert api.acquire_lease()
        result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                check=True, timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.stdout.strip() == b"0"
    finally:
        api.release_lease()


@pytest.mark.parametrize("viewport,point", [
    ({**VIEWPORT, "dpr": float("nan")}, POINT),
    ({**VIEWPORT, "width": True}, POINT),
    (VIEWPORT, {"x": -1, "y": 30}),
    (VIEWPORT, {"x": 1000, "y": 30}),
    ({**VIEWPORT, "dpr": 10}, POINT),
])
def test_invalid_geometry_never_acquires_input_or_moves(viewport, point):
    desktop, os_api = make_desktop()
    assert_error("invalid-request", lambda: desktop.prepare(viewport, point))
    assert not os_api.events and not os_api.lease


@pytest.mark.skipif(sys.platform == "win32", reason="Off-Windows boundary only")
def test_import_safe_but_construction_fails_closed_off_windows():
    assert_error("unsupported-platform", lambda: WindowsDesktop(10))
