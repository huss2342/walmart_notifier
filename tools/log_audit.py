"""Bounded, read-only audit of local operational logs; never prints raw records."""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import heapq
import json
import math
import re
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

SOURCES = {
    "browser": "browser-diagnostic-events.jsonl",
    "delivery": "discord_bridge/delivery.jsonl",
    "requests": "discord_bridge/requests.jsonl",
    "system": "operations.jsonl",
    "native": "native-verification/host.log",
    "recovery": "browser-recovery.log",
}
EVENTS = frozenset(
    [
        "page-loaded",
        "page-delivered",
        "navigation-native",
        "navigation-fallback",
        "navigation-interrupted",
        "pacing",
        "challenge",
        "verification-resumed",
        "traffic-wait",
        "traffic-resumed",
        "sweep-start",
        "sweep-complete",
        "ingest-retry",
        "empty-retry",
        "recovery-reload",
        "live-update",
        "navigation-ready",
        "pager-inspection",
        "tab-opened",
        "scan-restart-native",
        "scan-restart-load",
        "empty-boundary-reused",
        "results-page-ready",
        "navigation-retry",
        "card-extraction",
        "coverage-unresolved",
        "auxiliary-target-start",
        "auxiliary-page-delivered",
        "auxiliary-target-skipped",
        "auxiliary-error",
        "auxiliary-target-retry",
        "navigation-outcome",
        "focus-granted",
        "focus-denied",
        "focus-released",
        "pager-clamped",
        "page-repeat",
        "page-state",
        "intake",
        "dispatch_claimed",
        "dispatch_sent",
        "dispatch_retry",
        "dispatch_failed",
        "dispatch_skipped",
        "dispatch_throttled",
        "worker_error",
        "privacy_blocked",
        "audit_throttled",
        "queue_summary",
        "delivery_cycle",
        "welcome_cycle",
        "welcome_error",
        "request_match",
        "request_changed",
        "request_provider_error",
        "request_queue_summary",
        "monitor_error",
        "http-complete",
        "http-request",
        "http-error",
        "http_request",
        "http_response",
        "http_error",
        "http_completed",
        "request_complete",
        "request_error",
        "service-started",
        "service-stopped",
        "service-error",
        "service_starting",
        "service_started",
        "service_stopped",
        "service_error",
        "configuration_changed",
        "intake_result",
        "pipeline_item",
        "native-session-started",
        "native-operation",
        "native-session-closed",
        "healthy",
        "disabled",
        "refresh-disabled",
        "verification-paused",
        "heartbeat-grace",
        "startup-grace",
        "outage-already-handled",
        "launch-cooldown",
        "launch-hourly-limit",
        "launch-chrome",
        "invalid-config",
        "invalid-relay-settings",
        "chrome-unavailable",
        "chrome-profile-unavailable",
        "would-launch-chrome",
        "chrome-launched",
        "chrome-launch-failed",
        "would-check-local-notifier",
        "notifier-start-requested",
        "notifier-start-failed",
        "docker-start-requested",
        "docker-start-unavailable",
        "chrome-profile-selected",
        "recovery-already-running",
        "recovery-check-failed",
        "recovery-state-unavailable",
        "recovery-state-cleanup-unavailable",
        "recovery-started",
        "recovery-stopped",
        "browser-log-health",
        "logging-health",
        "log_health",
        "logging_health",
    ]
)
NATIVE_OPS = frozenset({"probe", "prepare", "press", "status", "release", "close", "invalid"})
ROUTES = frozenset(
    [
        "pro-all",
        "starter-items",
        "yesterday-recap",
        "price-under-10",
        "price-10-20",
        "price-20-40",
        "price-40-80",
        "price-80-200",
        "price-200-plus",
        "type-clothing-accessories",
        "type-home-storage-decor",
        "type-other",
        "type-toys-baby-books",
        "type-tools-auto",
        "type-kitchen-appliances",
        "type-furniture",
        "type-electronics",
        "type-sports-office-travel",
        "type-beauty-health",
        "type-pets",
        "collector-health",
        "/health",
        "/ingest",
        "/browser-diagnostics",
        "/browser-heartbeat",
        "/relay-settings",
        "/scanner-settings",
        "/scanner-status",
        "/scanner-commands",
        "/test",
        "/rescan",
        "/",
        "/rules",
        "/relay-status",
        "/test-notification",
        "/bot-check",
        "health",
        "ingest",
        "browser_diagnostics",
        "browser_heartbeat",
        "relay_settings",
        "scanner_settings",
        "scanner_status",
        "scanner_commands",
        "test",
        "rescan",
    ]
)
LABELS = {
    "severity": {
        "debug",
        "info",
        "warning",
        "error",
        "critical",
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
    },
    "method": {"GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH", "OTHER"},
    "lane": {"home", "departments", "keywords", "brands", "priority"},
    "state": {
        "pending",
        "leased",
        "sending",
        "sent",
        "failed",
        "skipped",
        "expired",
        "review",
        "cancelled",
    },
    "audience": {"personal", "channel"},
    "entry_point": {
        "http",
        "service",
        "browser-diagnostics",
        "collector_worker",
        "standalone_worker",
        "cli_enqueue",
        "native",
        "recovery",
    },
    "outcome": {"responded", "rejected", "error", "saved", "removed"},
    "component": {
        "backend",
        "rules",
        "relay-settings",
        "scanner-settings",
        "scanner-commands",
        "scanner-status",
    },
    "provider": {"discord", "telegram", "ntfy", "pushover", "none", "other"},
    "decision": {"pending", "duplicate", "seeded", "filtered", "sent", "failed"},
    "phase": {"idle", "prepared", "holding", "released", "aborted", "closed"},
    "op": NATIVE_OPS,
    "error_type": {
        "ValueError",
        "TypeError",
        "OSError",
        "TimeoutError",
        "ConnectionError",
        "OperationalError",
        "IntegrityError",
        "HTTPError",
        "BrokenPipeError",
        "UnicodeDecodeError",
        "JSONDecodeError",
        "RuntimeError",
        "DiscordError",
    },
    "reason": {
        "unsupported-platform",
        "invalid-request",
        "invalid-origin",
        "invalid-window",
        "helper-busy",
        "desktop-locked",
        "user-busy",
        "button-held",
        "focus-changed",
        "window-changed",
        "viewport-mismatch",
        "target-obscured",
        "target-expired",
        "user-input",
        "input-failed",
        "release-failed",
        "watchdog-unavailable",
        "already-used",
        "not-prepared",
        "host-unavailable",
        "max-duration",
        "closed",
    },
}
LABELS["error"] = LABELS["reason"]
EVENTS |= {
    "http_request_started",
    "request_dm_status",
    "request_interaction",
    "observation_routed",
    "worker_lifecycle",
    "worker_recovered",
    "gateway_state",
    "dispatch_sending",
    "referral_cycle",
    "referral_changed",
    "referral_error",
}
LABELS["component"] |= {
    "channel",
    "personal",
    "request-runtime",
    "gateway",
    "welcome",
    "membership",
    "referrals",
}
LABELS["outcome"] |= {"not_submitted", "accepted", "unknown"}
LABELS["phase"] |= {
    "worker",
    "dispatch",
    "audit",
    "queue_summary",
    "intake",
    "welcome",
    "claim",
    "member",
    "channel",
    "render",
    "send",
    "settle",
    "recovery",
    "validate",
    "begin_send",
    "loop",
    "prepare",
    "poll",
    "reconcile",
    "apply",
    "lookup",
}
LABELS["transition"] = {
    "started",
    "stopped",
    "ready",
    "disconnected",
    "error",
    "recovered",
    "queued",
    "rearmed",
}
LABELS["failureCode"] = {
    "none",
    "missing-document",
    "unsupported-kind",
    "ambiguous-filter-section",
    "missing-filter-section",
    "ambiguous-control",
    "missing-control",
    "missing-panel",
    "ambiguous-expansion",
    "missing-expansion",
    "expansion-stalled",
    "expansion-overflow",
    "option-overflow",
    "missing-selection",
    "ambiguous-selection",
    "already-selected",
    "cancelled",
    "facet-error",
    "controls-unavailable",
    "discovery-save-failed",
    "content-unresponsive",
    "progress-timeout",
    "unexpected-error",
    "selection-render-timeout",
    "selection-document-timeout",
    "selection-bind-failed",
    "selection-unconfirmed",
    "document-http-error",
    "render-timeout",
    "render-opportunity-timeout",
    "navigation-timeout",
    "navigation-scope-changed",
}
LABELS["transport_error"] = {
    "TimeoutError",
    "gaierror",
    "SSLError",
    "ConnectionError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "BrokenPipeError",
    "RemoteDisconnected",
    "BadStatusLine",
    "IncompleteRead",
    "HTTPException",
    "URLError",
    "UnicodeDecodeError",
    "JSONDecodeError",
    "ValueError",
    "OSError",
    "other",
}
DURATIONS = {
    "durationMs": 0.001,
    "duration_ms": 0.001,
    "holdMs": 0.001,
    "elapsed_seconds": 1,
    "audit_seconds": 1,
    "due_delay_seconds_min": 1,
    "due_delay_seconds_max": 1,
    "latency_seconds": 1,
}
NUMBERS = frozenset(
    {
        "status",
        "responseStatus",
        "response_bytes",
        "items",
        "new",
        "duplicates",
        "queued",
        "failed",
        "filtered",
        "pending",
        "notified",
        "request_bytes",
        "attempt",
        "page",
        "targetPage",
        "round",
        "targetIndex",
        "eventSeq",
        "sequence",
        "dropped_events",
        "droppedEvents",
        "rejectedEvents",
        "pendingEvents",
        "sent",
        "count",
    }
)
# Keep this offline reader aligned with the released, allowlisted browser schema.
# Contract tests prevent a future producer change from becoming an opaque "other".
EVENTS |= {
    "ingest-stage",
    "auxiliary-target-complete",
    "browser-error",
    "sync-state",
    "verification-state",
}
NUMBERS |= {
    "tabId",
    "generation",
    "documentId",
    "totalPages",
    "pages",
    "batchIndex",
    "batchCount",
    "resultAt",
    "episodeAt",
    "matched",
    "recognizedPagerCount",
    "exactHrefCount",
    "visibleExactHrefCount",
    "queryMismatchCount",
    "targetLabelButtonCount",
    "nextLabelButtonCount",
    "scopedButtonCount",
    "roleButtonCount",
    "pagerElementCount",
    "pagerAnchorCount",
    "pagerButtonCount",
    "pagerInputCount",
    "pagerNumericTextCount",
    "pagerTargetTextCount",
    "pagerTargetAriaCount",
    "pagerHiddenCount",
    "pagerMarkerCount",
    "pagerPlaceholderHrefCount",
    "pagerTargetPlaceholderCount",
    "pagerPageHrefCount",
    "pagerOtherHrefCount",
    "acceptedCount",
    "rejectedCount",
    "variantCount",
    "outOfStockCount",
    "missingValueCount",
}
DURATIONS.update({"queueMs": 0.001, "elapsedMs": 0.001, "intervalMs": 0.001, "delayMs": 0.001})
LABELS["outcome"] |= {
    "waiting",
    "accepted-stale",
    "retry",
    "timeout",
    "network",
    "invalid-response",
    "cancelled",
    "items",
    "empty",
    "unresolved",
    "duplicate",
    "starting",
    "unsupported",
    "needs-attention",
    "input-error",
    "verified",
    "status",
    "recovered",
    "ready",
    "interrupted",
    "http-error",
}
LABELS["component"] |= {
    "message",
    "heartbeat",
    "initialization",
    "refresh",
    "auxiliary",
    "diagnostics",
    "settings",
    "commands",
    "status",
    "coordinator",
    "verification",
}
LABELS["reason"] |= {
    "redirect",
    "dom-marker",
    "text",
    "probe",
    "missing-control",
    "native-stalled",
    "native-backoff",
    "unusable",
    "unreachable",
    "scheduled",
    "manual",
    "installed",
    "failover",
    "empty-results",
    "blank-timeout",
    "waiting-turn",
    "busy",
    "user-active",
    "expired",
    "unavailable",
    "disabled",
    "paused",
    "revoked",
    "not-authorized",
    "manual-tab-active",
    "activation-unavailable",
    "not-scanner",
    "selection-changed",
}
LABELS["mode"] = {"none", "sweep", "high-value"}
LABELS["operation"] = {"pager", "facet-controls", "page-scroll", "page-navigation"}
LABELS["stage"] = {
    "queued",
    "batch",
    "complete",
    "empty-observed",
    "empty-confirmation",
    "fallback",
    "unusable",
    "zero-page-complete",
    "recovered",
}
LABELS["intakePhase"] = {"initial", "late", "live"}
LABELS["statusCode"] = {
    "starting",
    "unsupported",
    "needs-attention",
    "input-error",
    "cancelled",
    "verified",
    "watching",
    "permission-required",
    "retrying-control",
    "waiting-page",
    "invalid-budget",
    "attempts-exhausted",
    "already-attempted",
    "waiting-tab-ready",
    "waiting-foreground",
    "cooldown",
    "unavailable",
    "native-permission-required",
    "native-connecting",
    "connecting",
    "retry-pending",
    "collector-pending",
    "native-waiting-idle",
    "native-unavailable",
}
LABELS["errorStage"] = {"inspect", "move", "press", "release", "detach"}
LABELS["errorReason"] = {"timeout", "detached", "command-failed"}
LABELS["releaseReason"] = {
    "none",
    "deadline",
    "page-changed",
    "cancelled",
    "failed",
    "feedback",
    "stalled",
    "max-duration",
}
LABELS["transport"] = {"native", "debugger"}
HEX_ID = re.compile(r"[a-f0-9]{32}")
JOB_ID = re.compile(r"[a-f0-9]{16,64}")
ITEM_ID = re.compile(r"ip-\d{6,20}")
STATES = LABELS["state"]


