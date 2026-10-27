"""Local, bounded operational events. Queue state remains the delivery ledger."""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import uuid
from contextlib import suppress
from pathlib import Path

from .rolling_logs import RETENTION_DAYS, RollingLog

log = logging.getLogger(__name__)
_EVENTS = {
    "dispatch_sent",
    "dispatch_retry",
    "dispatch_throttled",
    "dispatch_failed",
    "dispatch_skipped",
    "privacy_blocked",
    "audit_throttled",
    "worker_error",
    "queue_summary",
    "intake",
    "delivery_cycle",
    "monitor_error",
    "welcome_cycle",
    "welcome_error",
}
_STATES = {"pending", "leased", "sent", "failed", "expired", "skipped"}
_REASONS = {
    "stale",
    "seeded",
    "observation_no_longer_matches",
    "dispatch_invalidated",
    "rate_limited",
    "remote_retry",
    "stopping",
    "missing_destination",
    "retry_limit",
    "http_400",
    "http_401",
    "http_403",
    "http_404",
    "http_413",
    "http_413_review",
    "lease_changed",
    "invalid_dispatch",
    "privacy_audit",
    "audit_error",
    "file_unavailable",
    "invalid_metrics",
    "unavailable",
    "none",
    "other",
}
_NUMBERS = {
    "items",
    "messages",
    "sent",
    "retried",
    "failed",
    "skipped",
    "throttled",
    "new",
    "duplicates",
    "queued",
    "observations",
    "status",
    "provider_code",
    "request_bytes",
    "response_bytes",
    "attempt",
    "retry_after_seconds",
    "due_delay_seconds_min",
    "due_delay_seconds_max",
    "elapsed_seconds",
    "audit_seconds",
    "wait_seconds",
    "reconciled",
    "review",
}
_ENUMS = {
    "tier": {"pro", "starter", "free"},
    "scope": {"route", "global", "audit"},
    "phase": {"worker", "dispatch", "audit", "queue_summary", "intake", "welcome"},
    "reason": _REASONS,
}
_METRICS = {
    "due_items",
    "due_free_items",
    "oldest_due_age_seconds",
    "sent_items_last_5m",
    "sent_messages_last_5m",
    "global_wait_seconds",
    "active_route_cooldowns",
    "failed_items",
    "expired_items",
    "skipped_items",
    "pending",
    "future",
    "due",
    "retry_wait",
    "throttled",
    "leased",
    "samples",
    "p50",
    "p95",
    "max",
}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1e15


def _metrics(value):
    """Only known queue dimensions with numeric values; never provider strings."""
    if not isinstance(value, dict):
        return {}
    clean = {}
    for key, item in value.items():
        if key in _METRICS and (item is None or _number(item)):
            clean[key] = item
        elif key in {"by_tier", "recent_delivery_latency_seconds"} and isinstance(item, dict):
            clean[key] = {
                tier: _metrics(data)
                for tier, data in item.items()
                if tier in {"pro", "starter", "free"}
            }
        elif key == "reasons" and isinstance(item, dict):
            clean[key] = {
                state: {
                    reason: count
                    for reason, count in reasons.items()
                    if reason in _REASONS and _number(count)
                }
                for state, reasons in item.items()
                if state in _STATES and isinstance(reasons, dict)
            }
    return clean


def emit_event(sink, event, **fields):
    """Observability must never alter settlement or cause another provider write."""
    if sink is not None:
        with suppress(Exception):
            sink(event, **fields)


