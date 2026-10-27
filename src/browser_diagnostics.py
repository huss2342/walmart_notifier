"""Bounded, allowlisted browser health telemetry, independent of item state.

The HTTP boundary supplies untrusted JSON. Only documented numeric metadata and
fixed labels may be persisted; product data, URLs, tokens and free text have no
place in this schema. A report is committed only after its atomic disk replace
succeeds, so a failed write cannot acknowledge a snapshot that will be lost.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from copy import deepcopy
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "browser-diagnostics.json"
MAX_FILE_BYTES = 128 * 1024
MAX_EVENTS = 300
RETENTION_MS = 7 * 86_400_000
MAX_NUMBER = 1_000_000_000_000_000
INVALID_REPORT = "Invalid browser diagnostics report"

_EVENTS = frozenset({
    "page-loaded", "page-delivered", "navigation-native", "navigation-fallback",
    "navigation-interrupted", "pacing", "challenge", "verification-resumed",
    "sweep-start", "sweep-complete", "ingest-retry", "empty-retry", "recovery-reload",
    "live-update", "navigation-ready", "pager-inspection", "tab-opened",
    "scan-restart-native", "scan-restart-load", "empty-boundary-reused",
    "results-page-ready", "navigation-retry",
})
_REASONS = frozenset({
    "redirect", "dom-marker", "text", "probe", "missing-control", "native-stalled",
    "native-backoff", "unusable", "unreachable", "scheduled", "manual", "installed", "failover",
    "empty-results", "blank-timeout",
})
_MODES = frozenset({"none", "sweep", "high-value"})
_NUMERIC_FIELDS = frozenset({
    "tabId", "generation", "documentId", "page", "targetPage", "totalPages", "delayMs",
    "count", "pages",
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
_EVENT_KEYS = _NUMERIC_FIELDS | {"at", "event", "reason", "mode"}
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
    data = _object(value, _REPORT_KEYS, _REPORT_KEYS | {"metrics"})
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
    clean_events = []
    for value in events:
        event = _object(value, frozenset({"at", "event"}), _EVENT_KEYS)
        clean = {"at": _integer(event["at"]), "event": _label(event["event"], _EVENTS)}
        for key in _NUMERIC_FIELDS & event.keys():
            clean[key] = _number(event[key])
        if "reason" in event:
            clean["reason"] = _label(event["reason"], _REASONS)
        if "mode" in event:
            clean["mode"] = _label(event["mode"], _MODES)
        clean_events.append(clean)

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

    def __init__(self, path: Path | str | None = None):
        if path in ("", ":memory:"):
            self.path = None
        else:
            self.path = Path(path) if path is not None else DEFAULT_PATH
        self._lock = threading.RLock()
        self._snapshot = {"receivedAt": 0, "report": None}
        self._load()

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            with self.path.open("rb") as handle:
                encoded = handle.read(MAX_FILE_BYTES + 1)
            if len(encoded) > MAX_FILE_BYTES:
                raise ValueError(INVALID_REPORT)
            data = _object(json.loads(encoded), frozenset({"receivedAt", "report"}))
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

    def record_report(self, value: object) -> None:
        """Validate and replace the snapshot, propagating any storage failure."""
        report = _validate_report(value)
        with self._lock:
            now = int(time.time() * 1000)
            _prune_report(report, now)
            snapshot = {"receivedAt": now, "report": report}
            encoded = _encode_snapshot(snapshot)
            if self.path is not None:
                self._write(encoded)
            self._snapshot = snapshot

    def _write(self, encoded: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A unique sibling avoids collisions between process-local writes and
        # ensures replace happens on the same filesystem.
        descriptor, name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp",
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                log.warning("Browser diagnostics temporary snapshot cleanup unavailable")

    def snapshot(self) -> dict:
        """Return an isolated copy with expired and future events removed."""
        with self._lock:
            snapshot = deepcopy(self._snapshot)
        if snapshot["report"] is not None:
            _prune_report(snapshot["report"], int(time.time() * 1000))
        return snapshot