def _number(value):
    return type(value) in {int, float} and 0 <= value <= 1e15 and math.isfinite(value)


def _percentiles(values):
    values.sort()
    return {
        "samples": len(values),
        "p50": values[math.ceil(len(values) * 0.5) - 1],
        "p95": values[math.ceil(len(values) * 0.95) - 1],
        "p99": values[math.ceil(len(values) * 0.99) - 1],
        "max": values[-1],
    }


def _safe_event(row, source):
    if not isinstance(row, dict):
        return None
    stamp = row.get("timestamp", row.get("at"))
    if not _number(stamp):
        return None
    if source == "browser" and "timestamp" not in row:
        stamp /= 1000
    name = row.get("event")
    if (
        name is None
        and source == "native"
        and isinstance(row.get("op"), str)
        and row["op"] in NATIVE_OPS
    ):
        name = "native-" + row["op"]
    recognized = isinstance(name, str) and (
        name in EVENTS or name in {"native-" + op for op in NATIVE_OPS}
    )
    clean = {"source": source, "timestamp": stamp, "event": name if recognized else "other"}
    for field, choices in LABELS.items():
        if isinstance(row.get(field), str):
            clean[field] = row[field] if row[field] in choices else "other"
    if isinstance(row.get("route"), str):
        clean["route"] = row["route"] if row["route"] in ROUTES else "other"
    for field in NUMBERS | DURATIONS.keys():
        if _number(row.get(field)):
            clean[field] = row[field]
    for field in ("ok", "pressSent", "pressConfirmed", "releaseConfirmed", "cleanupConfirmed"):
        if type(row.get(field)) is bool:
            clean[field] = row[field]
    # Delivery request_id is an integer watch ID. Only HTTP IDs join HTTP traces.
    for field in ("http_request_id", "requestId", "request_id"):
        request = row.get(field)
        if isinstance(request, str) and HEX_ID.fullmatch(request):
            clean["http_request_id"] = request
            break
    for field in ("run_id", "streamId", "correlation_id"):
        if isinstance(row.get(field), str) and HEX_ID.fullmatch(row[field]):
            clean[field] = row[field]
    if isinstance(row.get("dispatch_id"), str) and JOB_ID.fullmatch(row["dispatch_id"]):
        clean["dispatch_id"] = row["dispatch_id"]
    return clean, recognized


