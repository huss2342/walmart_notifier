"""Small, bounded JSON receipts for service and HTTP operations.

Paths, headers, bodies, query strings and exception messages never enter this
sink. Logging failures are visible in health and cannot change request outcomes.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from pathlib import Path

from discord_bridge.rolling_logs import RETENTION_DAYS, RollingLog
from discord_bridge.telemetry_context import bind_request, current_request_id, valid_request_id

ROUTES = frozenset(
    {
        "/",
        "/health",
        "/ingest",
        "/rules",
        "/browser-diagnostics",
        "/relay-settings",
        "/scanner-settings",
        "/scanner-commands",
        "/scanner-status",
        "/relay-status",
        "/test-notification",
        "/bot-check",
        "/rescan",
    }
)
_EVENTS = frozenset(
    {
        "http_request",
        "http_request_started",
        "service_starting",
        "service_started",
        "service_stopped",
        "service_error",
        "configuration_changed",
        "intake_result",
        "pipeline_item",
    }
)
_NUMBERS = frozenset(
    {
        "status",
        "duration_ms",
        "request_bytes",
        "response_bytes",
        "items",
        "new",
        "duplicates",
        "queued",
        "filtered",
        "failed",
        "pending",
        "notified",
    }
)
_LABELS = {
    "method": frozenset({"GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "OTHER"}),
    "component": frozenset(
        {
            "backend",
            "rules",
            "relay-settings",
            "scanner-settings",
            "scanner-commands",
            "scanner-status",
        }
    ),
    "outcome": frozenset({"responded", "rejected", "error", "saved", "removed"}),
    "provider": frozenset({"discord", "telegram", "ntfy", "pushover", "none", "other"}),
    "decision": frozenset({"pending", "duplicate", "seeded", "filtered", "sent", "failed"}),
}


class OperationalLog:
    def __init__(
        self, directory, *, clock=None, max_bytes=2 * 1024 * 1024, total_bytes=16 * 1024 * 1024
    ):
        self.clock = clock or time.time
        self.run_id = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._written = self._dropped = 0
        self._last_error = self._last_event_at = None
        self._writer = None
        self._configured = directory is not None
        if directory is not None:
            try:
                self._writer = RollingLog(
                    Path(directory) / "operations.jsonl",
                    max_bytes=max_bytes,
                    total_bytes=total_bytes,
                )
            except OSError:
                self._last_error = "storage"

    def emit(self, event, *, entry_point="service", **fields):
        if self._writer is None:
            if self._configured:
                with self._lock:
                    self._dropped += 1
            return False
        try:
            if event not in _EVENTS or entry_point not in {"http", "service"}:
                raise ValueError("invalid operational event")
            now = self.clock()
            row = {
                "schema_version": 1,
                "event": event,
                "timestamp": now,
                "entry_point": entry_point,
                "run_id": self.run_id,
            }
            if current_request_id() is not None:
                row["request_id"] = current_request_id()
            for key, value in fields.items():
                if (
                    key in _NUMBERS
                    and type(value) in (int, float)
                    and math.isfinite(value)
                    and 0 <= value <= 1e15
                ):
                    row[key] = round(value, 3) if isinstance(value, float) else value
                elif (
                    key in _LABELS
                    and isinstance(value, str)
                    and value in _LABELS[key]
                    or key == "route"
                    and value in ROUTES | {"other"}
                    or key == "request_id"
                    and valid_request_id(value)
                    or key == "item_id"
                    and isinstance(value, str)
                    and re.fullmatch(r"ip-[0-9]{6,20}", value)
                    or key == "error_type"
                    and isinstance(value, str)
                    and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value)
                ):
                    row[key] = value
            row["severity"] = (
                "error"
                if row.get("error_type")
                or row.get("status", 0) >= 500
                or event == "service_error"
                or row.get("outcome") == "error"
                else "warning"
                if row.get("status", 0) >= 400
                else "info"
            )
            self._writer.append(
                (json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n").encode()
            )
            with self._lock:
                self._written += 1
                self._last_event_at = now
                self._last_error = None
            return True
        except Exception as exc:
            with self._lock:
                self._dropped += 1
                self._last_error = "storage" if isinstance(exc, OSError) else "invalid_event"
            return False

    def status(self):
        with self._lock:
            return {
                "enabled": self._writer is not None,
                "configured": self._configured,
                "file": "operations.jsonl",
                "run_id": self.run_id,
                "events_written": self._written,
                "dropped_events": self._dropped,
                "last_error": self._last_error,
                "last_event_at": self._last_event_at,
                "max_bytes": self._writer.max_bytes if self._writer else 0,
                "total_max_bytes": self._writer.total_bytes if self._writer else 0,
                "retention_days": RETENTION_DAYS,
            }


class RequestLoggingMixin:
    operations = None

    def handle_one_request(self):
        self._http_request_id = uuid.uuid4().hex
        self._http_context = None
        self._http_status = self._http_response_bytes = None
        self._http_request_bytes = None
        self._http_error = None
        started = time.monotonic()
        try:
            with bind_request(self._http_request_id):
                try:
                    return super().handle_one_request()
                finally:
                    if self._http_context is not None:
                        self._http_context.__exit__(None, None, None)
        except Exception as exc:
            self._http_error = type(exc).__name__
            raise
        finally:
            if self.operations is not None and (
                getattr(self, "requestline", "") or self._http_status is not None
            ):
                path = getattr(self, "path", "").split("?")[0].rstrip("/") or "/"
                method = getattr(self, "command", "OTHER")
                self.operations.emit(
                    "http_request",
                    entry_point="http",
                    request_id=self._http_request_id,
                    route=path if path in ROUTES else "other",
                    method=method if method in _LABELS["method"] else "OTHER",
                    status=self._http_status or 0,
                    duration_ms=(time.monotonic() - started) * 1000,
                    response_bytes=self._http_response_bytes,
                    error_type=self._http_error,
                    request_bytes=self._http_request_bytes,
                    outcome="error"
                    if self._http_error or not self._http_status or self._http_status >= 500
                    else "rejected"
                    if self._http_status >= 400
                    else "responded",
                )
                if (
                    self._http_status is not None
                    and 200 <= self._http_status < 300
                    and method in {"PUT", "DELETE"}
                    and path
                    in {"/rules", "/relay-settings", "/scanner-settings", "/scanner-commands"}
                ):
                    self.operations.emit(
                        "configuration_changed",
                        entry_point="http",
                        request_id=self._http_request_id,
                        component=path[1:],
                        outcome="removed" if method == "DELETE" else "saved",
                    )

    def parse_request(self):
        parsed = super().parse_request()
        if parsed:
            values = self.headers.get_all("X-Request-ID", [])
            if len(values) == 1 and valid_request_id(values[0]):
                self._http_request_id = values[0]
            self._http_context = bind_request(self._http_request_id)
            self._http_context.__enter__()
            if self.operations is not None:
                path = self.path.split("?")[0].rstrip("/") or "/"
                self.operations.emit(
                    "http_request_started",
                    entry_point="http",
                    request_id=self._http_request_id,
                    method=self.command if self.command in _LABELS["method"] else "OTHER",
                    route=path if path in ROUTES else "other",
                )
        return parsed

    def send_response(self, code, message=None):
        self._http_status = code
        super().send_response(code, message)
        self.send_header("X-Request-ID", getattr(self, "_http_request_id", uuid.uuid4().hex))

    def log_message(self, _format, *_args):
        # The default request line includes raw paths and query strings.
        # A sanitized structured receipt is emitted exactly once above.
        pass
