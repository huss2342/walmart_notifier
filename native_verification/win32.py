"""Restricted Windows input adapter. No titles, browser content, or generic commands.

Win32 calls are kept behind _Win32API so safety decisions can be tested without
injecting input. Geometry calls use a temporary per-monitor-v2 thread context;
CSS coordinates are accepted only when the renderer's physical client bounds
agree with the supplied viewport and DPR.
"""

import ctypes
import math
import ntpath
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import replace

from .contracts import MIN_IDLE_MS, PREPARE_TTL_MS, DesktopError, Target

DWORD = ctypes.c_uint32
LONG = ctypes.c_int32
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p
ULONG_PTR = ctypes.c_size_t


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", DWORD), ("dwTime", DWORD)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD),
                ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16),
                ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", ctypes.c_uint16),
                ("wParamH", ctypes.c_uint16)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", DWORD), ("value", _INPUTUNION)]


class _InputLease:
    """Mutex ownership stays on one thread even when controller cleanup moves."""

    def __init__(self, kernel, name="Local\\ReviewerItemRelayNativeInput-v1"):
        self.kernel = kernel
        self.name = name
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.acquired = False
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        handle = self.kernel.CreateMutexW(None, False, self.name)
        if not handle:
            self.ready.set()
            return
        try:
            result = self.kernel.WaitForSingleObject(handle, 0)
            # An abandoned lease may have an outstanding release watchdog.
            if result == 0x80:
                self.kernel.ReleaseMutex(handle)
                self.ready.set()
                return
            self.acquired = result == 0
            self.ready.set()
            if self.acquired:
                self.stop.wait()
                self.kernel.ReleaseMutex(handle)
        finally:
            self.kernel.CloseHandle(handle)

    def acquire(self):
        self.thread.start()
        if not self.ready.wait(1):
            self.stop.set()
            return False
        return self.acquired

    def close(self):
        self.stop.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=1)