def _links(row):
    items, jobs, pairs = set(), set(), collections.defaultdict(set)
    if isinstance(row.get("item_id"), str) and ITEM_ID.fullmatch(row["item_id"]):
        items.add(row["item_id"])
    for item_key, job_key, limit in (
        ("item_ids", "job_ids", 10),
        ("observation_item_ids", "observation_job_ids", 500),
    ):
        item_list = row.get(item_key) if isinstance(row.get(item_key), list) else []
        job_list = row.get(job_key) if isinstance(row.get(job_key), list) else []
        items.update(
            value
            for value in item_list[:limit]
            if isinstance(value, str) and ITEM_ID.fullmatch(value)
        )
        jobs.update(
            value
            for value in job_list[:limit]
            if isinstance(value, str) and JOB_ID.fullmatch(value)
        )
        if len(item_list) == len(job_list):
            for item, job in zip(item_list[:limit], job_list[:limit], strict=True):
                if (
                    isinstance(item, str)
                    and ITEM_ID.fullmatch(item)
                    and isinstance(job, str)
                    and JOB_ID.fullmatch(job)
                ):
                    pairs[item].add(job)
    if isinstance(row.get("samples"), list):
        items.update(
            sample["itemId"]
            for sample in row["samples"][:12]
            if isinstance(sample, dict)
            and isinstance(sample.get("itemId"), str)
            and ITEM_ID.fullmatch(sample["itemId"])
        )
    return items, jobs, pairs