class DeliveryMonitor:
    """Private JSONL events; 60-day compressed history with a 32-MiB backstop."""

    def __init__(
        self,
        directory,
        *,
        entry_point,
        console=True,
        max_bytes=5 * 1024 * 1024,
        backups=256,
        retention_days=RETENTION_DAYS,
        total_bytes=32 * 1024 * 1024,
        clock=None,
    ):
        if entry_point not in {"collector_worker", "standalone_worker", "cli_enqueue"}:
            raise ValueError("invalid monitoring entry point")
        if max_bytes < 512 or not 1 <= backups <= 256:
            raise ValueError("invalid log rotation bounds")
        self.path = Path(directory) / "delivery.jsonl"
        self.entry_point, self.run_id = entry_point, uuid.uuid4().hex
        self.console, self.max_bytes, self.backups = console, max_bytes, backups
        self.clock = clock or time.time
        self._lock = threading.RLock()
        self._next_heartbeat = 0
        self._last_error = None
        self._dropped = 0
        self._writer = RollingLog(
            self.path, max_bytes=max_bytes, total_bytes=total_bytes,
            retention_days=retention_days, backups=backups, clock=self.clock,
        )

    def emit(self, event, **fields):
        try:
            return self._emit(event, **fields)
        except Exception:
            with self._lock:
                self._last_error = "invalid_metrics"
                self._dropped += 1
            return False

    def _emit(self, event, **fields):
        if event not in _EVENTS:
            return False
        row = {
            "event": event,
            "timestamp": self.clock(),
            "entry_point": self.entry_point,
            "run_id": self.run_id,
            "correlation_id": self.run_id,
        }
        for key, value in fields.items():
            if key in _NUMBERS and _number(value):
                row[key] = round(value, 4) if isinstance(value, float) else value
            elif (
                key
                in {
                    "seed_only",
                    "ambiguous",
                    "blocked",
                    "settled",
                    "deferred",
                    "baseline",
                    "throttled",
                }
                and type(value) is bool
            ):
                row[key] = value
            elif key in {"correlation_id", "dispatch_id"} and isinstance(value, str):
                if re.fullmatch(r"[a-f0-9]{16,64}", value):
                    row[key] = value
            elif key == "route" and isinstance(value, str):
                if re.fullmatch(
                    r"(?:pro-all|starter-items|yesterday-recap|(?:type|price)-[a-z0-9-]{1,40})",
                    value,
                ):
                    row[key] = value
            elif key in _ENUMS and isinstance(value, str) and value in _ENUMS[key]:
                row[key] = value
            elif key == "error_type" and isinstance(value, str):
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value):
                    row[key] = value
            elif key in {"job_ids", "item_ids"} and isinstance(value, list):
                pattern = r"[a-f0-9]{16,64}" if key == "job_ids" else r"ip-\d{1,20}"
                row[key] = [
                    item
                    for item in value[:10]
                    if isinstance(item, str) and re.fullmatch(pattern, item)
                ]
            elif key == "value_cents" and isinstance(value, list):
                row[key] = [item if item is None or _number(item) else None for item in value[:10]]
            elif key == "queue":
                row[key] = _metrics(value)
            elif key == "jobs" and isinstance(value, dict):
                row[key] = {
                    state: count
                    for state, count in value.items()
                    if state in _STATES and _number(count)
                }
        encoded = (json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n").encode()
        try:
            self._writer.append(encoded)
            with self._lock:
                self._last_error = None
        except OSError:
            with self._lock:
                self._last_error = "file_unavailable"
                self._dropped += 1
            if self.console:
                log.error(
                    json.dumps(
                        {
                            "event": "monitor_error",
                            "reason": "file_unavailable",
                            "entry_point": self.entry_point,
                            "run_id": self.run_id,
                            "correlation_id": row["correlation_id"],
                        }
                    )
                )
            return False
        if self.console:
            level = logging.INFO
            if event in {"worker_error", "dispatch_failed", "privacy_blocked", "welcome_error"}:
                level = logging.ERROR
            elif event in {"dispatch_retry", "dispatch_throttled", "audit_throttled"}:
                level = logging.WARNING
            log.log(level, encoded.decode().rstrip())
        return True

    def intake(self, result, *, seed_only=False):
        emit_event(
            self.emit,
            "intake",
            **{key: result.get(key, 0) for key in ("new", "duplicates", "queued")},
            observations=result.get("accepted", result.get("new", 0) + result.get("duplicates", 0)),
            seed_only=seed_only,
            correlation_id=uuid.uuid4().hex,
        )

    def heartbeat(self, store, *, now=None, force=False):
        now = self.clock() if now is None else now
        with self._lock:
            if not force and now < self._next_heartbeat:
                return False
            self._next_heartbeat = now + 60
        try:
            queue, jobs = store.delivery_metrics(now=now), store.counts()
        except Exception as exc:
            emit_event(
                self.emit, "worker_error", phase="queue_summary", error_type=type(exc).__name__
            )
            return False
        return self.emit("queue_summary", queue=queue, jobs=jobs)

    def status(self):
        with self._lock:
            return {
                "file": self.path.name,
                "max_bytes": self.max_bytes,
                "backups": self.backups,
                "retention_days": self._writer.retention_days,
                "total_max_bytes": self._writer.total_bytes,
                "compressed_archives": True,
                "last_error": self._last_error,
                "dropped_events": self._dropped,
                "heartbeat_interval_seconds": 60,
            }
