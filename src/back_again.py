"""Back-again alerts: $80+ offers that vanish from every list and later return.

The extension's restock watch only sees offers that Walmart lists as out of
stock. Many $80+ offers instead disappear from the lists altogether and come
back later (SIIL ostomy belts every day at 18:00 CST; a batch of instrument
cases after 5+ days). The backend received each return as an ordinary
duplicate, so none was announced: Nov 16-19, 0 of 21 comebacks after 5+ days
and 0 of 5 vanish-returns alerted.

Rule (owner-approved Nov 19):
  * An in-stock $80+ observation of an offer the queue already knows, unseen
    by every lane for AWAY_SECONDS of covered time, is held: it is not
    recorded, so the queue's own "last seen" clock stays where it was.
  * A second in-stock sighting CONFIRM_SECONDS or more later (any lane) within
    PENDING_SECONDS confirms it: the observation is recorded with the restock
    flag, so the existing "Back in stock" routing, rendering, bundling and the
    queue's own restock rules apply unchanged. No second sighting: dropped.
  * At most once per COOLDOWN_SECONDS per offer. A restock the extension
    announced (its OOS -> in-stock path) also starts the cooldown, and an
    offer the extension flags is never held here, so a return alerts once.
  * Covered time: a gap of OUTAGE_SECONDS or more between observations holding
    any in-stock $80+ offer (scanner, browser or backend down) is not absence;
    otherwise every offer would look "back" after an outage.

The backend only ever receives in-stock observations (the extension drops out
of stock cards), so "unseen" also covers offers listed out of stock for an
hour whose extension watch was lost. Pending holds, cooldowns, outages and the
coverage heartbeat are persisted next to the queue database, so a restart
loses neither a pending confirmation nor a cooldown.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from atomic_json import write_atomic_json

log = logging.getLogger(__name__)

AWAY_SECONDS = 3600
# Under $80 (owner, Nov 19): a comeback re-posts only after a full day unseen,
# on its first sighting. No hold: those offers are re-read only every few hours
# (department rounds), so a hold would expire unconfirmed and the offer would
# never be recorded again. Capped per hour so a slow lane can't flood channels.
LOW_AWAY_SECONDS = 24 * 3600
LOW_MAX_PER_HOUR = 10
CONFIRM_SECONDS = 9
PENDING_SECONDS = 600
COOLDOWN_SECONDS = 6 * 3600
OUTAGE_SECONDS = 600
MIN_CENTS = 8000
# Outages only matter for offers seen just before and after one; a week is ample.
OUTAGE_KEEP_SECONDS = 8 * 86400
MAX_OUTAGES = 1000
MAX_PENDING = 2000
HEARTBEAT_SAVE_SECONDS = 60
SCHEMA = 1


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class BackAgain:
    def __init__(self, path: Path | str, queue_path: Path | str, *, clock=time.time):
        self.path = Path(path)
        self.queue_path = Path(queue_path)
        self.clock = clock
        # Held across plan -> queue record -> settle so concurrent intakes of the
        # same offer cannot both hold or both confirm it.
        self.lock = threading.RLock()
        self.pending: dict[str, float] = {}
        self.cooldown: dict[str, float] = {}
        self.outages: list[list[float]] = []
        self.heartbeat: float | None = None
        self._saved_heartbeat: float | None = None
        self.last_error: str | None = None  # Last failure (load, save or lookup), for /health.
        self._low_recent: list[float] = []  # sub-$80 comeback confirmations, last hour
        self._load()

    # --- persistence -------------------------------------------------------

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            # A corrupt file must never stop intake; start fresh and say so.
            self.last_error = type(exc).__name__
            log.warning("Back-again state unreadable (%s); starting empty.", type(exc).__name__)
            return
        if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
            return
        now = self.clock()
        pending, cooldown = raw.get("pending"), raw.get("cooldown")
        if isinstance(pending, dict):
            self.pending = {k: float(v) for k, v in pending.items()
                            if isinstance(k, str) and _finite(v) and now - v < PENDING_SECONDS}
        if isinstance(cooldown, dict):
            self.cooldown = {k: float(v) for k, v in cooldown.items()
                             if isinstance(k, str) and _finite(v) and now - v < COOLDOWN_SECONDS}
        outages = raw.get("outages")
        if isinstance(outages, list):
            self.outages = [[float(a), float(b)] for a, b in (
                pair for pair in outages if isinstance(pair, list) and len(pair) == 2
                and all(_finite(x) for x in pair)) if a <= b and now - b < OUTAGE_KEEP_SECONDS]
        if _finite(raw.get("heartbeat")):
            self.heartbeat = self._saved_heartbeat = float(raw["heartbeat"])

    def _save(self) -> None:
        now = self.clock()
        self.cooldown = {k: v for k, v in self.cooldown.items() if now - v < COOLDOWN_SECONDS}
        self.outages = [pair for pair in self.outages if now - pair[1] < OUTAGE_KEEP_SECONDS]
        self.outages = self.outages[-MAX_OUTAGES:]
        encoded = json.dumps({
            "schema": SCHEMA, "pending": self.pending, "cooldown": self.cooldown,
            "outages": self.outages, "heartbeat": self.heartbeat,
        }, separators=(",", ":"), allow_nan=False)
        try:
            write_atomic_json(self.path, encoded, logger=log)
            self._saved_heartbeat = self.heartbeat
        except OSError as exc:
            # Losing this file only risks a missed or repeated comeback alert;
            # it must never refuse the intake that triggered it.
            self.last_error = type(exc).__name__
            log.warning("Could not save back-again state (%s).", type(exc).__name__)

    def flush(self) -> None:
        with self.lock:
            if self.heartbeat != self._saved_heartbeat:
                self._save()

    # --- rule --------------------------------------------------------------

    def _covered(self, since: float, now: float) -> float:
        """Seconds in [since, now] the $80+ lists were being observed."""
        lost = 0.0
        for start, end in self.outages:
            lost += max(0.0, min(end, now) - max(start, since))
        return max(0.0, now - since - lost)

    def _last_seen(self, ids: list[str]) -> dict[str, float]:
        """The queue's own last in-stock sighting of each known offer (read only)."""
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        try:
            # mode=rw: never create a database that is not there.
            uri = self.queue_path.resolve().as_uri() + "?mode=rw"
            with closing(sqlite3.connect(uri, uri=True, timeout=10)) as db:
                return {row[0]: row[1] for row in db.execute(
                    f"SELECT item_id,last_seen FROM items WHERE item_id IN ({marks})", ids)
                    if _finite(row[1])}
        except sqlite3.Error as exc:
            # The rule is an extra; intake itself must go on (the queue reports
            # its own errors when it records this observation).
            self.last_error = type(exc).__name__
            log.warning("Back-again lookup failed (%s); no comeback check this intake.",
                        type(exc).__name__)
            return {}

    def plan(self, rows: list[dict], flagged: set[str], *, now: float | None = None):
        """Decide one intake. Call with `lock` held until `settle`.

        rows: normalized public observations. flagged: ids the extension marked
        restocked. Returns (held, confirmed, events): ids to leave out of this
        intake, ids to record with the restock flag, and (stage, item_id,
        seconds) receipts for the operations log.
        """
        now = self.clock() if now is None else now
        events: list[tuple[str, str, float]] = []
        changed = False
        for item_id, first in list(self.pending.items()):
            if now - first > PENDING_SECONDS:
                del self.pending[item_id]
                events.append(("expired", item_id, now - first))
                changed = True
        candidates = [row for row in rows if row.get("available") is True
                      and (row.get("value_cents") or 0) >= MIN_CENTS]
        low = [row for row in rows if row.get("available") is True
               and (row.get("value_cents") or 0) < MIN_CENTS]
        if candidates:
            if self.heartbeat is not None and now - self.heartbeat >= OUTAGE_SECONDS:
                self.outages.append([self.heartbeat, now])
                events.append(("outage", "", now - self.heartbeat))
                changed = True
            if self.heartbeat is None or now > self.heartbeat:
                self.heartbeat = now
        held, confirmed = set(), set()
        check = []
        for row in candidates:
            item_id = row["item_id"]
            if item_id in flagged:
                # The extension's own restock path decides this return.
                if self.pending.pop(item_id, None) is not None:
                    changed = True
                continue
            first = self.pending.get(item_id)
            if first is not None:
                if now - first >= CONFIRM_SECONDS:
                    del self.pending[item_id]
                    confirmed.add(item_id)
                    events.append(("confirmed", item_id, now - first))
                    changed = True
                else:
                    held.add(item_id)
                continue
            if now - self.cooldown.get(item_id, -math.inf) < COOLDOWN_SECONDS:
                continue
            check.append(item_id)
        if check:
            for item_id, last in self._last_seen(sorted(set(check))).items():
                away = self._covered(last, now)
                if away >= AWAY_SECONDS and len(self.pending) < MAX_PENDING:
                    self.pending[item_id] = now
                    held.add(item_id)
                    events.append(("held", item_id, away))
                    changed = True
        confirmed |= self._low_comebacks(low, flagged, now, events)
        if any(stage == "confirmed" and item_id in confirmed for stage, item_id, _ in events):
            changed = True
        stale = self.heartbeat is not None and (
            self._saved_heartbeat is None
            or now - self._saved_heartbeat >= HEARTBEAT_SAVE_SECONDS)
        if changed or stale:
            self._save()
        return held, confirmed, events

    def _low_comebacks(self, rows, flagged, now, events):
        """Sub-$80 offers seen again after LOW_AWAY_SECONDS unseen: confirmed at once."""
        self._low_recent = [t for t in self._low_recent if now - t < 3600]
        check = [row["item_id"] for row in rows
                 if row["item_id"] not in flagged
                 and now - self.cooldown.get(row["item_id"], -math.inf) >= COOLDOWN_SECONDS]
        out = set()
        if not check:
            return out
        for item_id, last in sorted(self._last_seen(sorted(set(check))).items()):
            if len(self._low_recent) >= LOW_MAX_PER_HOUR:
                break
            away = self._covered(last, now)
            if away >= LOW_AWAY_SECONDS:
                out.add(item_id)
                self._low_recent.append(now)
                events.append(("confirmed", item_id, away))
        return out

    def settle(self, confirmed: set[str], restocked: set[str], *, now: float | None = None) -> None:
        """After the queue recorded the intake: start every announced offer's cooldown.

        confirmed: this rule's confirmations (recorded with the restock flag,
        whether or not the queue's own restock rule re-armed them). restocked:
        offers the queue routed as 'restocked' in this intake, by either path.
        """
        ids = set(confirmed) | set(restocked)
        if not ids:
            return
        now = self.clock() if now is None else now
        for item_id in ids:
            self.cooldown[item_id] = now
            self.pending.pop(item_id, None)
        self._save()

    def health(self) -> dict:
        with self.lock:
            return {"enabled": True, "pending": len(self.pending), "cooldown": len(self.cooldown),
                    "outages": len(self.outages), "heartbeat": self.heartbeat,
                    "last_error": self.last_error}