def _related(candidates, *, item_id, request_id, maximum, quality):
    nodes = {name: set() for name in ("items", "jobs", "requests", "dispatches")}

    def add(kind, values):
        for value in values:
            if value in nodes[kind]:
                continue
            if sum(len(group) for group in nodes.values()) >= maximum:
                quality["relationships_truncated"] = True
                return
            nodes[kind].add(value)

    if item_id and not request_id:
        add("items", [item_id])
    if request_id and not item_id:
        add("requests", [request_id])
    for event, items, jobs, pairs in candidates:
        direct_request = request_id and request_id in {
            event.get("http_request_id"),
            event.get("correlation_id"),
        }
        if item_id:
            if item_id not in items:
                continue
            if request_id and not direct_request:
                continue
            add("items", [item_id])
            if request_id:
                add("requests", [request_id])
            # Keep one item's exact parallel job IDs, rather than a shared 500-item batch.
            add("jobs", pairs.get(item_id, jobs if len(items) == 1 else ()))
        elif direct_request:
            add("items", items)
            add("jobs", jobs)
        else:
            continue
        if event.get("http_request_id"):
            add("requests", [event["http_request_id"]])
        if event.get("dispatch_id"):
            add("dispatches", [event["dispatch_id"]])
    matched = []
    for event, items, jobs, _pairs in candidates:
        linked = bool(jobs & nodes["jobs"] or (not jobs and items & nodes["items"]))
        if linked and event.get("dispatch_id"):
            add("dispatches", [event["dispatch_id"]])
    for event, items, jobs, _pairs in candidates:
        direct_item = item_id in items if item_id else False
        direct_request = request_id and request_id in {
            event.get("http_request_id"),
            event.get("correlation_id"),
        }
        linked_job = bool(jobs & nodes["jobs"])
        linked_dispatch = event.get("dispatch_id") in nodes["dispatches"]
        http_context = event.get("http_request_id") in nodes["requests"] and not items and not jobs
        fallback = bool(items & nodes["items"] and (not jobs or not nodes["jobs"]))
        if item_id and not (direct_item or linked_job or linked_dispatch or http_context):
            continue
        if request_id and not (direct_request or linked_job or linked_dispatch or fallback):
            continue
        if http_context and not direct_request:
            event["trace_context"] = True
        if request_id and fallback and not (direct_request or linked_job or linked_dispatch):
            event["trace_link"] = "item-fallback; association does not prove request ownership"
        matched.append(event)
    return matched, nodes


