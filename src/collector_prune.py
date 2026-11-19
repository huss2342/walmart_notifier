"""Prune old Discord dispatch envelopes from the collector queue database.

collector.sqlite3 was 576 MB on Nov 19 and grew ~20 MB a day with no DELETE
anywhere. 322 MB of it were `dispatches`: one frozen copy of every message
ever posted (its rendered payload, ~3.5 KB, and its Discord nonce), 77k rows,
about 49k of them older than 14 days.

What is deleted, and why it is safe (discord_bridge/core.py, Nov 19):
  * Only dispatches in a final state (sent, skipped, failed, rejected_413)
    whose member jobs are all finished (none pending or leased) and were last
    sent or due more than KEEP_DAYS ago. A dispatch is read back only while
    one of its jobs is being claimed or retried (claim_batch, finish_batch,
    retry_batch, _cancel_dispatch); a pending retry's dispatch is itself
    pending, and the oldest job that can still be pending (free recap: due
    24 h after sighting, lives 7 days) is far younger than 14 days. A restock
    re-arm clears the job's batch_id and gets a new, restock-specific id.
What is never deleted:
  * items: "new or duplicate" is decided by an item's row existing.
  * jobs: UNIQUE(item_id, route) keeps an item from being queued twice for a
    route (a deleted free-recap job would be re-posted a day after the item's
    next sighting), and a restock re-arms the finished row in place.
  * the request tables (personal DMs), seeded_items and delivery gates.
Freed pages are reused by SQLite, so the file stops growing rather than
shrinking (no VACUUM: it would lock the queue for minutes).

Work happens on its own thread in small batches, each its own short write
transaction, with a pause between batches, so intake (which takes the same
write lock) waits at most one batch.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

log = logging.getLogger(__name__)

KEEP_DAYS = 14
BATCH = 500
PAUSE_SECONDS = 1.0
FIRST_RUN_SECONDS = 10 * 60
EVERY_SECONDS = 6 * 3600
MAX_BATCHES_PER_RUN = 400
FINAL_STATES = ("sent", "skipped", "failed", "rejected_413")


def prune_dispatches(path: Path | str, *, now: float | None = None, keep_days: float = KEEP_DAYS,
                     batch: int = BATCH, pause: float = PAUSE_SECONDS,
                     max_batches: int = MAX_BATCHES_PER_RUN, stop: threading.Event | None = None,
                     sleep=time.sleep) -> int:
    """Delete old final dispatch envelopes in rowid order; returns rows deleted."""
    now = time.time() if now is None else now
    cutoff = now - keep_days * 86400
    states = ",".join("?" * len(FINAL_STATES))
    deleted, after = 0, 0
    uri = Path(path).resolve().as_uri() + "?mode=rw"
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as db:
        # The container's /tmp is a 16 MB tmpfs: keep any sort in memory.
        db.execute("PRAGMA temp_store=MEMORY")
        for _ in range(max_batches):
            if stop is not None and stop.is_set():
                break
            with db:  # One short write transaction per window.
                db.execute("BEGIN IMMEDIATE")
                window = db.execute(
                    "SELECT rowid FROM dispatches WHERE rowid>? ORDER BY rowid LIMIT ?",
                    (after, batch),
                ).fetchall()
                if not window:
                    break
                first, last = window[0][0], window[-1][0]
                old = [row[0] for row in db.execute(
                    f"""SELECT d.rowid FROM dispatches d
                    WHERE d.rowid BETWEEN ? AND ? AND d.state IN ({states})
                    AND NOT EXISTS (
                        SELECT 1 FROM json_each(d.members) member
                        JOIN jobs job ON job.id=member.value
                        WHERE job.state IN ('pending','leased')
                        OR COALESCE(job.sent_at,job.due)>=?)""",
                    (first, last, *FINAL_STATES, cutoff),
                )]
                if old:
                    db.executemany("DELETE FROM dispatches WHERE rowid=?", [(r,) for r in old])
                    deleted += len(old)
                after = last
            if not old:
                # Rowids follow insertion: a window with nothing old left means
                # the rest is newer. (Kept rows older than it: in use, or a
                # restock re-sent one of their jobs.)
                newest = db.execute(
                    "SELECT COALESCE(job.sent_at,job.due) FROM dispatches d "
                    "JOIN jobs job ON job.id=json_extract(d.members,'$[0]') WHERE d.rowid=?",
                    (after,),
                ).fetchone()
                if newest is None or newest[0] is None or newest[0] >= cutoff:
                    break
            sleep(pause)
    return deleted


class CollectorPruner:
    """Runs prune_dispatches 10 min after start and then every 6 h, off the hot path."""

    def __init__(self, path: Path | str, *, stop_event: threading.Event,
                 keep_days: float | None = None):
        self.path = Path(path)
        self.stop_event = stop_event
        if keep_days is None:
            try:
                keep_days = float(os.environ.get("DISCORD_PRUNE_KEEP_DAYS", KEEP_DAYS))
            except ValueError:
                keep_days = KEEP_DAYS
        # 0 turns pruning off; never keep less than a week (free jobs live 8 days).
        self.keep_days = 0 if keep_days <= 0 else max(7.0, keep_days)
        self.last_run: float | None = None
        self.last_deleted = 0
        self.last_error: str | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.keep_days <= 0 or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="collector-prune", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        delay = FIRST_RUN_SECONDS
        while not self.stop_event.wait(delay):
            delay = EVERY_SECONDS
            try:
                self.last_deleted = prune_dispatches(
                    self.path, keep_days=self.keep_days, stop=self.stop_event,
                    sleep=self.stop_event.wait,
                )
                self.last_error = None
                if self.last_deleted:
                    log.info("Pruned %s dispatch envelopes older than %s days.",
                             self.last_deleted, self.keep_days)
            except sqlite3.Error as exc:
                # Retried at the next run; pruning never affects delivery.
                self.last_error = type(exc).__name__
                log.warning("Collector pruning failed (%s).", type(exc).__name__)
            self.last_run = time.time()

    def health(self) -> dict:
        return {"enabled": self.keep_days > 0, "keep_days": self.keep_days,
                "last_run": self.last_run, "last_deleted": self.last_deleted,
                "last_error": self.last_error}