class _Win32API:
    """Exact pointer-width signatures; constructing this object sends no input."""

    def __init__(self):
        if sys.platform != "win32":
            raise DesktopError("unsupported-platform")
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
        self.enum_proc = ctypes.WINFUNCTYPE(BOOL, HANDLE, ctypes.c_ssize_t)
        self.lease = None
        self.last_input_stamp = None
        self._declare()

    def _declare(self):
        def signature(lib, name, args, result):
            function = getattr(lib, name)
            function.argtypes = args
            function.restype = result

        for name in ("IsWindow", "IsWindowVisible", "IsWindowEnabled", "IsIconic"):
            signature(self.user, name, [HANDLE], BOOL)
        signature(self.user, "GetAncestor", [HANDLE, ctypes.c_uint], HANDLE)
        signature(self.user, "GetClassNameW", [HANDLE, ctypes.c_wchar_p, ctypes.c_int],
                  ctypes.c_int)
        signature(self.user, "GetWindowThreadProcessId", [HANDLE, ctypes.POINTER(DWORD)], DWORD)
        signature(self.user, "GetForegroundWindow", [], HANDLE)
        signature(self.user, "EnumChildWindows", [HANDLE, self.enum_proc, ctypes.c_ssize_t], BOOL)
        signature(self.user, "GetClientRect", [HANDLE, ctypes.POINTER(RECT)], BOOL)
        signature(self.user, "ClientToScreen", [HANDLE, ctypes.POINTER(POINT)], BOOL)
        signature(self.user, "GetCursorPos", [ctypes.POINTER(POINT)], BOOL)
        signature(self.user, "WindowFromPoint", [POINT], HANDLE)
        signature(self.user, "GetLastInputInfo", [ctypes.POINTER(LASTINPUTINFO)], BOOL)
        signature(self.user, "GetAsyncKeyState", [ctypes.c_int], ctypes.c_short)
        signature(self.user, "GetSystemMetrics", [ctypes.c_int], ctypes.c_int)
        signature(self.user, "SendInput", [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int],
                  ctypes.c_uint)
        signature(self.user, "SetThreadDpiAwarenessContext", [HANDLE], HANDLE)
        signature(self.user, "OpenInputDesktop", [DWORD, BOOL, DWORD], HANDLE)
        signature(self.user, "CloseDesktop", [HANDLE], BOOL)
        signature(self.user, "GetThreadDesktop", [DWORD], HANDLE)
        signature(self.user, "GetUserObjectInformationW",
                  [HANDLE, ctypes.c_int, HANDLE, DWORD, ctypes.POINTER(DWORD)], BOOL)
        signature(self.kernel, "OpenProcess", [DWORD, BOOL, DWORD], HANDLE)
        signature(self.kernel, "QueryFullProcessImageNameW",
                  [HANDLE, DWORD, ctypes.c_wchar_p, ctypes.POINTER(DWORD)], BOOL)
        signature(self.kernel, "CloseHandle", [HANDLE], BOOL)
        signature(self.kernel, "GetTickCount", [], DWORD)
        signature(self.kernel, "GetCurrentThreadId", [], DWORD)
        signature(self.kernel, "GetCurrentProcessId", [], DWORD)
        signature(self.kernel, "ProcessIdToSessionId", [DWORD, ctypes.POINTER(DWORD)], BOOL)
        signature(self.kernel, "CreateMutexW", [HANDLE, BOOL, ctypes.c_wchar_p], HANDLE)
        signature(self.kernel, "WaitForSingleObject", [HANDLE, DWORD], DWORD)
        signature(self.kernel, "ReleaseMutex", [HANDLE], BOOL)
        signature(self.wts, "WTSQuerySessionInformationW",
                  [HANDLE, DWORD, ctypes.c_int, ctypes.POINTER(HANDLE), ctypes.POINTER(DWORD)],
                  BOOL)
        signature(self.wts, "WTSFreeMemory", [HANDLE], None)

    @contextmanager
    def _physical(self):
        old = self.user.SetThreadDpiAwarenessContext(HANDLE(-4))
        if not old:
            raise DesktopError("viewport-mismatch")
        try:
            yield
        finally:
            self.user.SetThreadDpiAwarenessContext(old)

    def _class(self, window):
        buffer = ctypes.create_unicode_buffer(256)
        self.user.GetClassNameW(window, buffer, len(buffer))
        return buffer.value

    def _chrome_process(self, window):
        pid = DWORD()
        self.user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        handle = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return False
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = DWORD(len(buffer))
            return bool(self.kernel.QueryFullProcessImageNameW(
                            handle, 0, buffer, ctypes.byref(size))
                        and ntpath.basename(buffer.value).casefold() == "chrome.exe")
        finally:
            self.kernel.CloseHandle(handle)

    def valid_chrome_window(self, window):
        return bool(window and self.user.IsWindow(window) and self.user.IsWindowVisible(window)
                    and self.user.IsWindowEnabled(window) and not self.user.IsIconic(window)
                    and self.user.GetAncestor(window, 2) == window
                    and self._class(window) == "Chrome_WidgetWin_1"
                    and self._chrome_process(window))

    def foreground_window(self):
        return self.user.GetForegroundWindow() or 0

    def _desktop_name(self, desktop):
        buffer = ctypes.create_unicode_buffer(256)
        required = DWORD()
        if not self.user.GetUserObjectInformationW(desktop, 2, buffer, ctypes.sizeof(buffer),
                                                   ctypes.byref(required)):
            return None
        return buffer.value

    def desktop_unlocked(self):
        session = DWORD()
        if not self.kernel.ProcessIdToSessionId(self.kernel.GetCurrentProcessId(),
                                              ctypes.byref(session)):
            return False
        buffer, size = HANDLE(), DWORD()
        if not self.wts.WTSQuerySessionInformationW(None, session.value, 8,
                                                   ctypes.byref(buffer), ctypes.byref(size)):
            return False
        try:
            if (size.value < ctypes.sizeof(DWORD)
                    or ctypes.cast(buffer, ctypes.POINTER(DWORD))[0] != 0):
                return False  # WTSActive only; disconnected sessions must not inject.
        finally:
            self.wts.WTSFreeMemory(buffer)
        desktop = self.user.OpenInputDesktop(0, False, 0x101)
        if not desktop:
            return False
        try:
            thread_desktop = self.user.GetThreadDesktop(self.kernel.GetCurrentThreadId())
            receives_input, needed = BOOL(), DWORD()
            return bool(thread_desktop
                        and self._desktop_name(desktop) == "Default"
                        and self._desktop_name(thread_desktop) == "Default"
                        and self.user.GetUserObjectInformationW(thread_desktop, 6,
                            ctypes.byref(receives_input), ctypes.sizeof(receives_input),
                            ctypes.byref(needed))
                        and receives_input.value)
        finally:
            self.user.CloseDesktop(desktop)

    def input_info(self):
        info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
        if not self.user.GetLastInputInfo(ctypes.byref(info)):
            raise DesktopError("host-unavailable")
        return info.dwTime, (self.kernel.GetTickCount() - info.dwTime) & 0xFFFFFFFF

    def held_buttons(self):
        # Modifier keys can change a click's meaning, so guard those as well.
        return {key for key in (1, 2, 4, 5, 6, 16, 17, 18, 27, 91, 92)
                if self.user.GetAsyncKeyState(key) & 0x8000}

    def renderer_windows(self, window):
        result = []
        with self._physical():
            @self.enum_proc
            def visit(child, _):
                if (self.user.IsWindowVisible(child)
                        and self._class(child) == "Chrome_RenderWidgetHostHWND"
                        and self.user.GetAncestor(child, 2) == window
                        and self._chrome_process(child)):
                    rect = RECT()
                    if self.user.GetClientRect(child, ctypes.byref(rect)):
                        first, last = POINT(rect.left, rect.top), POINT(rect.right, rect.bottom)
                        if (self.user.ClientToScreen(child, ctypes.byref(first))
                                and self.user.ClientToScreen(child, ctypes.byref(last))
                                and last.x > first.x and last.y > first.y):
                            result.append((child, (first.x, first.y, last.x, last.y)))
                return True
            self.user.EnumChildWindows(window, visit, 0)
        return result

    def cursor_position(self):
        with self._physical():
            point = POINT()
            if not self.user.GetCursorPos(ctypes.byref(point)):
                raise DesktopError("host-unavailable")
            return point.x, point.y

    def point_root(self, x, y):
        with self._physical():
            window = self.user.WindowFromPoint(POINT(x, y))
            return self.user.GetAncestor(window, 2) if window else 0

    def acquire_lease(self):
        if self.lease:
            return True
        lease = _InputLease(self.kernel)
        if not lease.acquire():
            lease.close()
            return False
        gate = _InputLease(self.kernel, "Local\\ReviewerItemRelayNativeRelease-v1")
        if not gate.acquire():
            gate.close()
            lease.close()
            return False
        gate.close()
        self.lease = lease
        return True

    def release_lease(self):
        if self.lease:
            self.lease.close()
            self.lease = None

    def _send(self, flags, dx=0, dy=0):
        stamp = self.kernel.GetTickCount()
        event = INPUT(type=0, value=_INPUTUNION(mi=MOUSEINPUT(dx, dy, 0, flags, stamp, 0)))
        sent = self.user.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) == 1
        if sent:
            self.last_input_stamp = stamp
        return sent, stamp

    def _wait_input(self, predicate):
        deadline = time.monotonic() + 0.1
        while True:
            if predicate():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.002)

    def move(self, x, y):
        with self._physical():
            left, top, width, height = [self.user.GetSystemMetrics(index)
                                        for index in (76, 77, 78, 79)]
            if (width < 2 or height < 2
                    or not (left <= x < left + width and top <= y < top + height)):
                return False
            # Aim at each pixel's center in the 16-bit absolute input grid.
            dx = min(65535, int((x - left + 0.5) * 65536 / width))
            dy = min(65535, int((y - top + 0.5) * 65536 / height))
            sent, stamp = self._send(0xC001, dx, dy)
        return bool(sent and self._wait_input(lambda: self.input_info()[0] == stamp
                                               and self.cursor_position() == (x, y)))

    def down(self):
        sent, stamp = self._send(0x0002)
        return bool(sent and self._wait_input(lambda: self.input_info()[0] == stamp
                                               and bool(self.user.GetAsyncKeyState(1) & 0x8000)))

    def up(self):
        sent, _ = self._send(0x0004)
        return bool(sent and self._wait_input(lambda: not self.user.GetAsyncKeyState(1) & 0x8000))