def _has_item(row, wanted):
    return (
        row.get("item_id") == wanted
        or isinstance(row.get("item_ids"), list)
        and wanted in row["item_ids"]
        or isinstance(row.get("observation_item_ids"), list)
        and wanted in row["observation_item_ids"][:500]
        or isinstance(row.get("samples"), list)
        and any(
            isinstance(sample, dict) and sample.get("itemId") == wanted
            for sample in row["samples"][:12]
        )
    )


def _paths(directory, sources, limit, quality):
    found = []
    for source in sources:
        base = directory / SOURCES[source]
        pattern = re.compile(
            re.escape(base.name) + r"(?:\.[1-9][0-9]*|\.\d{8}T\d{12}-[a-f0-9]{8}\.gz)?"
        )
        try:
            for path in base.parent.glob(base.name + "*"):
                if not pattern.fullmatch(path.name) or not path.is_file():
                    continue
                if not path.resolve().is_relative_to(directory.resolve()):
                    quality["read_errors"] += 1
                    continue
                found.append((source, path))
                if len(found) > limit:
                    quality["scan_truncated"] = True
                    return found[:limit]
        except OSError:
            quality["read_errors"] += 1

    def recent_first(value):
        try:
            return value[1].stat().st_mtime, value[0], value[1].name
        except OSError:
            quality["read_errors"] += 1
            return 0, value[0], value[1].name

    # Prefer recent segments across streams when the explicit scan budget is hit.
    return sorted(found, key=recent_first, reverse=True)


def _lines(handle, quality, *, max_bytes, max_line_bytes):
    while quality["bytes_read"] < max_bytes:
        remaining = max_bytes - quality["bytes_read"]
        line = handle.readline(min(max_line_bytes + 1, remaining))
        if not line:
            return
        quality["bytes_read"] += len(line)
        if len(line) > max_line_bytes:
            quality["oversized_lines"] += 1
            while not line.endswith(b"\n") and quality["bytes_read"] < max_bytes:
                line = handle.readline(min(max_line_bytes + 1, max_bytes - quality["bytes_read"]))
                quality["bytes_read"] += len(line)
                if not line:
                    return
            continue
        if not line.endswith(b"\n"):
            quality["partial_lines"] += 1
            if quality["bytes_read"] >= max_bytes:
                quality["scan_truncated"] = True
            return
        yield line
    quality["scan_truncated"] = True


class _ReadBudgetReached(OSError):
    pass


