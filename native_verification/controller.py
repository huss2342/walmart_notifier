"""Bounded, single-press input lease. All desktop operations are serialized."""

import secrets
import threading
import time
from contextlib import suppress

from .contracts import ERROR_CODES, MAX_HOLD_MS, PREPARE_TTL_MS, DesktopError


class NativeController:
    def __init__(self, desktop, watchdog_factory=None, clock=time.monotonic, *, start_polling=True):
        if watchdog_factory is None:
            from .watchdog import ReleaseWatchdog

            watchdog_factory = ReleaseWatchdog
        self.desktop = desktop
        self.watchdog_factory = watchdog_factory
        self.clock = clock
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.watchdog = None
        self.watchdog_disarmed = False
        self.target = None
        self.token = None
        self.prepared_at = None
        self.started_at = None
        self.released_at = None
        self.last_pulse = None
        self.used = False
        self.owned = False
        self.closed = False
        self.data = {
            "ready": False,
            "reason": None,
            "idleMs": 0,
            "phase": "idle",
            "pressSent": False,
            "pressConfirmed": False,
            "releaseConfirmed": False,
            "holdMs": 0,
        }
        self.thread = None
        if start_polling:
            self.thread = threading.Thread(
                target=self._poll, name="native-input-lease", daemon=True
            )
            self.thread.start()

    def state(self):
        with self.lock:
            state = dict(self.data)
            if self.started_at is not None:
                end = self.released_at if self.released_at is not None else self.clock()
                state["holdMs"] = max(0, round((end - self.started_at) * 1000))
            return state

    def _safe_error(self, error, fallback="host-unavailable"):
        return (
            error.code
            if isinstance(error, DesktopError) and error.code in ERROR_CODES
            else fallback
        )

    def _release(self):
        if self.owned:
            try:
                self.desktop.release()
            except Exception as error:
                self.data.update(ready=False, reason="release-failed", phase="aborted")
                raise DesktopError("release-failed") from error
            self.released_at = self.clock()
            self.owned = False
            self.data["releaseConfirmed"] = True
        # No-down evidence also permits cancellation: sending mouseUp here would
        # affect a user drag that caused the prepress guard to abort. Once down
        # was attempted, disarm requires a confirmed desktop release instead.
        can_disarm = not self.data["pressSent"] or self.data["releaseConfirmed"]
        if self.watchdog is not None and not self.watchdog_disarmed and can_disarm:
            try:
                self.watchdog.disarm()
                self.watchdog_disarmed = True
            except Exception as error:
                self.data.update(ready=False, reason="watchdog-unavailable", phase="aborted")
                raise DesktopError("watchdog-unavailable") from error

    def _abort(self, reason):
        self.data.update(ready=False, reason=reason, phase="aborted")
        self.token = None
        # Keep polling/EOF cleanup and the independent backstop armed on failure.
        with suppress(DesktopError):
            self._release()

    def handle(self, op, payload):
        with self.lock:
            if self.closed:
                if op in {"close", "release", "status"}:
                    return self.state()
                raise DesktopError("closed")
            try:
                if op == "probe":
                    probe = self.desktop.probe()
                    self.data.update(
                        ready=bool(probe["ready"]), reason=probe["reason"], idleMs=probe["idleMs"]
                    )
                elif op == "prepare":
                    if self.used:
                        raise DesktopError("already-used")
                    self.target = self.desktop.prepare(payload["viewport"], payload["point"])
                    self.token = secrets.token_hex(24)
                    self.prepared_at = self.clock()
                    self.data.update(phase="prepared", ready=True, reason=None)
                    return {**self.state(), "token": self.token}
                elif op == "press":
                    if self.used:
                        raise DesktopError("already-used")
                    if (
                        self.target is None
                        or self.token is None
                        or not secrets.compare_digest(payload["token"], self.token)
                    ):
                        raise DesktopError("not-prepared")
                    if (self.clock() - self.prepared_at) * 1000 >= PREPARE_TTL_MS:
                        raise DesktopError("target-expired")
                    reason = self.desktop.abort_reason(self.target)
                    if reason:
                        raise DesktopError(reason)
                    self.used = True
                    try:
                        self.watchdog = self.watchdog_factory()
                        self.watchdog.start()
                        self.watchdog.arm()
                    except Exception as error:
                        raise DesktopError("watchdog-unavailable") from error
                    # Process startup/arm acknowledgment consumes the same lease.
                    if (self.clock() - self.prepared_at) * 1000 >= PREPARE_TTL_MS:
                        raise DesktopError("target-expired")
                    reason = self.desktop.abort_reason(self.target)
                    if reason:
                        raise DesktopError(reason)
                    self.started_at = self.clock()
                    self.last_pulse = self.started_at
                    self.owned = True  # A failed SendInput can leave a suspected press.
                    self.data["pressSent"] = True
                    self.token = None
                    self.target = self.desktop.press(self.target)
                    self.data.update(pressConfirmed=True, phase="holding", ready=False, reason=None)
                elif op == "status":
                    self.tick()
                elif op == "release":
                    self._release()
                    if self.data["phase"] != "aborted":
                        self.data.update(phase="released", ready=False)
                    self.token = None
                elif op == "close":
                    self.close()
                else:
                    raise DesktopError("invalid-request")
                return self.state()
            except Exception as error:
                reason = self._safe_error(error)
                self._abort(reason)
                raise DesktopError(self.data["reason"] or reason) from error

    def tick(self):
        with self.lock:
            if self.closed:
                return
            if (
                self.data["phase"] == "prepared"
                and (self.clock() - self.prepared_at) * 1000 >= PREPARE_TTL_MS
            ):
                self._abort("target-expired")
            if not self.owned:
                return
            if self.data["phase"] == "aborted":
                self._abort(self.data["reason"] or "input-failed")
                return
            try:
                if (self.clock() - self.started_at) * 1000 >= MAX_HOLD_MS:
                    self._abort("max-duration")
                    return
                reason = self.desktop.abort_reason(self.target)
                if reason:
                    self._abort(reason)
                    return
                if self.clock() - self.last_pulse >= 0.2:
                    self.watchdog.pulse()
                    self.last_pulse = self.clock()
            except Exception as error:
                self._abort(self._safe_error(error, "watchdog-unavailable"))

    def _poll(self):
        while not self.stop.wait(0.04):
            self.tick()

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.stop.set()
            with suppress(DesktopError):
                self._release()
            if self.watchdog is not None:
                try:
                    self.watchdog.close()  # An armed watchdog requests another release.
                except Exception:
                    self.data.update(ready=False, reason="watchdog-unavailable")
            try:
                self.desktop.close()
            except Exception:
                self.data.update(ready=False, reason="release-failed")
            self.closed = True
            self.data.update(phase="closed", ready=False)
            self.token = None
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=0.2)