class WindowsDesktop:
    """One Chrome window, one prepared point, and at most one press per connection."""

    def __init__(self, parent_window: int, *, _api=None, _clock=time.monotonic):
        if type(parent_window) is not int or parent_window <= 0:
            raise DesktopError("invalid-window")
        self.window = parent_window
        self.api = _api if _api is not None else _Win32API()
        self.clock = _clock
        self.target = None
        self.prepared_at = None
        self.used = False
        self.suspected = False
        self.closed = False
        self.leased = False
        self.lock = threading.RLock()

    def _guard(self, *, idle=False, holding=False):
        if self.closed:
            return "closed"
        if not self.api.valid_chrome_window(self.window):
            return "invalid-window"
        if not self.api.desktop_unlocked():
            return "desktop-locked"
        if self.api.foreground_window() != self.window:
            return "focus-changed"
        if idle and self.api.input_info()[1] < MIN_IDLE_MS:
            return "user-busy"
        buttons = self.api.held_buttons()
        if holding:
            if buttons != {1}:
                return "user-input"
        elif buttons:
            return "button-held"
        return None

    def probe(self):
        with self.lock:
            try:
                _, idle = self.api.input_info()
                reason = self._guard(idle=True)
                return {"ready": reason is None, "reason": reason, "idleMs": idle}
            except DesktopError as error:
                return {"ready": False, "reason": error.code, "idleMs": 0}

    def prepare(self, viewport: dict, point: dict) -> Target:
        with self.lock:
            if self.used:
                raise DesktopError("already-used")
            values = [viewport.get(key) for key in ("width", "height", "dpr")]
            values += [point.get(key) for key in ("x", "y")]
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
                raise DesktopError("invalid-request")
            width, height, dpr, x, y = values
            if not (0 < width <= 20000 and 0 < height <= 20000 and 0.5 <= dpr <= 4
                    and 0 <= x < width and 0 <= y < height):
                raise DesktopError("invalid-request")
            reason = self._guard(idle=True)
            if reason:
                raise DesktopError(reason)
            if not self.leased:
                if not self.api.acquire_lease():
                    raise DesktopError("helper-busy")
                self.leased = True
            try:
                reason = self._guard(idle=True)
                if reason:
                    raise DesktopError(reason)
                widgets = self.api.renderer_windows(self.window)
                if len(widgets) != 1:
                    raise DesktopError("viewport-mismatch")
                widget, bounds = widgets[0]
                left, top, right, bottom = bounds
                if abs(right - left - width * dpr) > 2 or abs(bottom - top - height * dpr) > 2:
                    raise DesktopError("viewport-mismatch")
                px = min(right - 1, left + round(x * dpr))
                py = min(bottom - 1, top + round(y * dpr))
                if self.api.point_root(px, py) != self.window:
                    raise DesktopError("target-obscured")
                stamp, _ = self.api.input_info()
                if self._guard() or self.api.input_info()[0] != stamp:
                    raise DesktopError("user-input")
                if not self.api.move(px, py):
                    raise DesktopError("input-failed")
                stamp = self.api.last_input_stamp
                self.target = Target(self.window, widget, px, py, bounds, stamp)
                self.prepared_at = self.clock()
                reason = self.abort_reason(self.target)
                if reason:
                    raise DesktopError(reason)
                return self.target
            except Exception:
                self.target = None
                self.api.release_lease()
                self.leased = False
                raise

    def press(self, target: Target) -> Target:
        with self.lock:
            if self.used:
                raise DesktopError("already-used")
            if target != self.target or self.prepared_at is None:
                raise DesktopError("not-prepared")
            if (self.clock() - self.prepared_at) * 1000 >= PREPARE_TTL_MS:
                raise DesktopError("target-expired")
            reason = self.abort_reason(target)
            if reason:
                raise DesktopError(reason)
            self.used = True
            self.suspected = True  # A failed acknowledgement is still an owned/suspected press.
            if not self.api.down():
                raise DesktopError("input-failed")
            self.target = replace(target, input_stamp=self.api.last_input_stamp)
            return self.target

    def abort_reason(self, target: Target):
        with self.lock:
            if target != self.target or not self.leased:
                return "not-prepared"
            reason = self._guard(holding=self.suspected)
            if reason:
                return reason
            if self.api.renderer_windows(self.window) != [(target.widget, target.bounds)]:
                return "window-changed"
            if self.api.cursor_position() != (target.x, target.y):
                return "user-input"
            if self.api.input_info()[0] != target.input_stamp:
                return "user-input"
            if self.api.point_root(target.x, target.y) != self.window:
                return "target-obscured"
            return None

    def release(self):
        with self.lock:
            if self.suspected:
                if not self.api.up():
                    raise DesktopError("release-failed")
                self.suspected = False
            self.target = None

    def close(self):
        with self.lock:
            self.release()
            if self.leased:
                self.api.release_lease()
                self.leased = False
            self.closed = True