class _CompressedBudget:
    def __init__(self, handle, quality, limit):
        self.handle, self.quality, self.limit = handle, quality, limit

    def read(self, size=-1):
        remaining = self.limit - self.quality["compressed_bytes_read"]
        if remaining <= 0:
            self.quality["scan_truncated"] = True
            raise _ReadBudgetReached
        chunk = self.handle.read(min(remaining, size) if size >= 0 else remaining)
        self.quality["compressed_bytes_read"] += len(chunk)
        return chunk


@contextmanager
def _open_log(path, quality, max_bytes):
    with path.open("rb") as handle:
        if path.suffix == ".gz":
            # Bound malformed gzip headers/padding even before any bytes decode.
            with gzip.GzipFile(fileobj=_CompressedBudget(handle, quality, max_bytes)) as stream:
                yield stream
        else:
            yield handle


def _ledger(directory, *, since, until, item_id, related=None):
    result = {"available": False, "counts": {}}
    path = directory / "discord_bridge" / "collector.sqlite3"
    if not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
        return result
    try:
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        ) as db:
            db.execute("PRAGMA query_only=ON")
            deadline = time.monotonic() + 5
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 5000)
            where, args = "due BETWEEN ? AND ?", [since, until]
            if item_id:
                where += " AND item_id=?"
                args.append(item_id)
            relation_where, relation_args = "", []
            if related is not None:
                if related["jobs"]:
                    relation_where = " AND id IN (SELECT value FROM json_each(?))"
                    relation_args = [json.dumps(sorted(related["jobs"]))]
                    result["scope"] = "exact job IDs linked by routing receipts"
                else:
                    relation_where = " AND item_id IN (SELECT value FROM json_each(?))"
                    relation_args = [json.dumps(sorted(related["items"]))]
                    result["scope"] = (
                        "associated item IDs; broader than immutable request ownership"
                    )
            where += relation_where
            args.extend(relation_args)
            groups = db.execute(
                "SELECT route,state,COUNT(*) FROM jobs WHERE "
                + where
                + " GROUP BY route,state LIMIT 129",
                args,
            ).fetchall()
            result["truncated"] = len(groups) > 128
            for route, state, count in groups[:128]:
                key = (
                    (route if route in ROUTES else "other")
                    + ":"
                    + (state if state in STATES else "other")
                )
                result["counts"][key] = result["counts"].get(key, 0) + count
            result["available"] = True
            result["cohort"] = "destination jobs scheduled within the selected window"
            delays = collections.defaultdict(list)
            sent_args = [since, until]
            sent_where = "state='sent' AND sent_at BETWEEN ? AND ?"
            if item_id:
                sent_where += " AND item_id=?"
                sent_args.append(item_id)
            sent_where += relation_where
            sent_args.extend(relation_args)
            rows = db.execute(
                "SELECT route,MAX(0,sent_at-due) FROM jobs WHERE "
                + sent_where
                + " ORDER BY sent_at DESC LIMIT 10001",
                sent_args,
            ).fetchall()
            result["latency_truncated"] = len(rows) > 10000
            for route, delay in rows[:10000]:
                if _number(delay):
                    delays[route if route in ROUTES else "other"].append(delay)
            result["sent_latency_seconds"] = {
                route: _percentiles(values) for route, values in sorted(delays.items())
            }
            result["latency_cohort"] = (
                "destination jobs sent within the selected window; delay after scheduled due"
            )
    except (sqlite3.Error, OSError):
        result["error"] = "ledger_unavailable"
    return result


def _browser_health(directory, quality, max_bytes):
    result = {"available": False, "scope": "latest snapshot; cumulative outside the event window"}
    path = directory / "browser-diagnostics.json"
    if not path.is_file():
        return result
    remaining = max_bytes - quality["bytes_read"]
    if remaining <= 0:
        quality["scan_truncated"] = True
        return result
    try:
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError
        with path.open("rb") as handle:
            encoded = handle.read(min(1024 * 1024 + 1, remaining))
        quality["bytes_read"] += len(encoded)
        if len(encoded) > 1024 * 1024:
            raise ValueError
        snapshot = json.loads(encoded)
        logging = snapshot.get("report", {}).get("logging")
        if not isinstance(logging, dict):
            return result
        safe = {}
        for field in (
            "lastSequence",
            "recorded",
            "rejected",
            "storageErrors",
            "ringOverwrites",
            "unacknowledgedOverwrites",
            "pending",
            "lastRecordedAt",
            "lastSyncAt",
            "syncAttempts",
            "syncFailures",
            "consecutiveSyncFailures",
            "lastSyncStatus",
            "acknowledgedSequence",
        ):
            if _number(logging.get(field)):
                safe[field] = logging[field]
        if logging.get("lastSyncError") in {
            "none",
            "timeout",
            "network",
            "http",
            "storage",
            "report",
        }:
            safe["lastSyncError"] = logging["lastSyncError"]
        result.update(available=True, counters=safe)
    except (OSError, ValueError, UnicodeError, RecursionError, AttributeError, TypeError):
        quality["read_errors"] += 1
        if quality["bytes_read"] >= max_bytes:
            quality["scan_truncated"] = True
    return result


