"""Page snapshot log: what every reviewer list read showed, for the freshness study.

Extension 2.32.7 (Nov 19, ~2 days of measurement) sends, for every direct read
and every tab page delivered, the lane, page, sort, min_price, catId, the
32-bit hash of the search (never its text), the outcome and each product's id,
position, free price in cents and stock; plus the freshness probes' start, skip
and end records (extension/fast-reads.js). Each record is checked field by
field against fixed allowlists: anything else drops that record (counted), never
the batch. Accepted records go to data/page-snapshots.jsonl through the shared
rolling writer (16 MiB segments, gzip archives, 200 MiB in all, background
thread so no request waits on the bind mount). data/freshness-20261119/analyze.py
reads it.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from discord_bridge.rolling_logs import RollingLog

FILE_NAME = "page-snapshots.jsonl"
SEGMENT_BYTES = 16 * 1024 * 1024
TOTAL_BYTES = 200 * 1024 * 1024
RETENTION_DAYS = 30
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 500
MAX_ROWS = 200
MAX_CHECKS = 80
MAX_INT = 10**15

LANES = frozenset({
    "home", "departments", "keywords", "brands", "priority", "requests", "newest", "value",
    "value80", "new80",
})
SHAPES = frozenset({"value", "value80", "newest", "new80", "department"})
SOURCES = frozenset({"fast", "tab"})
OUTCOMES = frozenset({"items", "blocked", "error", "unavailable"})
STOCK = frozenset({"i", "o", "x", "n"})
PHASES = frozenset({"initial", "late", "live"})
PROBE_EVENTS = frozenset({"start", "skipped", "end"})
SKIP_REASONS = frozenset({"active", "hourly", "daily"})
END_OUTCOMES = frozenset({"appeared", "timeout", "blocked", "safe-period", "paused"})

_ITEM_ID = re.compile(r"ip-[0-9]{6,20}")
_US_ITEM_ID = re.compile(r"[0-9]{6,20}")
_CAT_ID = re.compile(r"[0-9]{1,16}(?:_[0-9]{1,16})*")
_SORT = re.compile(r"[a-z_]{1,24}")  # A tab may carry any of Walmart's sort names.
_CATEGORY = re.compile(r"[0-9:_]{1,200}")
_PROBE_ID = re.compile(r"[0-9a-f]{8,32}")
_DATE_KEY = re.compile(r"[A-Za-z]{1,40}")
_DATE_VALUE = re.compile(r"[0-9A-Za-z:.+ -]{1,40}")


class Invalid(ValueError):
    """One record outside the allowlist."""


def _int(value: object, minimum: int = 0, maximum: int = MAX_INT) -> int:
    # bool is an int subclass; accepting it would weaken every numeric check.
    if type(value) is not int or not minimum <= value <= maximum:
        raise Invalid
    return value


def _label(value: object, choices: frozenset[str]) -> str:
    if type(value) is not str or value not in choices:
        raise Invalid
    return value


def _match(value: object, pattern: re.Pattern) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise Invalid
    return value


def _true(value: object) -> bool:
    if value is not True:
        raise Invalid
    return True


def _keys(data: object, required: frozenset[str], allowed: frozenset[str]) -> dict:
    if type(data) is not dict or not required <= data.keys() or not data.keys() <= allowed:
        raise Invalid
    return data


def _row(value: object) -> list:
    """[usItemId, position, cents|None, stock, extra?] for one product on a page."""
    if type(value) is not list or not 4 <= len(value) <= 5:
        raise Invalid
    row = [_match(value[0], _US_ITEM_ID), _int(value[1], 0, 10_000),
           None if value[2] is None else _int(value[2], 0, 10**9), _label(value[3], STOCK)]
    if len(value) == 5:
        extra = _keys(value[4], frozenset(), frozenset({"c", "d"}))
        clean = {}
        if "c" in extra:
            clean["c"] = _match(extra["c"], _CATEGORY)
        if "d" in extra:
            dates = extra["d"]
            if type(dates) is not dict or not 1 <= len(dates) <= 4:
                raise Invalid
            clean["d"] = {
                _match(key, _DATE_KEY):
                    _match(item, _DATE_VALUE) if type(item) is str else _int(item)
                for key, item in dates.items()
            }
        if clean:
            row.append(clean)
    return row


_READ_FIELDS = frozenset({
    "kind", "source", "at", "durationMs", "lane", "page", "outcome", "sort", "minPrice", "catId",
    "searchHash", "hot", "probe", "coordinator", "targetIndex", "status", "totalPages", "products",
    "ageSeconds", "intakePhase", "items",
})


def _read(data: dict) -> dict:
    required = frozenset({"kind", "source", "at", "lane", "page", "outcome", "items"})
    _keys(data, required, _READ_FIELDS)
    clean = {
        "kind": "read", "source": _label(data["source"], SOURCES), "at": _int(data["at"]),
        "lane": _label(data["lane"], LANES), "page": _int(data["page"], 0, 1_000),
        "outcome": _label(data["outcome"], OUTCOMES),
    }
    for field, maximum in (("durationMs", 10**9), ("minPrice", 10**6), ("targetIndex", 10**5),
                           ("totalPages", 10**5), ("products", 10**5), ("ageSeconds", 10**8),
                           ("searchHash", 0xFFFFFFFF)):
        if field in data:
            clean[field] = _int(data[field], 0, maximum)
    if "status" in data:
        status = _int(data["status"], 0, 599)
        if status and status < 100:
            raise Invalid
        clean["status"] = status
    if "sort" in data:
        clean["sort"] = _match(data["sort"], _SORT)
    if "catId" in data:
        clean["catId"] = _match(data["catId"], _CAT_ID)
    if "probe" in data:
        clean["probe"] = _match(data["probe"], _PROBE_ID)
    if "intakePhase" in data:
        clean["intakePhase"] = _label(data["intakePhase"], PHASES)
    for flag in ("hot", "coordinator"):
        if flag in data:
            clean[flag] = _true(data[flag])
    items = data["items"]
    if type(items) is not list or len(items) > MAX_ROWS:
        raise Invalid
    clean["items"] = [_row(row) for row in items]
    return clean


def _trigger(value: object) -> dict:
    data = _keys(value, frozenset({"lane", "page"}),
                 frozenset({"lane", "page", "searchHash", "hot", "position"}))
    clean = {"lane": _label(data["lane"], frozenset({"requests", "priority"})),
             "page": _int(data["page"], 1, 1_000)}
    if "searchHash" in data:
        clean["searchHash"] = _int(data["searchHash"], 0, 0xFFFFFFFF)
    if "hot" in data:
        clean["hot"] = _true(data["hot"])
    if "position" in data:
        clean["position"] = _int(data["position"], 0, 10_000)
    return clean


def _sighting(value: object) -> dict:
    data = _keys(value, frozenset({"at", "page"}), frozenset({"at", "page", "position", "extra"}))
    clean = {"at": _int(data["at"]), "page": _int(data["page"], 0, 1_000)}
    if "position" in data:
        clean["position"] = _int(data["position"], 0, 10_000)
    if "extra" in data:
        clean["extra"] = _true(data["extra"])
    return clean


def _check(value: object) -> dict:
    data = _keys(value, frozenset({"shape", "page", "at", "found"}),
                 frozenset({"shape", "page", "at", "found", "position", "extra", "catId"}))
    if type(data["found"]) is not bool:
        raise Invalid
    clean = {"shape": _label(data["shape"], SHAPES), "page": _int(data["page"], 0, 1_000),
             "at": _int(data["at"]), "found": data["found"]}
    if "position" in data:
        clean["position"] = _int(data["position"], 0, 10_000)
    if "extra" in data:
        clean["extra"] = _true(data["extra"])
    if "catId" in data:
        clean["catId"] = _match(data["catId"], _CAT_ID)
    return clean


_PROBE_FIELDS = frozenset({
    "kind", "event", "probe", "itemId", "valueCents", "at", "startedAt", "trigger", "reason",
    "predictedPage", "pages", "plannedReads", "category", "department", "outcome", "lagMs",
    "extraReads", "droppedReads", "first", "checks", "moreChecks",
})


def _pages(value: object) -> list[int]:
    if type(value) is not list or not value or len(value) > 50:
        raise Invalid
    return [_int(page, 1, 1_000) for page in value]


def _probe(data: dict) -> dict:
    _keys(data, frozenset({"kind", "event", "itemId", "valueCents", "at"}), _PROBE_FIELDS)
    event = _label(data["event"], PROBE_EVENTS)
    clean = {"kind": "probe", "event": event, "itemId": _match(data["itemId"], _ITEM_ID),
             "valueCents": _int(data["valueCents"], 0, 10**9), "at": _int(data["at"])}
    allowed = {
        "skipped": frozenset({"reason", "trigger"}),
        "start": frozenset({"probe", "startedAt", "trigger", "predictedPage", "pages",
                            "plannedReads", "category", "department"}),
        "end": frozenset({"probe", "startedAt", "outcome", "lagMs", "extraReads", "droppedReads",
                          "pages", "first", "checks", "moreChecks"}),
    }[event]
    extra = data.keys() - {"kind", "event", "itemId", "valueCents", "at"}
    if not extra <= allowed:
        raise Invalid
    if event == "skipped":
        clean["reason"] = _label(data.get("reason"), SKIP_REASONS)
        clean["trigger"] = _trigger(data.get("trigger"))
        return clean
    clean["probe"] = _match(data.get("probe"), _PROBE_ID)
    clean["startedAt"] = _int(data.get("startedAt"))
    clean["pages"] = _pages(data.get("pages"))
    if event == "start":
        clean["trigger"] = _trigger(data.get("trigger"))
        clean["predictedPage"] = _int(data.get("predictedPage"), 1, 1_000)
        clean["plannedReads"] = _int(data.get("plannedReads"), 0, 100)
        if "category" in data:
            clean["category"] = _match(data["category"], _CATEGORY)
        if "department" in data:
            clean["department"] = _match(data["department"], _CAT_ID)
        return clean
    clean["outcome"] = _label(data.get("outcome"), END_OUTCOMES)
    for field, maximum in (("lagMs", 10**8), ("extraReads", 10_000), ("droppedReads", 10_000),
                           ("moreChecks", 10**6)):
        if field in data:
            clean[field] = _int(data[field], 0, maximum)
    first = data.get("first")
    if type(first) is not dict or not first.keys() <= SHAPES:
        raise Invalid
    clean["first"] = {shape: _sighting(value) for shape, value in first.items()}
    checks = data.get("checks")
    if type(checks) is not list or len(checks) > MAX_CHECKS:
        raise Invalid
    clean["checks"] = [_check(check) for check in checks]
    return clean


def validate_record(value: object) -> dict:
    """One clean record, or Invalid (a ValueError) when anything is outside the allowlist."""
    if type(value) is not dict:
        raise Invalid
    kind = value.get("kind")
    if kind == "read":
        return _read(value)
    if kind == "probe":
        return _probe(value)
    raise Invalid


def parse_batch(body: bytes) -> tuple[list[dict], int, int]:
    """(clean records, records rejected, records the extension dropped) or ValueError."""
    payload = json.loads(body.decode("utf-8"))
    if (type(payload) is not dict or payload.get("schemaVersion") != 1
            or not payload.keys() <= {"schemaVersion", "records", "dropped"}):
        raise ValueError("invalid page snapshot batch")
    records = payload.get("records")
    if type(records) is not list or len(records) > MAX_RECORDS:
        raise ValueError("invalid page snapshot batch")
    dropped = payload.get("dropped", 0)
    if type(dropped) is not int or not 0 <= dropped <= MAX_INT:
        raise ValueError("invalid page snapshot batch")
    clean, rejected = [], 0
    for record in records:
        try:
            clean.append(validate_record(record))
        except (Invalid, TypeError, AttributeError):
            rejected += 1
    return clean, rejected, dropped


class PageSnapshotLog:
    """Thread-safe writer of validated records; failures are counted, never raised to callers."""

    def __init__(self, directory: Path | str | None, *, clock=None, segment_bytes=SEGMENT_BYTES,
                 total_bytes=TOTAL_BYTES, background=True):
        self.clock = clock or time.time
        self._lock = threading.Lock()
        self._accepted = self._rejected = self._dropped = self._failed = 0
        self._last_at = None
        self._writer = None
        if directory is not None:
            try:
                self._writer = RollingLog(
                    Path(directory) / FILE_NAME, max_bytes=segment_bytes, total_bytes=total_bytes,
                    retention_days=RETENTION_DAYS, background=background,
                    on_write=self._written,
                )
            except OSError:
                self._writer = None

    def _written(self, error, lines):
        if error is not None:
            with self._lock:
                self._failed += lines

    def record(self, records: list[dict], rejected: int = 0, dropped: int = 0) -> bool:
        """Append one batch; a gap line notes records lost before they reached here."""
        now = self.clock()
        received = int(now * 1000)
        lines = []
        if dropped or rejected:
            lines.append({"kind": "gap", "receivedAt": received, "dropped": dropped,
                          "rejected": rejected})
        lines.extend({"receivedAt": received, **record} for record in records)
        with self._lock:
            self._rejected += rejected
            self._dropped += dropped
            self._last_at = now
        if not lines:
            return True
        if self._writer is None:
            with self._lock:
                self._failed += len(records)
            return False
        encoded = "".join(
            json.dumps(line, separators=(",", ":"), allow_nan=False) + "\n" for line in lines
        ).encode("utf-8")
        try:
            # One batch is at most MAX_BODY_BYTES (4 MiB), well under a segment.
            self._writer.append(encoded)
        except (OSError, ValueError):
            with self._lock:
                self._failed += len(records)
            return False
        with self._lock:
            self._accepted += len(records)
        return True

    def flush(self, timeout: float = 5.0) -> bool:
        return self._writer.flush(timeout) if self._writer is not None else True

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self._writer is not None, "file": FILE_NAME,
                "accepted": self._accepted, "rejected": self._rejected,
                "dropped_by_extension": self._dropped, "write_failures": self._failed,
                "last_batch_at": self._last_at,
                "total_max_bytes": TOTAL_BYTES if self._writer is not None else 0,
            }
