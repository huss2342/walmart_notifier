"""Dedupe store so a given item only ever alerts once.

A JSON file on disk. The whole point of this project is that it runs on a
machine that is already on all the time, so there is nothing to gain from a
hosted database -- and a file you can open in a text editor is much easier to
inspect and reset than a cloud table.

Writes go through a temp file and an atomic replace, so killing the process
mid-write leaves the previous good file rather than a truncated one.

Persistence is a snapshot plus an append-only log, because rewriting the whole
file per record does not survive this workload. Saving per item rewrote a
4.6 MB file for each of ~38 items on a page -- ~175 MB for one page -- and the
portal yields 3,000-10,000 new ids a day, so a 60-day window is hundreds of
thousands of records. Whole-file writes would be tens of GB a day.

New records are appended as one JSON line each, so a write costs what is new
rather than what is stored. The snapshot is rewritten only on compaction: when
the log outgrows the live set, and at shutdown. Appends are batched by a
background thread every FLUSH_INTERVAL_SECONDS and forced once per ingest, so
a hard kill loses at most a second of records -- whose only consequence is
that those items may alert once more.

Retention is by age rather than a bare count. A count cap alone silently set
the horizon to whatever the current churn rate implied -- at 20,000 entries
that had become about three days, so an item still listed could be trimmed and
alert again as if new. MAX_ENTRIES remains only as a backstop against
unbounded growth.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).parent.parent / "data" / "seen.json"

# How long an item stays remembered. This is the number that decides whether a
# still-listed item can re-alert, so it is expressed in days rather than left
# to fall out of a row count.
RETENTION_DAYS = 60
# Backstop against unbounded growth only; age is the real policy. At roughly
# 150 bytes per record this is about 40 MB in the worst case.
MAX_ENTRIES = 250_000
# Titles are only ever read by a human inspecting the file; the alert itself
# uses the live item. Full titles ran to 300 characters and dominated the file.
MAX_TITLE_CHARS = 120
FLUSH_INTERVAL_SECONDS = 2.0
# Compact once the log is at least this long and at least as long as the live
# set, bounding both log growth and replay time at startup. At ~5,000 new ids a
# day that is one snapshot rewrite every few days.
MIN_COMPACT_LINES = 20_000


def retention_days() -> int:
    raw = os.environ.get("RETENTION_DAYS", "").strip()
    if not raw:
        return RETENTION_DAYS
    try:
        parsed = int(raw)
    except ValueError:
        log.warning("RETENTION_DAYS=%r is not a number; using %d.", raw, RETENTION_DAYS)
        return RETENTION_DAYS
    return parsed if parsed > 0 else RETENTION_DAYS


def default_path() -> Path:
    return Path(os.environ.get("STATE_PATH", DEFAULT_PATH)).expanduser()


class SeenStore:
    def __init__(self, path: Path | str | None = None, autosave: bool = True):
        # `path=""` (or ":memory:") keeps everything in RAM, which is what the
        # tests use.
        self.path: Path | None
        if path == "" or path == ":memory:":
            self.path = None
        else:
            self.path = Path(path) if path is not None else default_path()

        self.autosave = autosave
        self._lock = threading.RLock()
        self._appends: list[dict] = []
        self._log_lines = 0
        self._flush_wake = threading.Event()
        self._closed = threading.Event()
        self._flusher: threading.Thread | None = None
        self._seen: dict[str, dict] = {}
        # Claims are deliberately process-local and are not written to disk.
        # Persisting a claim before the notification succeeds turns a power
        # loss (or a killed request thread) into a permanently missed alert.
        # On restart we prefer a possible duplicate notification to silence.
        self._pending: dict[str, dict] = {}
        self._markers: dict[str, str] = {}
        self._load()
        self._prune_expired()
        if self.autosave and self.path is not None:
            self._flusher = threading.Thread(
                target=self._flush_loop, name="seen-store-flush", daemon=True
            )
            self._flusher.start()

    # --- persistence --------------------------------------------------------

    def _load(self) -> None:
        if self.path is None:
            return
        # The log is replayed even with no snapshot: until the first
        # compaction, it is the only copy of everything recorded so far.
        self._read_snapshot()
        replayed = self._replay_log()
        log.info(
            "Loaded %d seen items from %s (%d replayed from the log).",
            len(self._seen), self.path, replayed,
        )

    def _read_snapshot(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read %s; starting with an empty store.", self.path)
            return
        if not isinstance(data, dict):
            log.error("%s is not a JSON object; starting with an empty store.", self.path)
            return
        seen = data.get("seen")
        markers = data.get("markers")
        self._seen = seen if isinstance(seen, dict) else {}
        self._markers = markers if isinstance(markers, dict) else {}

    @property
    def log_path(self) -> Path | None:
        """Append-only companion to the snapshot."""
        return None if self.path is None else self.path.with_suffix(".log")

    def _replay_log(self) -> int:
        """Apply records appended since the last snapshot."""
        path = self.log_path
        if path is None or not path.is_file():
            return 0
        replayed = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        # A torn final line is the expected cost of appending
                        # without fsync. Everything before it is still good.
                        log.warning("Ignoring an unreadable line in %s.", path)
                        continue
                    key, value = entry.get("k"), entry.get("v")
                    if not isinstance(key, str):
                        continue
                    if entry.get("t") == "marker" and isinstance(value, str):
                        self._markers[key] = value
                    elif entry.get("t") == "item" and isinstance(value, dict):
                        self._seen[key] = value
                    replayed += 1
            self._log_lines = replayed
        except OSError:
            log.exception("Could not read %s; continuing from the snapshot.", path)
        return replayed

    def save(self) -> None:
        """Compact: write the snapshot and drop the log it supersedes."""
        if self.path is None:
            return
        with self._lock:
            self._appends.clear()
            self._prune_expired()
            self._trim()
            payload = json.dumps(
                {"seen": self._seen, "markers": self._markers},
                indent=1, sort_keys=True,
            )
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # Atomic replace: a crash mid-write must not destroy the file
                # that stops the next run re-alerting on everything.
                tmp = self.path.with_suffix(self.path.suffix + ".tmp")
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(self.path)
                # Only now is the log redundant. Removing it first would lose
                # every appended record if the snapshot write then failed.
                log_path = self.log_path
                if log_path is not None and log_path.exists():
                    log_path.unlink()
                self._log_lines = 0
            except OSError:
                log.exception("Could not write %s; dedupe will not survive a restart.", self.path)

    def _prune_expired(self) -> int:
        """Drop records older than the retention window. Returns how many."""
        days = retention_days()
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        with self._lock:
            stale = [
                item_id for item_id, record in self._seen.items()
                # A record with no timestamp predates value/age tracking. Treat
                # it as expired rather than immortal.
                if not isinstance(record, dict) or record.get("seen_at", "") < cutoff
            ]
            for item_id in stale:
                del self._seen[item_id]
        if stale:
            log.info("Dropped %d record(s) older than %d days.", len(stale), days)
        return len(stale)

    def _trim(self) -> None:
        if len(self._seen) <= MAX_ENTRIES:
            return
        log.warning(
            "Seen store hit the %d-record backstop; dropping the oldest. "
            "Lower RETENTION_DAYS if this recurs.", MAX_ENTRIES
        )
        # Oldest first by seen_at; entries without one are treated as oldest.
        ordered = sorted(
            self._seen.items(),
            key=lambda kv: kv[1].get("seen_at", "") if isinstance(kv[1], dict) else "",
        )
        for item_id, _ in ordered[: len(self._seen) - MAX_ENTRIES]:
            del self._seen[item_id]

    def _queue(self, kind: str, key: str, value: object) -> None:
        """Queue one record for the append log."""
        if self.path is None:
            return
        self._appends.append({"t": kind, "k": key, "v": value})
        if self.autosave:
            self._flush_wake.set()

    def flush(self) -> int:
        """Append queued records. Returns how many were written.

        This is the hot path: its cost is the number of new records, not the
        size of the store.
        """
        if self.path is None:
            return 0
        with self._lock:
            pending, self._appends = self._appends, []
            if not pending:
                return 0
            lines = "".join(
                json.dumps(entry, separators=(",", ":")) + "\n" for entry in pending
            )
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                log_path = self.log_path
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(lines)
                self._log_lines += len(pending)
            except OSError:
                # Put them back so the next flush retries rather than losing
                # them; a lost record means a duplicate alert later.
                self._appends[:0] = pending
                log.exception("Could not append to %s.", self.log_path)
                return 0

            # Fold the log in once it is as long as the live set. Comparing
            # against twice the live set never fires here: every line is a new
            # item, so the log and the store grow together.
            if (self._log_lines >= MIN_COMPACT_LINES and
                    self._log_lines >= len(self._seen)):
                log.info("Compacting the seen store (%d log lines).", self._log_lines)
                self.save()
            return len(pending)

    def _flush_loop(self) -> None:
        while not self._closed.is_set():
            self._flush_wake.wait(FLUSH_INTERVAL_SECONDS)
            self._flush_wake.clear()
            if self._closed.is_set():
                return
            try:
                self.flush()
            except Exception:
                # A dead flusher would silently stop persisting dedupe state,
                # so every restart would re-alert the catalogue.
                log.exception("Scheduled flush failed; will retry.")

    def close(self) -> None:
        """Stop the flusher and write anything still pending."""
        self._closed.set()
        self._flush_wake.set()
        flusher, self._flusher = self._flusher, None
        if flusher is not None:
            flusher.join(timeout=5)
        self.flush()
        # Compact on the way out so the next start reads one clean snapshot.
        self.save()

    # --- dedupe -------------------------------------------------------------

    def is_new(self, item_id: str) -> bool:
        """Whether an item has been durably recorded as handled.

        An in-flight claim remains "new" here so a concurrent request reaches
        ``claim()`` and can report a retryable pending result instead of
        acknowledging the page as an ordinary, completed duplicate.
        """
        with self._lock:
            return item_id not in self._seen

    def is_pending(self, item_id: str) -> bool:
        """Whether another request currently owns this item's delivery."""
        with self._lock:
            return item_id in self._pending

    def claim(self, item_id: str, title: str = "", value: float | None = None) -> bool:
        """Atomically take ownership of an item. True only for the first caller.

        Two requests can relay the same item at the same moment, so the check
        and reservation have to happen under one lock. The reservation stays
        in memory until ``commit()`` records a successful delivery.
        """
        with self._lock:
            if item_id in self._seen or item_id in self._pending:
                return False
            self._pending[item_id] = self._record_payload(title, value)
            return True

    def commit(self, item_id: str) -> bool:
        """Persist a successfully delivered claim.

        Returns false only when the caller no longer owns an in-flight claim.
        """
        with self._lock:
            record = self._pending.pop(item_id, None)
            if record is None:
                return False
            self._seen[item_id] = record
            self._queue("item", item_id, record)
            return True

    def release(self, item_id: str) -> None:
        """Undo a claim so a later run retries. Used when delivery fails."""
        with self._lock:
            self._pending.pop(item_id, None)

    def mark_seen(self, item_id: str, title: str = "", value: float | None = None) -> bool:
        """Record a filtered/seeded item unless its delivery is in flight."""
        with self._lock:
            if item_id in self._pending:
                return False
            self._record(item_id, title, value)
            self._queue("item", item_id, self._seen[item_id])
            return True

    def _record(self, item_id: str, title: str, value: float | None = None) -> None:
        # The value is recorded purely so "has a $50 item ever actually been
        # relayed?" is answerable. Without it, an item that never arrives and
        # an item that arrived and was filtered look identical after the fact.
        self._seen[item_id] = self._record_payload(title, value)

    @staticmethod
    def _record_payload(title: str, value: float | None = None) -> dict:
        return {
            "title": title[:MAX_TITLE_CHARS],
            "value_usd": value,
            "seen_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    # --- markers ------------------------------------------------------------

    def get_marker(self, key: str) -> str | None:
        with self._lock:
            return self._markers.get(key)

    def set_marker(self, key: str, value: str) -> None:
        with self._lock:
            self._markers[key] = value
            self._queue("marker", key, value)

    # --- introspection ------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)

    def observed_value_stats(self) -> dict[str, int | float | None]:
        """Return aggregate value diagnostics without exposing item details."""
        with self._lock:
            values: list[float] = []
            unknown = 0
            for record in self._seen.values():
                value = record.get("value_usd") if isinstance(record, dict) else None
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    unknown += 1
                    continue
                try:
                    numeric = float(value)
                except (TypeError, ValueError, OverflowError):
                    unknown += 1
                    continue
                if not math.isfinite(numeric):
                    unknown += 1
                    continue
                values.append(numeric)

            return {
                "value_known": len(values),
                "value_unknown": unknown,
                "min_value_usd": min(values) if values else None,
                "max_value_usd": max(values) if values else None,
            }