def audit(
    directory,
    *,
    since=None,
    until=None,
    source=None,
    item_id=None,
    request_id=None,
    trace=False,
    ledger=False,
    max_files=512,
    max_bytes=128 * 1024 * 1024,
    max_line_bytes=65536,
    max_events=500_000,
    max_output=100,
    max_related=10000,
    max_candidates=250000,
):
    """Return safe aggregates and an optional bounded trace. No source is modified."""
    until = time.time() if until is None else until
    since = until - 86400 if since is None else since
    limits = (
        max_files,
        max_bytes,
        max_line_bytes,
        max_events,
        max_output,
        max_related,
        max_candidates,
    )
    if (
        not all(type(value) is int and value > 0 for value in limits)
        or max_files > 4096
        or max_bytes > 1024**3
        or max_line_bytes > 1024**2
        or max_events > 2_000_000
        or max_output > 10000
        or max_related > 100000
        or max_candidates > 1000000
        or not _number(since)
        or not _number(until)
        or since > until
        or (
            item_id is not None and (not isinstance(item_id, str) or not ITEM_ID.fullmatch(item_id))
        )
        or (
            request_id is not None
            and (not isinstance(request_id, str) or not HEX_ID.fullmatch(request_id))
        )
        or (source is not None and (not isinstance(source, str) or source not in SOURCES))
    ):
        raise ValueError("Invalid audit options")
    directory = Path(directory)
    quality = dict.fromkeys(
        (
            "bytes_read",
            "compressed_bytes_read",
            "rows_examined",
            "malformed_rows",
            "unknown_format_rows",
            "unknown_rows",
            "oversized_lines",
            "partial_lines",
            "read_errors",
            "duplicate_rows",
            "identity_conflicts",
        ),
        0,
    )
    quality.update(scan_truncated=False, output_truncated=False, relationships_truncated=False)
    sources = [source] if source else list(SOURCES)
    statuses = {
        name: {"available": False, "files": 0, "events": 0, "issues": {}} for name in sources
    }
    paths = _paths(directory, sources, max_files, quality)
    counts, durations, seen, records = (
        collections.Counter(),
        collections.defaultdict(list),
        {},
        [],
    )
    events = 0
    candidates = []

    def consume(event):
        nonlocal events
        events += 1
        name = event["source"]
        statuses[name]["events"] += 1
        counts[name + ":" + event["event"]] += 1
        for key, multiplier in DURATIONS.items():
            if key in event:
                durations[name + ":" + event["event"] + ":" + key].append(event[key] * multiplier)
        if trace:
            if len(records) < max_output:
                heapq.heappush(records, (event["timestamp"], events, event))
            else:
                quality["output_truncated"] = True
                heapq.heappushpop(records, (event["timestamp"], events, event))

    for name, path in paths:
        statuses[name]["available"] = True
        statuses[name]["files"] += 1
        if (
            quality["rows_examined"] >= max_events
            or quality["bytes_read"] >= max_bytes
            or quality["compressed_bytes_read"] >= max_bytes
        ):
            quality["scan_truncated"] = True
            break
        previous_quality = dict(quality)
        try:
            with _open_log(path, quality, max_bytes) as handle:
                for line in _lines(
                    handle, quality, max_bytes=max_bytes, max_line_bytes=max_line_bytes
                ):
                    if quality["rows_examined"] >= max_events:
                        quality["scan_truncated"] = True
                        break
                    quality["rows_examined"] += 1
                    try:
                        raw = json.loads(line)
                    except (ValueError, UnicodeError, RecursionError):
                        key = (
                            "malformed_rows"
                            if line.lstrip().startswith((b"{", b"["))
                            else "unknown_format_rows"
                        )
                        quality[key] += 1
                        continue
                    parsed = _safe_event(raw, name)
                    if parsed is None:
                        quality["unknown_rows"] += 1
                        continue
                    event, recognized = parsed
                    if not recognized:
                        quality["unknown_rows"] += 1
                    if not since <= event["timestamp"] <= until:
                        continue
                    try:
                        identity_body = raw
                        stable = (
                            name == "browser"
                            and event.get("streamId")
                            and type(raw.get("eventSeq")) is int
                            and raw["eventSeq"] > 0
                        )
                        if stable:
                            identity_body = {
                                key: value
                                for key, value in raw.items()
                                if key
                                not in {
                                    "extensionVersion",
                                    "entry_point",
                                    "http_request_id",
                                    "request_id",
                                }
                            }
                        canonical = json.dumps(
                            identity_body, sort_keys=True, separators=(",", ":"), allow_nan=False
                        )
                    except (ValueError, RecursionError):
                        quality["malformed_rows"] += 1
                        continue
                    fingerprint = hashlib.sha256(name.encode() + canonical.encode()).digest()
                    identity = (name, event["streamId"], raw["eventSeq"]) if stable else fingerprint
                    if identity in seen:
                        quality[
                            "duplicate_rows"
                            if seen[identity] == fingerprint
                            else "identity_conflicts"
                        ] += 1
                        continue
                    seen[identity] = fingerprint
                    if item_id or request_id:
                        if len(candidates) < max_candidates:
                            candidates.append((event, *_links(raw)))
                        else:
                            quality["relationships_truncated"] = True
                    else:
                        consume(event)
        except _ReadBudgetReached:
            pass
        except (OSError, EOFError, ValueError):
            quality["read_errors"] += 1
        finally:
            for key in (
                "malformed_rows",
                "unknown_format_rows",
                "unknown_rows",
                "read_errors",
                "oversized_lines",
                "partial_lines",
                "duplicate_rows",
                "identity_conflicts",
            ):
                difference = quality[key] - previous_quality[key]
                if difference:
                    issues = statuses[name]["issues"]
                    issues[key] = issues.get(key, 0) + difference
    related = None
    if item_id or request_id:
        matches, related = _related(
            candidates, item_id=item_id, request_id=request_id, maximum=max_related, quality=quality
        )
        for event in matches:
            consume(event)
    result = {
        "window": {"since": since, "until": until},
        "events": events,
        "counts": dict(sorted(counts.items())),
        "sources": statuses,
        "quality": quality,
        "durations_seconds": {
            key: _percentiles(values) for key, values in sorted(durations.items())
        },
    }
    if trace:
        result["trace"] = [row[2] for row in sorted(records)]
        result["trace_scope"] = "latest matching events, ordered by timestamp"
    if ledger:
        if request_id and not (related["jobs"] or related["items"]):
            result["ledger"] = {
                "available": False,
                "scope": "request-linked identifiers unavailable; ledger not queried",
            }
        else:
            result["ledger"] = _ledger(
                directory,
                since=since,
                until=until,
                item_id=item_id,
                related=related if request_id else None,
            )
    if related is not None:
        result["relationships"] = {
            "physical_passes": 1,
            "candidates": len(candidates),
            "max_candidates": max_candidates,
            "max_related": max_related,
            **{
                label: len(related[group])
                for label, group in (
                    ("item_ids", "items"),
                    ("job_ids", "jobs"),
                    ("request_ids", "requests"),
                    ("dispatch_ids", "dispatches"),
                )
            },
        }
    if source in {None, "browser"}:
        result["browser_logging"] = _browser_health(directory, quality, max_bytes)
    return result


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(UTC).timestamp()
    except (ValueError, OverflowError) as exc:
        raise argparse.ArgumentTypeError("Use an ISO timestamp with UTC offset") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("summary", "trace"))
    parser.add_argument(
        "--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data"
    )
    parser.add_argument("--since", type=_time)
    parser.add_argument("--until", type=_time)
    parser.add_argument("--source", choices=tuple(SOURCES))
    parser.add_argument("--item", dest="item_id")
    parser.add_argument("--request", "--correlation", dest="request_id")
    parser.add_argument(
        "--ledger", action="store_true", help="Read aggregate job metadata using SQLite mode=ro"
    )
    for flag, default in (
        ("max-files", 512),
        ("max-bytes", 128 * 1024 * 1024),
        ("max-line-bytes", 65536),
        ("max-events", 500_000),
        ("limit", 100),
        ("max-related", 10000),
        ("max-candidates", 250000),
    ):
        parser.add_argument("--" + flag, type=int, default=default)
    args = vars(parser.parse_args(argv))
    args["trace"] = args.pop("command") == "trace"
    args["max_output"] = args.pop("limit")
    directory = args.pop("data_dir")
    try:
        result = audit(directory, **args)
    except ValueError:
        parser.error("Invalid audit options")
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
