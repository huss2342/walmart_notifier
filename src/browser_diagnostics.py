"""Bounded, allowlisted browser health telemetry, independent of item state.

The HTTP boundary supplies untrusted JSON. Only documented numeric metadata,
fixed labels, product IDs and bounded card facts may be persisted. URLs, tokens
and free text have no place in this schema. Events are archived before atomic
snapshot replacement, so a failed write cannot acknowledge lost evidence.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from copy import deepcopy
from pathlib import Path

from discord_bridge.rolling_logs import RollingLog

from atomic_json import write_atomic_json
from scanner_control import _FAILURE_CODES as _SCANNER_FAILURE_CODES

log = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "browser-diagnostics.json"
MAX_FILE_BYTES = 1024 * 1024
MAX_EVENTS = 300
MAX_CARD_SAMPLES = 12
RETENTION_MS = 7 * 86_400_000
MAX_NUMBER = 1_000_000_000_000_000
INVALID_REPORT = "Invalid browser diagnostics report"

_EVENTS = frozenset({
    "page-loaded", "page-delivered", "navigation-native", "navigation-fallback",
    "navigation-interrupted", "pacing", "challenge", "verification-resumed",
    "traffic-wait", "traffic-resumed",
    "sweep-start", "sweep-complete", "ingest-retry", "empty-retry", "recovery-reload",
    "live-update", "navigation-ready", "pager-inspection", "tab-opened",
    "scan-restart-native", "scan-restart-load", "empty-boundary-reused",
    "results-page-ready", "navigation-retry", "card-extraction", "coverage-unresolved",
    "auxiliary-target-start", "auxiliary-page-delivered", "auxiliary-target-skipped",
    "auxiliary-error", "auxiliary-target-retry", "navigation-outcome", "ingest-stage",
    "auxiliary-target-complete", "browser-error", "sync-state", "verification-state",
    "focus-granted", "focus-denied", "focus-released", "pager-clamped", "page-repeat", "page-state",
})
_REASONS = frozenset({
    "redirect", "dom-marker", "text", "probe", "missing-control", "native-stalled",
    "native-backoff", "unusable", "unreachable", "scheduled", "manual", "installed", "failover",
    "empty-results", "blank-timeout",
    "waiting-turn", "busy", "user-active", "expired", "unavailable", "disabled", "paused",
    "revoked", "not-authorized", "manual-tab-active", "activation-unavailable", "not-scanner",
    "selection-changed",
})
_MODES = frozenset({"none", "sweep", "high-value"})
_NUMERIC_FIELDS = frozenset({
    "tabId", "generation", "documentId", "page", "targetPage", "totalPages", "delayMs",
    "count", "pages",
    "round", "targetIndex", "responseStatus", "queueMs", "batchIndex", "batchCount", "eventSeq",
    "resultAt", "episodeAt",
    "durationMs", "attempt", "items", "new", "duplicates", "matched", "notified",
    "intervalMs", "elapsedMs",
    "failed", "pending", "recognizedPagerCount", "exactHrefCount", "visibleExactHrefCount",
    "queryMismatchCount", "targetLabelButtonCount", "nextLabelButtonCount", "scopedButtonCount",
    "roleButtonCount", "pagerElementCount", "pagerAnchorCount", "pagerButtonCount",
    "pagerInputCount",
    "pagerNumericTextCount", "pagerTargetTextCount", "pagerTargetAriaCount",
    "pagerHiddenCount", "pagerMarkerCount", "pagerPlaceholderHrefCount",
    "pagerTargetPlaceholderCount", "pagerPageHrefCount", "pagerOtherHrefCount",
})
_REPORT_KEYS = frozenset({
    "schemaVersion", "extensionVersion", "exportedAt", "settings", "verification", "events",
})
_CARD_COUNTS = frozenset({
    "acceptedCount", "rejectedCount", "variantCount", "outOfStockCount", "missingValueCount",
})
_LANES = frozenset({"home", "departments", "keywords", "brands", "priority"})
_CARD_DECISIONS = frozenset({
    "accepted", "out-of-stock", "variant", "card-unavailable", "not-free", "missing-title",
})
_CARD_SAMPLE_KEYS = frozenset({
    "itemId", "decision", "cardFound", "free", "outOfStock", "valueCents",
})
_EVENT_KEYS = _NUMERIC_FIELDS | _CARD_COUNTS | {
    "at", "event", "reason", "mode", "lane", "samples", "failureCode", "operation",
    "stage", "state", "outcome", "requestId", "streamId", "intakePhase", "component",
    "statusCode", "errorStage", "errorReason", "releaseReason", "cleanupConfirmed", "transport",
}
_PAGE_STATE_STAGES = frozenset({
    "empty-observed", "empty-confirmation", "fallback", "unusable", "zero-page-complete",
    "recovered",
})
_PAGE_STATE_LABELS = {
    "readyState": frozenset({"loading", "interactive", "complete", "unknown"}),
    "visibility": frozenset({"visible", "hidden", "prerender", "unknown"}),
    "route": frozenset({"reviewer", "auth", "other"}),
    "navigationType": frozenset({"navigate", "reload", "back_forward", "unknown"}),
}
_PAGE_STATE_FLAGS = frozenset({
    "reviewerShell", "resultsLabel", "noResults", "loading", "error", "signIn", "pagerPresent",
})
_PAGE_STATE_COUNTS = {
    "productLinks": 10000, "offerLines": 10000, "renderedPage": 100000,
    "failedRequests": 10000, "readingFailures": 10000, "readingWorkMs": MAX_NUMBER,
    "queryIndex": 10000, "queryCount": 10000,
}
_PAGE_STATE_KEYS = (frozenset(_PAGE_STATE_LABELS) | _PAGE_STATE_FLAGS
                    | frozenset(_PAGE_STATE_COUNTS) | {"responseStatus"})
_METRIC_COUNTERS = frozenset({
    "initialPages", "successfulTransitions", "challengeEpisodes", "nativeAttempts",
    "urlFallbacks", "nativeBackoffFallbacks", "nativeStalledFallbacks", "nativeRetries",
})
_METRIC_TIMES = frozenset({
    "measurementStartedAt", "completedVerificationDowntimeMs", "verificationDowntimeMs",
    "verificationActiveSince",
})
_METRIC_KEYS = _METRIC_COUNTERS | _METRIC_TIMES | {"episodesPer100Transitions"}
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}")
_ITEM_ID = re.compile(r"ip-[0-9]{6,20}")
_CORRELATION_ID = re.compile(r"[0-9a-f]{32}")
_LOG_COUNTERS = frozenset({
    "lastSequence", "recorded", "rejected", "storageErrors", "ringOverwrites",
    "unacknowledgedOverwrites", "pending", "lastRecordedAt", "lastSyncAt", "syncAttempts",
    "syncFailures", "consecutiveSyncFailures", "lastSyncStatus", "acknowledgedSequence",
})
_LOG_KEYS = _LOG_COUNTERS | {"streamId", "lastSyncError"}
_VERIFICATION_OUTCOMES = frozenset({
    "starting", "unsupported", "needs-attention", "input-error", "cancelled", "verified",
})
_VERIFICATION_LABELS = {
    "statusCode": _VERIFICATION_OUTCOMES | {
        "watching", "permission-required", "retrying-control", "waiting-page", "invalid-budget",
        "attempts-exhausted", "already-attempted", "waiting-tab-ready", "waiting-foreground",
        "cooldown", "unavailable", "native-permission-required", "native-connecting", "connecting",
        "retry-pending", "collector-pending", "native-waiting-idle", "native-unavailable",
    },
    "errorStage": frozenset({"inspect", "move", "press", "release", "detach"}),
    "errorReason": frozenset({"timeout", "detached", "command-failed"}),
    "releaseReason": frozenset({"none", "deadline", "page-changed", "cancelled", "failed",
                                "feedback", "stalled", "max-duration"}),
    "transport": frozenset({"native", "debugger"}),
}


def _correlation_id(value: object) -> str:
    if type(value) is not str or _CORRELATION_ID.fullmatch(value) is None:
        raise ValueError(INVALID_REPORT)
    return value


def _logging_state(value: object) -> dict:
    data = _object(value, _LOG_KEYS)
    clean = {key: _integer(data[key]) for key in _LOG_COUNTERS}
    clean["streamId"] = _correlation_id(data["streamId"])
    clean["lastSyncError"] = _label(data["lastSyncError"], frozenset({
        "none", "timeout", "network", "http", "storage", "report",
    }))
    if (clean["lastSequence"] != clean["recorded"]
            or clean["acknowledgedSequence"] > clean["lastSequence"]
            or clean["unacknowledgedOverwrites"] > clean["ringOverwrites"]
            or clean["ringOverwrites"] > clean["recorded"] or clean["pending"] > 512
            or clean["lastSyncStatus"] != 0 and not 100 <= clean["lastSyncStatus"] <= 599):
        raise ValueError(INVALID_REPORT)
    return clean


def _receipt(value: object) -> dict:
    data = _object(value, frozenset({"streamId", "lastSequence", "gapEvents"}))
    return {"streamId": _correlation_id(data["streamId"]),
            "lastSequence": _integer(data["lastSequence"]),
            "gapEvents": _integer(data["gapEvents"])}


def _number(value: object, minimum: float = 0, maximum: float = MAX_NUMBER) -> int | float:
    # bool is an int subclass; accepting it would weaken every numeric check.
    if type(value) not in (int, float):
        raise ValueError(INVALID_REPORT)
    if not minimum <= value <= maximum or not math.isfinite(value):
        raise ValueError(INVALID_REPORT)
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError(INVALID_REPORT)
    return _number(value)


def _object(value: object, required: frozenset[str], allowed: frozenset[str] | None = None):
    if type(value) is not dict:
        raise ValueError(INVALID_REPORT)
    keys = value.keys()
    if not required <= keys or not keys <= (required if allowed is None else allowed):
        raise ValueError(INVALID_REPORT)
    return value


def _label(value: object, choices: frozenset[str]) -> str:
    if type(value) is not str or value not in choices:
        raise ValueError(INVALID_REPORT)
    return value


def _card_sample(value: object) -> dict:
    data = _object(value, _CARD_SAMPLE_KEYS)
    if type(data["itemId"]) is not str or _ITEM_ID.fullmatch(data["itemId"]) is None:
        raise ValueError(INVALID_REPORT)
    if type(data["cardFound"]) is not bool:
        raise ValueError(INVALID_REPORT)
    for field in ("free", "outOfStock"):
        if data[field] is not None and type(data[field]) is not bool:
            raise ValueError(INVALID_REPORT)
    value_cents = None if data["valueCents"] is None else _integer(data["valueCents"])
    return {"itemId": data["itemId"], "decision": _label(data["decision"], _CARD_DECISIONS),
            "cardFound": data["cardFound"], "free": data["free"],
            "outOfStock": data["outOfStock"], "valueCents": value_cents}


def _page_state(value: object) -> dict:
    data = _object(value, _PAGE_STATE_KEYS)
    clean = {field: _label(data[field], choices) for field, choices in _PAGE_STATE_LABELS.items()}
    for field in _PAGE_STATE_FLAGS:
        if type(data[field]) is not bool:
            raise ValueError(INVALID_REPORT)
        clean[field] = data[field]
    for field, maximum in _PAGE_STATE_COUNTS.items():
        count = _integer(data[field])
        if count > maximum:
            raise ValueError(INVALID_REPORT)
        clean[field] = count
    status = _integer(data["responseStatus"])
    if status != 0 and not 100 <= status <= 599:
        raise ValueError(INVALID_REPORT)
    clean["responseStatus"] = status
    return clean


def _event(value: object) -> dict:
    data = _object(value, frozenset({"at", "event"}), _EVENT_KEYS)
    name = _label(data["event"], _EVENTS)
    clean = {"at": _integer(data["at"]), "event": name}
    for field in ("requestId", "streamId"):
        if field in data:
            clean[field] = _correlation_id(data[field])
    if ("streamId" in data) != ("eventSeq" in data):
        raise ValueError(INVALID_REPORT)
    if "eventSeq" in data and _integer(data["eventSeq"]) < 1:
        raise ValueError(INVALID_REPORT)
    if "intakePhase" in data:
        clean["intakePhase"] = _label(data["intakePhase"], frozenset({"initial", "late", "live"}))
    if name.startswith("focus-"):
        clean["operation"] = _label(data.get("operation"), frozenset({
            "pager", "facet-controls", "page-scroll", "page-navigation",
        }))
    elif "operation" in data:
        raise ValueError(INVALID_REPORT)
    for key in _NUMERIC_FIELDS & data.keys():
        clean[key] = _number(data[key])
    if "responseStatus" in data:
        status = _integer(data["responseStatus"])
        if status != 0 and not 100 <= status <= 599:
            raise ValueError(INVALID_REPORT)
    if name == "ingest-stage":
        clean["stage"] = _label(data.get("stage"), frozenset({"queued", "batch", "complete"}))
        clean["outcome"] = _label(data.get("outcome"), frozenset({
            "waiting", "accepted", "accepted-stale", "rejected", "retry", "timeout", "network",
            "invalid-response", "cancelled",
        }))
    elif name == "auxiliary-target-complete":
        clean["outcome"] = _label(data.get("outcome"), frozenset({
            "items", "empty", "unresolved", "duplicate",
        }))
    elif name == "verification-state":
        clean["outcome"] = _label(data.get("outcome"), _VERIFICATION_OUTCOMES | {"status"})
        for field, choices in _VERIFICATION_LABELS.items():
            if field in data:
                clean[field] = _label(data[field], choices)
        if clean["outcome"] == "status" and "statusCode" not in clean:
            raise ValueError(INVALID_REPORT)
        if "cleanupConfirmed" in data:
            if type(data["cleanupConfirmed"]) is not bool:
                raise ValueError(INVALID_REPORT)
            clean["cleanupConfirmed"] = data["cleanupConfirmed"]
    elif name in {"browser-error", "sync-state"}:
        clean["component"] = _label(data.get("component"), frozenset({
            "message", "heartbeat", "initialization", "refresh", "auxiliary", "diagnostics",
            "settings", "commands", "status", "coordinator", "verification",
        }))
        clean["outcome"] = _label(data.get("outcome"), frozenset({"error", "recovered"}))
    elif name == "navigation-outcome":
        clean["outcome"] = _label(
            data.get("outcome"), frozenset({"ready", "interrupted", "timeout", "http-error"})
        )
    elif "outcome" in data:
        raise ValueError(INVALID_REPORT)
    if "component" in data and name not in {"browser-error", "sync-state"}:
        raise ValueError(INVALID_REPORT)
    if (name != "verification-state"
            and (set(_VERIFICATION_LABELS) | {"cleanupConfirmed"}) & data.keys()):
        raise ValueError(INVALID_REPORT)
    if "reason" in data:
        clean["reason"] = _label(data["reason"], _REASONS)
    if "mode" in data:
        clean["mode"] = _label(data["mode"], _MODES)
    if "lane" in data:
        clean["lane"] = _label(data["lane"], _LANES)
    if "failureCode" in data:
        if name not in {"auxiliary-target-skipped", "auxiliary-target-retry", "auxiliary-error"}:
            raise ValueError(INVALID_REPORT)
        clean["failureCode"] = _label(data["failureCode"], _SCANNER_FAILURE_CODES)
    if name.startswith("auxiliary-") and "lane" not in clean:
        raise ValueError(INVALID_REPORT)
    if name == "page-state":
        _object(data, frozenset({"at", "event", "lane", "stage", "state"}), _EVENT_KEYS)
        clean["stage"] = _label(data["stage"], _PAGE_STATE_STAGES)
        clean["state"] = _page_state(data["state"])
    elif "state" in data or "stage" in data and name != "ingest-stage":
        raise ValueError(INVALID_REPORT)
    if name == "card-extraction":
        _object(data, frozenset({"at", "event", "lane", "samples"}) | _CARD_COUNTS, _EVENT_KEYS)
        samples = data["samples"]
        if type(samples) is not list or len(samples) > MAX_CARD_SAMPLES:
            raise ValueError(INVALID_REPORT)
        clean.update({field: _integer(data[field]) for field in _CARD_COUNTS})
        clean["samples"] = [_card_sample(sample) for sample in samples]
    elif "samples" in data or _CARD_COUNTS & data.keys():
        raise ValueError(INVALID_REPORT)
    return clean


def _validate_metrics(value: object, exported_at: int) -> dict:
    data = _object(value, _METRIC_KEYS)
    clean = {field: _integer(data[field]) for field in _METRIC_COUNTERS | _METRIC_TIMES}
    if any(clean[field] > 1_000_000_000_000 for field in _METRIC_COUNTERS):
        raise ValueError(INVALID_REPORT)
    start = clean["measurementStartedAt"]
    active = clean["verificationActiveSince"]
    completed = clean["completedVerificationDowntimeMs"]
    downtime = clean["verificationDowntimeMs"]
    if (start > exported_at or (active and not start <= active <= exported_at)
            or downtime != completed + (exported_at - active if active else 0)
            or downtime > exported_at - start
            or clean["nativeBackoffFallbacks"] + clean["nativeStalledFallbacks"]
            > clean["urlFallbacks"]):
        raise ValueError(INVALID_REPORT)
    transitions = clean["successfulTransitions"]
    rate = data["episodesPer100Transitions"]
    if transitions == 0:
        if rate is not None:
            raise ValueError(INVALID_REPORT)
        clean["episodesPer100Transitions"] = None
    else:
        rate = _number(rate, 0, 100_000_000_000_000)
        if not math.isclose(rate, clean["challengeEpisodes"] * 100 / transitions,
                            rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError(INVALID_REPORT)
        clean["episodesPer100Transitions"] = rate
    return clean


def _validate_report(value: object) -> dict:
    data = _object(value, _REPORT_KEYS, _REPORT_KEYS | {"metrics", "logging"})
    if type(data["schemaVersion"]) is not int or data["schemaVersion"] != 1:
        raise ValueError(INVALID_REPORT)
    version = data["extensionVersion"]
    if type(version) is not str or len(version) > 64:
        raise ValueError(INVALID_REPORT)
    if version != "unknown" and _VERSION.fullmatch(version) is None:
        raise ValueError(INVALID_REPORT)

    settings = _object(
        data["settings"], frozenset({"refreshMinutes", "pageDelaySeconds", "sweepMode"}),
    )
    verification = _object(
        data["verification"], frozenset({"count", "lastAt", "pausedUntil"}),
    )
    events = data["events"]
    if type(events) is not list or len(events) > MAX_EVENTS:
        raise ValueError(INVALID_REPORT)
    clean_events = [_event(value) for value in events]

    report = {
        "schemaVersion": 1,
        "extensionVersion": version,
        "exportedAt": _integer(data["exportedAt"]),
        "settings": {
            "refreshMinutes": _number(settings["refreshMinutes"], 0, 120),
            # This reports the saved option. Older installs permitted 1–4;
            # runtime pacing enforces its own five-second minimum separately.
            "pageDelaySeconds": _number(settings["pageDelaySeconds"], 1, 60),
            "sweepMode": _label(settings["sweepMode"], _MODES),
        },
        "verification": {
            "count": _integer(verification["count"]),
            "lastAt": _integer(verification["lastAt"]),
            "pausedUntil": _integer(verification["pausedUntil"]),
        },
        "events": clean_events,
    }
    # Metrics are optional in schema 1 so old extensions and retained snapshots
    # remain readable. If supplied, the complete fixed numeric object is strict.
    if "metrics" in data:
        report["metrics"] = _validate_metrics(data["metrics"], report["exportedAt"])
    if "logging" in data:
        report["logging"] = _logging_state(data["logging"])
        if any(event.get("streamId") == report["logging"]["streamId"]
               and event["eventSeq"] > report["logging"]["lastSequence"] for event in clean_events):
            raise ValueError(INVALID_REPORT)
    # Check the whole validated input before retention so expired entries cannot
    # make an oversized incoming report appear acceptable.
    _encode_snapshot({"receivedAt": 0, "report": report})
    return report


def _encode_snapshot(snapshot: dict) -> bytes:
    encoded = json.dumps(snapshot, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError(INVALID_REPORT)
    return encoded


def _prune_report(report: dict, now: int) -> None:
    cutoff = now - RETENTION_MS
    report["events"] = [event for event in report["events"] if cutoff <= event["at"] <= now]


class BrowserDiagnosticsStore:
    """One thread-safe browser snapshot, atomically persisted on each report.

    ``None`` selects the default file. ``""`` and ``":memory:"`` disable disk
    persistence, matching the item store's local test convention.
    """

    def __init__(self, path: Path | str | None = None, *, archive=None):
        if path in ("", ":memory:"):
            self.path = None
        else:
            self.path = Path(path) if path is not None else DEFAULT_PATH
        self._lock = threading.RLock()
        self._snapshot = {"receivedAt": 0, "report": None}
        self._archived_events: dict[str, None] = {}
        self._archive_failures = self._snapshot_failures = 0
        self._last_failure_at = 0
        self._last_failure_code = "none"
        archive_path = self.path.with_name("browser-diagnostic-events.jsonl") if self.path else None
        try:
            self._archive_was_present = archive is not None or (
                archive_path is not None and archive_path.is_file()
                and archive_path.stat().st_size > 0
            )
        except OSError:
            # Without evidence of a populated archive, do not suppress the
            # retained snapshot's events on the first successful mirror.
            self._archive_was_present = False
        self._archive = archive if archive is not None else (
            RollingLog(archive_path,
                       max_bytes=2 * 1024 * 1024, total_bytes=16 * 1024 * 1024)
            if self.path is not None else None
        )
        self._load()

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            with self.path.open("rb") as handle:
                encoded = handle.read(MAX_FILE_BYTES + 1)
            if len(encoded) > MAX_FILE_BYTES:
                raise ValueError(INVALID_REPORT)
            data = _object(json.loads(encoded), frozenset({"receivedAt", "report"}),
                           frozenset({"receivedAt", "report", "receipt"}))
            received_at = _integer(data["receivedAt"])
            report = _validate_report(data["report"])
            _prune_report(report, int(time.time() * 1000))
        except FileNotFoundError:
            return
        except (OSError, ValueError, UnicodeError, RecursionError):
            # Never log the exception: JSON errors and OS paths can include
            # untrusted payload material. Corruption must not stop the server.
            log.warning("Browser diagnostics snapshot unavailable; starting empty")
            return
        self._snapshot = {"receivedAt": received_at, "report": report}
        if "receipt" in data:
            try:
                receipt = _receipt(data["receipt"])
                if (report.get("logging", {}).get("streamId") != receipt["streamId"]
                        or report["logging"]["lastSequence"] < receipt["lastSequence"]):
                    raise ValueError(INVALID_REPORT)
                self._snapshot["receipt"] = receipt
            except ValueError:
                log.warning("Browser diagnostic receipt unavailable; sequence continuity unknown")
        if self._archive_was_present:
            self._remember_archived(self._fingerprint(event) for event in report["events"])

    @staticmethod
    def _fingerprint(event: dict) -> str:
        if "streamId" in event and "eventSeq" in event:
            return f"{event['streamId']}:{event['eventSeq']}"
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _remember_archived(self, fingerprints) -> None:
        for fingerprint in fingerprints:
            self._archived_events[fingerprint] = None
        while len(self._archived_events) > MAX_EVENTS * 2:
            self._archived_events.pop(next(iter(self._archived_events)))

    def _archive_events(self, report: dict) -> None:
        fresh = {}
        for event in report["events"]:
            fingerprint = self._fingerprint(event)
            if fingerprint not in self._archived_events:
                fresh[fingerprint] = event
        if self._archive is not None and fresh:
            encoded = "".join(json.dumps({"entry_point": "browser-diagnostics",
                                         "extensionVersion": report["extensionVersion"], **event},
                                        separators=(",", ":"), allow_nan=False) + "\n"
                              for event in fresh.values()).encode("utf-8")
            self._archive.append(encoded)
        self._remember_archived(fresh)

    def record_report(self, value: object) -> dict | None:
        """Validate and replace the snapshot, propagating any storage failure."""
        report = _validate_report(value)
        with self._lock:
            now = int(time.time() * 1000)
            future_sequences = {event["eventSeq"] for event in report["events"]
                                if "logging" in report and event["at"] > now
                                and event.get("streamId") == report["logging"]["streamId"]}
            _prune_report(report, now)
            snapshot = {"receivedAt": now, "report": report}
            receipt = None
            if "logging" in report:
                state = report["logging"]
                previous = self._snapshot.get("receipt", {})
                same = previous.get("streamId") == state["streamId"]
                last = previous["lastSequence"] if same else 0
                prior_log = (self._snapshot.get("report") or {}).get("logging", {})
                prior_sequence = (prior_log.get("lastSequence", 0)
                                  if prior_log.get("streamId") == state["streamId"] else 0)
                if state["lastSequence"] < max(last, prior_sequence):
                    raise ValueError(INVALID_REPORT)
                # A browser clock can lead the receiver. Those events remain
                # in the browser ring; do not acknowledge a pruned future tail
                # as archived or count it as permanently lost.
                pending = [seq for seq in future_sequences if seq > last]
                accepted_through = (min(state["lastSequence"], min(pending) - 1)
                                    if pending else state["lastSequence"])
                seen = {event["eventSeq"] for event in report["events"]
                        if event.get("streamId") == state["streamId"]
                        and last < event["eventSeq"] <= accepted_through}
                receipt = {"streamId": state["streamId"], "lastSequence": accepted_through,
                           "gapEvents": (previous["gapEvents"] if same else 0)
                           + accepted_through - last - len(seen)}
                snapshot["receipt"] = receipt
            encoded = _encode_snapshot(snapshot)
            # Archive first: a snapshot-replace failure can be retried without
            # losing evidence or logging it again in this process. A crash may
            # repeat a line, which is preferable to acknowledging missing data.
            try:
                self._archive_events(report)
            except Exception:
                self._archive_failures += 1
                self._last_failure_at, self._last_failure_code = now, "archive"
                raise
            if self.path is not None:
                try:
                    self._write(encoded)
                except Exception:
                    self._snapshot_failures += 1
                    self._last_failure_at, self._last_failure_code = now, "snapshot"
                    raise
            self._snapshot = snapshot
            return deepcopy(receipt)

    def health(self) -> dict:
        """Safe recording health; a recent snapshot alone cannot prove lossless logs."""
        with self._lock:
            report = self._snapshot.get("report") or {}
            return {"receivedAt": self._snapshot["receivedAt"],
                    "eventCount": len(report.get("events", [])), "maxEvents": MAX_EVENTS,
                    "maxSnapshotBytes": MAX_FILE_BYTES, "archiveMaxBytes": 2 * 1024 * 1024,
                    "archiveTotalBytes": 16 * 1024 * 1024,
                    "archiveFailures": self._archive_failures,
                    "snapshotFailures": self._snapshot_failures,
                    "lastFailureAt": self._last_failure_at,
                    "lastFailureCode": self._last_failure_code,
                    "receipt": deepcopy(self._snapshot.get("receipt")),
                    "logging": deepcopy(report.get("logging"))}

    def _write(self, encoded: bytes) -> None:
        write_atomic_json(
            self.path, encoded, logger=log,
            cleanup_warning="Browser diagnostics temporary snapshot cleanup unavailable",
        )

    def snapshot(self) -> dict:
        """Return an isolated copy with expired and future events removed."""
        with self._lock:
            snapshot = deepcopy(self._snapshot)
        if snapshot["report"] is not None:
            _prune_report(snapshot["report"], int(time.time() * 1000))
        return snapshot
