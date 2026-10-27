"""Separate release-only process; parent failure cannot leave its press armed.

The child accepts a tiny fixed pipe protocol. It never moves the cursor or sends
mouseDown. Its independent monotonic deadline is not extended by heartbeats.
"""

import os
import queue
import subprocess
import sys
import threading
import time
from contextlib import suppress

from .contracts import MAX_HOLD_MS, DesktopError

_HANDSHAKE_SECONDS = 2.0
_ARM_SECONDS = 0.5
_DISARM_SECONDS = 0.5
_HEARTBEAT_SECONDS = 1.5


def _watch_loop(read, send, release, parent_alive, clock=time.monotonic, *, arm_guard=None):
    armed_at = None
    last_pulse = None
    used = False
    send("ready")
    while True:
        message = read(0.05)
        now = clock()
        # This command proves the parent already confirmed release or confirmed
        # it never sent down. Honor queued cancellation before an expiry check;
        # scheduler delay must not turn a settled attempt into another mouseUp.
        if message == "disarm" and used:
            armed_at = None
            send("disarmed")
            continue
        if armed_at is not None and (not parent_alive()
                or now - armed_at >= MAX_HOLD_MS / 1000
                or now - last_pulse >= _HEARTBEAT_SECONDS):
            break
        if message is None:
            if not parent_alive():
                return True
            continue
        if message == "arm" and not used:
            if not parent_alive():
                return True
            if arm_guard is not None and not arm_guard():
                send("failed")
                return False
            # The mutex acquisition can outlive the parent. Until the armed
            # acknowledgement, no down was allowed: exit without mouseUp.
            if not parent_alive():
                return True
            used = True
            armed_at = last_pulse = clock()
            send("armed")
        elif message == "pulse" and armed_at is not None:
            last_pulse = now
        elif message in ("close", "eof"):
            if armed_at is None:
                return True
            break
        else:
            if armed_at is None:
                return False
            break
    for _ in range(5):
        try:
            if release():
                send("released")
                return True
        except (OSError, DesktopError):
            pass
        time.sleep(0.02)
    send("failed")
    return False


def _line_reader(stream, destination):
    try:
        while True:
            line = stream.readline(64)
            if not line:
                break
            # Fixed short ASCII messages only, never JSON or arbitrary payloads.
            if not line.endswith(b"\n") or len(line) > 32:
                destination.put("invalid")
                break
            destination.put(line.rstrip(b"\r\n").decode("ascii", errors="replace"))
    except (OSError, ValueError):
        pass
    finally:
        destination.put("eof")


def _take(messages, timeout):
    try:
        return messages.get(timeout=timeout)
    except queue.Empty:
        return None


def _child_main(parent_pid):
    from .win32 import _InputLease, _Win32API

    api = _Win32API()
    parent = api.kernel.OpenProcess(0x00100000, False, parent_pid)
    if not parent:
        return 1
    messages = queue.Queue()
    gate = _InputLease(api.kernel, "Local\\ReviewerItemRelayNativeRelease-v1")
    # A daemon must not hold CPython's buffered stdin lock during interpreter
    # shutdown after a heartbeat/parent-death exit while its pipe remains open.
    incoming = os.fdopen(sys.stdin.fileno(), "rb", buffering=0, closefd=False)
    threading.Thread(target=_line_reader, args=(incoming, messages), daemon=True).start()

    def send(value):
        try:
            sys.stdout.buffer.write(value.encode("ascii") + b"\n")
            sys.stdout.buffer.flush()
        except (BrokenPipeError, OSError):
            # A broken response pipe is a parent disconnect, too.
            messages.put("eof")

    try:
        result = _watch_loop(lambda timeout: _take(messages, timeout), send, api.up,
                             lambda: api.kernel.WaitForSingleObject(parent, 0) == 0x102,
                             arm_guard=gate.acquire)
        return 0 if result else 1
    finally:
        gate.close()
        api.kernel.CloseHandle(parent)


class ReleaseWatchdog:
    def __init__(self):
        self.process = None
        self.messages = queue.Queue()
        self.armed = False
        self.used = False
        self.closed = False
        self.lock = threading.RLock()

    def _send(self, command):
        try:
            if not self.process or self.process.poll() is not None:
                raise DesktopError("watchdog-unavailable")
            self.process.stdin.write(command.encode("ascii") + b"\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as error:
            raise DesktopError("watchdog-unavailable") from error

    def _expect(self, expected, *, timeout=None, drain_late_arm=False):
        deadline = time.monotonic() + (_HANDSHAKE_SECONDS if timeout is None else timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DesktopError("watchdog-unavailable")
            message = _take(self.messages, remaining)
            if message == expected:
                return
            if message == "armed" and drain_late_arm:
                drain_late_arm = False
                continue
            raise DesktopError("watchdog-unavailable")

    def start(self):
        with self.lock:
            if sys.platform != "win32":
                raise DesktopError("unsupported-platform")
            if self.closed:
                raise DesktopError("closed")
            if self.process:
                return
            try:
                self.process = subprocess.Popen(
                    [sys.executable, "-m", "native_verification.watchdog", "--child",
                     str(os.getpid())],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                threading.Thread(target=_line_reader, args=(self.process.stdout, self.messages),
                                 daemon=True).start()
                self._expect("ready")
            except (OSError, DesktopError) as error:
                self.close()
                raise DesktopError("watchdog-unavailable") from error

    def arm(self):
        with self.lock:
            if self.used:
                raise DesktopError("already-used")
            self._send("arm")
            # Treat a lost acknowledgement as armed: close must request release.
            self.armed = self.used = True
            self._expect("armed", timeout=_ARM_SECONDS)

    def pulse(self):
        with self.lock:
            if not self.armed:
                raise DesktopError("watchdog-unavailable")
            self._send("pulse")

    def disarm(self):
        with self.lock:
            if not self.armed:
                return
            self._send("disarm")
            self._expect("disarmed", timeout=_DISARM_SECONDS, drain_late_arm=True)
            self.armed = False

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            process = self.process
            if not process:
                return
            try:
                if process.poll() is None:
                    with suppress(DesktopError):
                        self._send("close")
                    process.stdin.close()  # EOF also requests release when armed.
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        # Never kill an armed release process. Its own watchdog
                        # deadline remains authoritative if parent cleanup stalls.
                        if not self.armed:
                            process.terminate()
                            process.wait(timeout=1)
            finally:
                if process.poll() is not None:
                    process.stdout.close()


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--child" or not sys.argv[2].isdigit():
        raise SystemExit(2)
    try:
        raise SystemExit(_child_main(int(sys.argv[2])))
    except (DesktopError, OSError):
        raise SystemExit(1) from None
