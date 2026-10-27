"""Low-priority, exact-user welcomes sourced from private native join events.

No Gateway/member-list intent is needed. The separate ledger advances its
cursor atomically with outbox creation. An uncertain POST is reconciled by its
persisted nonce, never blindly repeated after Discord's short dedupe window.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .api import DiscordError
from .monitoring import emit_event

_ID = re.compile(r"[0-9]{17,20}\Z")
_POLL_SECONDS = 30
_REJOIN_SECONDS = 86400
_RETENTION_SECONDS = 7 * 86400
_MAX_PENDING = 1000


class _Deferred(Exception):
    pass


def _snowflake(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


class WelcomeWorker:
    """One worker owns a small SQLite outbox; health exposes counters, not IDs."""

    def __init__(
        self,
        client,
        state,
        path,
        *,
        enabled=False,
        paid_due=None,
        auditor=None,
        stop_event=None,
        clock=None,
        event_sink=None,
    ):
        self.client, self.state = client, state
        self.enabled = enabled
        self.path = Path(path)
        self.paid_due = paid_due or (lambda: False)
        self.auditor = auditor
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.clock = clock or time.time
        self.event_sink = event_sink
        self._lock = threading.RLock()
        self._cycle_lock = threading.Lock()
        self._next_poll = 0
        self._last_success = None
        self._last_error = None
        self._configured = False
        self._thread = None
        self._new_activation = False
        if not enabled:
            return
        self.guild = state.get("guild_id")
        self.bot = state.get("bot_user_id")
        channels = state.get("channels", {})
        self.source, self.destination = channels.get("audit"), channels.get("greeting")
        self.plan_guide = channels.get("welcome")
        self.notification_guide = channels.get("how-it-works")
        if (
            not all(
                _snowflake(value)
                for value in (
                    self.guild,
                    self.bot,
                    self.source,
                    self.destination,
                    self.plan_guide,
                    self.notification_guide,
                )
            )
            or self.source == self.destination
        ):
            raise ValueError("Welcome requires distinct, configured audit and greeting channels")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(descriptor)
        with self._db() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
                raise ValueError("unsupported welcome database schema")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS welcomes (
                    event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    nonce TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
                    created REAL NOT NULL, attempted REAL, next_attempt REAL NOT NULL,
                    message_id TEXT);
                CREATE TABLE IF NOT EXISTS cooldowns (
                    user_hash TEXT PRIMARY KEY, until REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS welcome_due ON welcomes(state,next_attempt);
                PRAGMA user_version=1;
            """)
            binding = hashlib.sha256(
                "|".join(
                    (
                        self.guild,
                        self.bot,
                        self.source,
                        self.destination,
                    )
                ).encode()
            ).hexdigest()
            previous = db.execute("SELECT value FROM settings WHERE key='binding'").fetchone()
            if previous is not None and previous[0] != binding:
                raise ValueError("Welcome ledger belongs to another channel configuration")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('binding',?)", (binding,))
            # Persist activation before any API read or paid-work deferral.
            # A lazy latest-message baseline would silently skip real joins
            # that happened while a product burst delayed the first poll.
            activation_ms = max(0, int(self.clock() * 1000) - 1420070400000)
            self._new_activation = bool(
                db.execute(
                    "INSERT OR IGNORE INTO settings VALUES ('cursor',?)",
                    (str(activation_ms << 22),),
                ).rowcount
            )

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _available(self):
        if self.stop_event.is_set() or self.paid_due():
            raise _Deferred

    def request(self, *args, **kwargs):
        """Audit proxy rechecks product priority before every provider request."""
        self._available()
        return self.client.request(*args, **kwargs)

    def _history(self, channel, **kwargs):
        self._available()
        rows = self.client.channel_messages(channel, **kwargs)
        if not isinstance(rows, list) or len(rows) > kwargs.get("limit", 100):
            raise DiscordError("Invalid welcome history response")
        for row in rows:
            if not isinstance(row, dict) or not _snowflake(row.get("id")):
                raise DiscordError("Invalid welcome history message")
        return rows

    def _ready(self):
        from .provision import audit

        assessment = (self.auditor or audit)(self, self.guild, self.state)
        if not isinstance(assessment, dict) or assessment.get("ok") is not True:
            self._last_error = "privacy_audit"
            return False
        guild = self.request("GET", f"/guilds/{self.guild}")
        flags = guild.get("system_channel_flags") if isinstance(guild, dict) else None
        if (
            not isinstance(guild, dict)
            or guild.get("system_channel_id") != self.source
            or type(flags) is not int
            or flags & 1  # SUPPRESS_JOIN_NOTIFICATIONS
            or not flags & 8  # SUPPRESS_JOIN_NOTIFICATION_REPLIES
        ):
            self._last_error = "system_channel"
            return False
        self._configured = True
        return True

    def _ingest(self, rows, now):
        with self._db() as db:
            cursor = int(db.execute("SELECT value FROM settings WHERE key='cursor'").fetchone()[0])
            scan_head = db.execute("SELECT value FROM settings WHERE key='scan_head'").fetchone()
            head = max(
                cursor,
                int(scan_head[0]) if scan_head else cursor,
                *(int(row["id"]) for row in rows),
            )
            pending = db.execute(
                "SELECT COUNT(*) FROM welcomes WHERE state IN ('pending','sending')"
            ).fetchone()[0]
            incoming = sum(int(row["id"]) > cursor for row in rows)
            if pending + incoming > _MAX_PENDING:
                # Keep the scan floor/progress untouched, but let the caller
                # drain existing greetings so this bounded queue can recover.
                return False
            for row in sorted(rows, key=lambda value: int(value["id"])):
                if int(row["id"]) <= cursor:
                    continue
                author = row.get("author")
                if row.get("type") != 7 or not isinstance(author, dict):
                    continue
                user = author.get("id")
                if (
                    not _snowflake(user)
                    or author.get("bot", False) is not False
                    or user == self.bot
                ):
                    continue
                digest = hashlib.sha256(user.encode()).hexdigest()
                cooldown = db.execute(
                    "SELECT until FROM cooldowns WHERE user_hash=?", (digest,)
                ).fetchone()
                if cooldown is not None and cooldown[0] > now:
                    continue
                nonce = hashlib.sha256((self.guild + row["id"]).encode()).hexdigest()[:24]
                inserted = db.execute(
                    "INSERT OR IGNORE INTO welcomes VALUES (?,?,?,'pending',?,NULL,?,NULL)",
                    (row["id"], user, nonce, now, now),
                ).rowcount
                if inserted:
                    db.execute(
                        "INSERT INTO cooldowns VALUES (?,?) ON CONFLICT(user_hash) "
                        "DO UPDATE SET until=excluded.until",
                        (digest, now + _REJOIN_SECONDS),
                    )
            # A source page may be the newest 100 events. Walk backward
            # until reaching the saved floor; never jump over an unseen gap.
            if len(rows) < 100 or any(int(row["id"]) <= cursor for row in rows):
                db.execute("UPDATE settings SET value=? WHERE key='cursor'", (str(head),))
                db.execute("DELETE FROM settings WHERE key IN ('scan_head','scan_before')")
            else:
                for key, value in (
                    ("scan_head", str(head)),
                    ("scan_before", str(min(int(row["id"]) for row in rows))),
                ):
                    db.execute(
                        "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) "
                        "DO UPDATE SET value=excluded.value",
                        (key, value),
                    )
        return True

    def _settle(self, event, state, *, message=None):
        with self._db() as db:
            db.execute(
                "UPDATE welcomes SET state=?,message_id=? WHERE event_id=?", (state, message, event)
            )

    def _payload(self, row):
        return {
            "content": (
                f"Welcome <@{row['user_id']}>! Choose your plan in <#{self.plan_guide}>, "
                f"then visit <#{self.notification_guide}> to pick your notifications. "
                "Glad you're here!"
            ),
            "allowed_mentions": {
                "parse": [],
                "users": [row["user_id"]],
                "roles": [],
                "replied_user": False,
            },
            "nonce": row["nonce"],
            "enforce_nonce": True,
        }

    def _collect(self, result, now):
        with self._db() as db:
            before = db.execute("SELECT value FROM settings WHERE key='scan_before'").fetchone()
        result["baseline"] = self._new_activation
        self._new_activation = False
        return self._ingest(
            self._history(self.source, **({"before": before[0]} if before else {})), now
        )

    def _send_pending(self, result, now):
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM welcomes WHERE state IN ('pending','sending') "
                "AND next_attempt<=? ORDER BY created,event_id LIMIT 5",
                (now,),
            ).fetchall()
        history = None
        for row in rows:
            self._available()
            if row["state"] == "sending":
                if history is None:
                    history = self._history(self.destination)
                matches = [
                    message
                    for message in history
                    if message.get("nonce") == row["nonce"]
                    and isinstance(message.get("author"), dict)
                    and message["author"].get("id") == self.bot
                ]
                if matches:
                    self._settle(row["event_id"], "sent", message=matches[0]["id"])
                    result["reconciled"] += 1
                else:
                    # A crash may have occurred before or after transmission.
                    # Prefer one missed welcome over another direct ping.
                    self._settle(row["event_id"], "review")
                    result["review"] += 1
                    self._last_error = "ambiguous_send"
                continue
            payload = self._payload(row)
            # Commit uncertain state before POST. Restart never sends this row
            # again unless a definitive 429 says Discord rejected the request.
            with self._db() as db:
                claimed = db.execute(
                    "UPDATE welcomes SET state='sending',attempted=? "
                    "WHERE event_id=? AND state='pending'",
                    (now, row["event_id"]),
                ).rowcount
            if not claimed:
                continue
            try:
                self._available()
            except _Deferred:
                self._settle(row["event_id"], "pending")
                raise
            try:
                response = self.request("POST", f"/channels/{self.destination}/messages", payload)
                if not isinstance(response, dict) or not _snowflake(response.get("id")):
                    raise DiscordError("Invalid welcome send response", ambiguous=True)
                self._settle(row["event_id"], "sent", message=response["id"])
                result["sent"] += 1
            except _Deferred:
                self._settle(row["event_id"], "pending")
                raise
            except DiscordError as exc:
                if exc.status == 429:
                    delay = exc.retry_after
                    delay = (
                        delay
                        if type(delay) in (int, float) and math.isfinite(delay) and delay >= 0
                        else 30
                    )
                    with self._db() as db:
                        db.execute(
                            "UPDATE welcomes SET state='pending',next_attempt=? WHERE event_id=?",
                            (now + max(1, delay), row["event_id"]),
                        )
                    result["throttled"] = True
                    self._last_error = "rate_limited"
                    self._next_poll = max(self._next_poll, now + delay)
                elif exc.status in {400, 401, 403, 404} and not exc.ambiguous:
                    self._settle(row["event_id"], "failed")
                    self._last_error = "send_rejected"
                else:
                    self._last_error = "ambiguous_send"
                break

    def tick(self):
        result = {
            "sent": 0,
            "reconciled": 0,
            "review": 0,
            "baseline": False,
            "blocked": False,
            "deferred": False,
            "throttled": False,
        }
        with self._cycle_lock:
            now = self.clock()
            if not self.enabled or now < self._next_poll:
                return result
            self._next_poll = now + _POLL_SECONDS
            try:
                self._available()
                self._configured = False
                if not self._ready():
                    result["blocked"] = True
                    return result
                self._last_error = None
                with self._db() as db:
                    db.execute(
                        "UPDATE welcomes SET state='skipped' WHERE state='pending' AND created<?",
                        (now - _REJOIN_SECONDS,),
                    )
                    db.execute(
                        "DELETE FROM welcomes WHERE state NOT IN ('pending','sending') "
                        "AND created<?",
                        (now - _RETENTION_SECONDS,),
                    )
                    db.execute("DELETE FROM cooldowns WHERE until<=?", (now,))
                ingested = self._collect(result, now)
                if not ingested:
                    self._last_error = "backlog_full"
                    result["deferred"] = True
                self._send_pending(result, now)
                self._last_success = now
            except _Deferred:
                result["deferred"] = True
            except DiscordError as exc:
                self._last_error = "rate_limited" if exc.status == 429 else "provider_error"
                result["throttled"] = exc.status == 429
                if (
                    exc.status == 429
                    and type(exc.retry_after) in (int, float)
                    and math.isfinite(exc.retry_after)
                ):
                    self._next_poll = max(self._next_poll, now + max(0, exc.retry_after))
            except Exception as exc:
                self._last_error = "worker_error"
                emit_event(
                    self.event_sink, "welcome_error", phase="welcome", error_type=type(exc).__name__
                )
            finally:
                emit_event(self.event_sink, "welcome_cycle", phase="welcome", **result)
        return result

    def health(self):
        with self._lock:
            result = {
                "enabled": self.enabled,
                "configured": self._configured,
                "last_success": self._last_success,
                "last_error": self._last_error,
                "pending": 0,
                "manual_review": 0,
                "sent": 0,
                "failed": 0,
                "skipped": 0,
            }
            if self.enabled:
                with self._db() as db:
                    counts = dict(db.execute("SELECT state,COUNT(*) FROM welcomes GROUP BY state"))
                result.update(
                    pending=counts.get("pending", 0) + counts.get("sending", 0),
                    manual_review=counts.get("review", 0),
                    sent=counts.get("sent", 0),
                    failed=counts.get("failed", 0),
                    skipped=counts.get("skipped", 0),
                )
            return result

    def start(self):
        if not self.enabled or self._thread is not None:
            return

        def work():
            while not self.stop_event.is_set():
                self.tick()
                self.stop_event.wait(_POLL_SECONDS)

        self._thread = threading.Thread(target=work, name="discord-welcome", daemon=True)
        self._thread.start()

    def stop(self):
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=12)
