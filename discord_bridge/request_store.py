"""Private watches and an atomic, durable per-member notification outbox.

These tables share JobStore's connection during observation intake. No historical
scan or best-effort after-commit callback can create a personal notification.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

MAX_REQUESTS = 10
ALERT_LIFETIME_SECONDS = 20 * 60
LEASE_SECONDS = 60
_SNOWFLAKE = re.compile(r"[0-9]{17,20}")
_PRODUCT = re.compile(r"(?:ip-)?([0-9]{6,20})", re.IGNORECASE)


def _now(value=None):
    value = time.time() if value is None else value
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid request time.")
    return value


def _user(value):
    if not isinstance(value, str) or not _SNOWFLAKE.fullmatch(value):
        raise ValueError("Invalid Discord member.")
    return value


def _request_id(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("Request not found. Use /request list to find your requests.")
    if not re.fullmatch(r"[1-9][0-9]{0,17}", str(value)):
        raise ValueError("Request not found. Use /request list to find your requests.")
    return int(value)


def _words(value):
    normalized = unicodedata.normalize("NFKC", value).casefold()
    # Unicode alphanumeric words, with punctuation (including underscore) as
    # separators. No user-controlled regular expression is ever evaluated.
    return re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)


def parse_query(value):
    """Validate exact Walmart IDs/links or a bounded AND-of-whole-words query."""
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(
            "Enter keywords (up to 150 characters), a Walmart item ID, or a Walmart item link."
        )
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("Use a single line of ordinary text for your request.")
    query = value.strip()
    exact = _PRODUCT.fullmatch(query)
    if exact:
        return query, "exact", "ip-" + exact.group(1)
    if query.lower().startswith("ip-") or query.isdecimal():
        raise ValueError("A Walmart item ID must contain 6 to 20 digits.")
    if "://" in query or query.lower().startswith(("www.", "walmart.com/")):
        try:
            parsed = urlsplit(query)
        except ValueError:
            raise ValueError(
                "Use an HTTPS Walmart product link containing /ip/ and the item ID."
            ) from None
        match = re.fullmatch(r"/ip/(?:[^/?#]+/)?([0-9]{6,20})/?", parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.netloc not in {"www.walmart.com", "walmart.com"}
            or not match
            or any(char.isspace() for char in query)
        ):
            raise ValueError("Use an HTTPS Walmart product link containing /ip/ and the item ID.")
        # Save a short, predictable canonical product request, not URL tracking.
        number = match.group(1)
        return f"https://www.walmart.com/ip/{number}", "exact", "ip-" + number
    words = sorted(set(_words(query)))
    if len(query) > 150 or not words:
        raise ValueError("Enter keywords containing letters or numbers, up to 150 characters.")
    return query, "keywords", json.dumps(words, ensure_ascii=True, separators=(",", ":"))


def _all(db, statement, parameters=()):
    cursor = db.execute(statement, parameters)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _one(db, statement, parameters=()):
    rows = _all(db, statement, parameters)
    return rows[0] if rows else None


def initialize_requests(db):
    """Create only namespaced tables; never commit a caller's transaction."""
    statements = (
        "CREATE TABLE IF NOT EXISTS personal_request_settings "
        "(key TEXT PRIMARY KEY,value TEXT NOT NULL)",
        """CREATE TABLE IF NOT EXISTS personal_request_users (
            user_id TEXT PRIMARY KEY, dm_status TEXT NOT NULL DEFAULT 'needs_test',
            channel_id TEXT)""",
        """CREATE TABLE IF NOT EXISTS personal_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,
            query TEXT NOT NULL,kind TEXT NOT NULL,matcher TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,created REAL NOT NULL,updated REAL NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS personal_requests_user ON personal_requests(user_id,id)",
        """CREATE TABLE IF NOT EXISTS personal_request_alerts (
            id TEXT PRIMARY KEY,user_id TEXT NOT NULL,item_id TEXT NOT NULL,
            payload TEXT NOT NULL,observed REAL NOT NULL,
            created REAL NOT NULL,expires REAL NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',next_attempt REAL NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,lease_token TEXT,lease_until REAL,
            channel_id TEXT,message_id TEXT,reason TEXT,nonce TEXT NOT NULL,
            UNIQUE(user_id,item_id))""",
        """CREATE TABLE IF NOT EXISTS personal_request_matches (
            alert_id TEXT NOT NULL,request_id INTEGER NOT NULL,revision INTEGER NOT NULL,
            PRIMARY KEY(alert_id,request_id))""",
        """CREATE INDEX IF NOT EXISTS personal_request_due
            ON personal_request_alerts(state,next_attempt,expires)""",
        """CREATE INDEX IF NOT EXISTS personal_request_item
            ON personal_request_alerts(item_id,state)""",
        """CREATE INDEX IF NOT EXISTS personal_request_matches_request
            ON personal_request_matches(request_id)""",
    )
    for statement in statements:
        db.execute(statement)


def _matches(request, item, *, words=None):
    if request["kind"] == "exact":
        return request["matcher"] == item["item_id"]
    tokens = request.get("_tokens")
    if tokens is None:
        tokens = set(json.loads(request["matcher"]))
    return tokens.issubset(set(_words(item["title"])) if words is None else words)


def match_requests(db, normalized_items, *, now, seed_only=False):
    """Match only this freshly accepted observation inside its intake transaction.

    Existing product IDs, including previously seeded IDs, remain eligible when
    they are freshly reobserved. Seed/import batches themselves never alert.
    Returns the number of newly queued member/product pairs.
    """
    requests = _all(db, "SELECT * FROM personal_requests WHERE updated<=?", (now,))
    if not requests:
        return 0
    now = _now(now)
    for request in requests:
        if request["kind"] == "keywords":
            request["_tokens"] = set(json.loads(request["matcher"]))
    db.execute(
        "UPDATE personal_request_alerts SET state='expired',reason='stale' "
        "WHERE state IN ('pending','leased') AND expires<=?",
        (now,),
    )
    queued = 0
    for item in normalized_items:
        item_id = item["item_id"]
        # Keep the latest availability/title for send-time revalidation. A
        # sending attempt is already committed and is not silently requeued.
        encoded = json.dumps(item, ensure_ascii=True, separators=(",", ":"))
        db.execute(
            "UPDATE personal_request_alerts SET payload=?,observed=? "
            "WHERE item_id=? AND state IN ('pending','leased')",
            (encoded, now, item_id),
        )
        if seed_only or item.get("available") is not True:
            db.execute(
                "UPDATE personal_request_alerts SET state='cancelled',reason=? "
                "WHERE item_id=? AND state IN ('pending','leased')",
                ("seeded" if seed_only else "unavailable", item_id),
            )
            continue
        by_user = {}
        words = set(_words(item["title"]))
        for request in requests:
            if _matches(request, item, words=words):
                by_user.setdefault(request["user_id"], []).append(request)
        for user_id, matches in by_user.items():
            alert_id = hashlib.sha256(f"request-v1:{user_id}:{item_id}".encode()).hexdigest()[:32]
            nonce = str(int(hashlib.sha256(alert_id.encode()).hexdigest()[:20], 16))
            inserted = db.execute(
                """INSERT OR IGNORE INTO personal_request_alerts
                (id,user_id,item_id,payload,observed,created,expires,next_attempt,nonce)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    alert_id,
                    user_id,
                    item_id,
                    encoded,
                    now,
                    now,
                    now + ALERT_LIFETIME_SECONDS,
                    now,
                    nonce,
                ),
            ).rowcount
            queued += inserted
            state = db.execute(
                "SELECT state FROM personal_request_alerts WHERE id=?", (alert_id,)
            ).fetchone()[0]
            if state in {"cancelled", "expired", "failed"}:
                # These states are definitely unsent. A new eligible live
                # observation can try again; sent, sending and review remain
                # protected permanently against an unknown duplicate DM.
                db.execute("DELETE FROM personal_request_matches WHERE alert_id=?", (alert_id,))
                db.execute(
                    """UPDATE personal_request_alerts SET payload=?,observed=?,created=?,expires=?,
                    state='pending',next_attempt=?,attempts=0,lease_token=NULL,lease_until=NULL,
                    channel_id=NULL,message_id=NULL,reason=NULL,nonce=? WHERE id=?""",
                    (
                        encoded,
                        now,
                        now,
                        now + ALERT_LIFETIME_SECONDS,
                        now,
                        str(secrets.randbits(80)),
                        alert_id,
                    ),
                )
                state = "pending"
                queued += 1
            if state not in {"pending", "leased"}:
                continue
            for request in matches:
                db.execute(
                    "INSERT OR REPLACE INTO personal_request_matches VALUES (?,?,?)",
                    (alert_id, request["id"], request["revision"]),
                )
    return queued


class RequestStore:
    def __init__(self, path: Path | str, *, guild_id=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.guild_id = _user(guild_id) if guild_id is not None else None
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            initialize_requests(db)
            if self.guild_id:
                previous = db.execute(
                    "SELECT value FROM personal_request_settings WHERE key='guild'"
                ).fetchone()
                if previous and previous[0] != self.guild_id:
                    raise ValueError("Personal requests belong to another Discord server.")
                db.execute(
                    "INSERT OR IGNORE INTO personal_request_settings VALUES ('guild',?)",
                    (self.guild_id,),
                )

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, user_id, query, *, now=None):
        user_id, now = _user(user_id), _now(now)
        query, kind, matcher = parse_query(query)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) FROM personal_requests WHERE user_id=?", (user_id,)
            ).fetchone()[0]
            if count >= MAX_REQUESTS:
                raise ValueError(
                    "You can have up to 10 requests. Delete one with /request delete first."
                )
            db.execute(
                "INSERT OR IGNORE INTO personal_request_users(user_id) VALUES (?)", (user_id,)
            )
            if db.execute(
                "SELECT 1 FROM personal_requests WHERE user_id=? AND kind=? AND matcher=?",
                (user_id, kind, matcher),
            ).fetchone():
                raise ValueError("You already have this request. Use /request list to manage it.")
            cursor = db.execute(
                """INSERT INTO personal_requests(user_id,query,kind,matcher,created,updated)
                VALUES (?,?,?,?,?,?)""",
                (user_id, query, kind, matcher, now, now),
            )
            return self._request(db, user_id, cursor.lastrowid)

    @staticmethod
    def _request(db, user_id, request_id):
        row = _one(
            db,
            "SELECT id,query,kind,revision,created,updated FROM personal_requests "
            "WHERE user_id=? AND id=?",
            (user_id, request_id),
        )
        if row is None:
            raise ValueError("Request not found. Use /request list to find your requests.")
        status = db.execute(
            "SELECT dm_status FROM personal_request_users WHERE user_id=?", (user_id,)
        ).fetchone()
        row["dm_status"] = row["status"] = status[0] if status else "needs_test"
        delivery = db.execute(
            """SELECT a.state FROM personal_request_alerts a
            JOIN personal_request_matches m ON m.alert_id=a.id
            WHERE m.request_id=? AND a.state IN ('review','failed')
            ORDER BY CASE a.state WHEN 'review' THEN 0 ELSE 1 END,a.created DESC LIMIT 1""",
            (request_id,),
        ).fetchone()
        row["delivery_status"] = (
            "needs_review"
            if delivery and delivery[0] == "review"
            else "failed"
            if delivery
            else row["status"]
        )
        return row

    def list(self, user_id):
        user_id = _user(user_id)
        with self._db() as db:
            ids = db.execute(
                "SELECT id FROM personal_requests WHERE user_id=? ORDER BY id", (user_id,)
            ).fetchall()
            return [self._request(db, user_id, row[0]) for row in ids]

    def edit(self, user_id, request_id, query, *, now=None):
        user_id, request_id, now = _user(user_id), _request_id(request_id), _now(now)
        query, kind, matcher = parse_query(query)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._request(db, user_id, request_id)
            if db.execute(
                "SELECT 1 FROM personal_requests "
                "WHERE user_id=? AND kind=? AND matcher=? AND id!=?",
                (user_id, kind, matcher, request_id),
            ).fetchone():
                raise ValueError("You already have this request. Use /request list to manage it.")
            db.execute(
                """UPDATE personal_requests SET query=?,kind=?,matcher=?,
                revision=revision+1,updated=?
                WHERE user_id=? AND id=?""",
                (query, kind, matcher, now, user_id, request_id),
            )
            db.execute("DELETE FROM personal_request_matches WHERE request_id=?", (request_id,))
            self._cancel_orphans(db)
            return self._request(db, user_id, request_id)

    def delete(self, user_id, request_id):
        user_id, request_id = _user(user_id), _request_id(request_id)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            deleted = db.execute(
                "DELETE FROM personal_requests WHERE user_id=? AND id=?", (user_id, request_id)
            ).rowcount
            if deleted:
                db.execute("DELETE FROM personal_request_matches WHERE request_id=?", (request_id,))
                self._cancel_orphans(db)
            return bool(deleted)

    @staticmethod
    def _cancel_orphans(db):
        db.execute(
            """UPDATE personal_request_alerts SET state='cancelled',reason='request_changed'
            WHERE state IN ('pending','leased') AND NOT EXISTS (
            SELECT 1 FROM personal_request_matches m JOIN personal_requests r ON r.id=m.request_id
            WHERE m.alert_id=personal_request_alerts.id AND m.revision=r.revision)"""
        )

    def set_dm_status(self, user_id, status, *, channel_id=None):
        user_id = _user(user_id)
        if status not in {"ready", "blocked", "needs_test"}:
            raise ValueError("Invalid DM delivery status.")
        if channel_id is not None:
            _user(channel_id)
        with self._db() as db:
            db.execute(
                """INSERT INTO personal_request_users(user_id,dm_status,channel_id) VALUES (?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET dm_status=excluded.dm_status,
                channel_id=COALESCE(excluded.channel_id,personal_request_users.channel_id)""",
                (user_id, status, channel_id),
            )

    def dm_status(self, user_id):
        user_id = _user(user_id)
        with self._db() as db:
            row = db.execute(
                "SELECT dm_status FROM personal_request_users WHERE user_id=?", (user_id,)
            ).fetchone()
            return row[0] if row else "needs_test"

    @staticmethod
    def _retire(db, now):
        db.execute(
            "UPDATE personal_request_alerts SET state='review',reason='interrupted_send' "
            "WHERE state='sending' AND lease_until<=?",
            (now,),
        )
        db.execute(
            "UPDATE personal_request_alerts SET state='expired',reason='stale' "
            "WHERE state IN ('pending','leased') AND expires<=?",
            (now,),
        )

    @staticmethod
    def _hydrate(db, row, now):
        if (
            row is None
            or row["state"] != "leased"
            or row["expires"] <= now
            or row["lease_until"] <= now
        ):
            return None
        user = _one(db, "SELECT * FROM personal_request_users WHERE user_id=?", (row["user_id"],))
        if not user or user["dm_status"] != "ready":
            return None
        item = json.loads(row["payload"])
        if item.get("available") is not True:
            return None
        requests = _all(
            db,
            """SELECT r.* FROM personal_request_matches m
            JOIN personal_requests r ON r.id=m.request_id
            WHERE m.alert_id=? AND m.revision=r.revision AND r.user_id=? AND r.updated<=?
            ORDER BY r.id""",
            (row["id"], row["user_id"], row["observed"]),
        )
        words = set(_words(item["title"]))
        matches = [request for request in requests if _matches(request, item, words=words)]
        if not matches:
            return None
        return {
            **row,
            "item": item,
            "queries": [request["query"] for request in matches],
            "channel_id": user["channel_id"],
        }

    def claim(self, *, now=None):
        now = _now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._retire(db, now)
            self._cancel_orphans(db)
            # Loop through invalidated observations without starving a later
            # valid member. Bound work even if old pending rows were corrupted.
            rows = _all(
                db,
                """SELECT a.* FROM personal_request_alerts a
                JOIN personal_request_users u USING(user_id)
                WHERE u.dm_status='ready' AND a.expires>? AND a.next_attempt<=?
                AND (a.state='pending' OR (a.state='leased' AND a.lease_until<=?))
                ORDER BY a.next_attempt,a.created,a.id LIMIT 20""",
                (now, now, now),
            )
            for row in rows:
                token = secrets.token_hex(16)
                db.execute(
                    "UPDATE personal_request_alerts SET state='leased',lease_token=?,lease_until=? "
                    "WHERE id=?",
                    (token, now + LEASE_SECONDS, row["id"]),
                )
                row.update(state="leased", lease_token=token, lease_until=now + LEASE_SECONDS)
                hydrated = self._hydrate(db, row, now)
                if hydrated:
                    return hydrated
                db.execute(
                    "UPDATE personal_request_alerts SET state='cancelled',"
                    "reason='no_current_match' WHERE id=?",
                    (row["id"],),
                )
            return None

    def validate(self, job, *, now=None):
        now = _now(now)
        with self._db() as db:
            row = _one(
                db,
                "SELECT * FROM personal_request_alerts WHERE id=? AND lease_token=?",
                (job["id"], job["lease_token"]),
            )
            return self._hydrate(db, row, now)

    def begin_send(self, job, *, now=None, channel_id=None):
        """Commit possible submission and return its exact validated snapshot."""
        now = _now(now)
        if channel_id is not None:
            _user(channel_id)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = _one(
                db,
                "SELECT * FROM personal_request_alerts WHERE id=? AND lease_token=?",
                (job["id"], job["lease_token"]),
            )
            snapshot = self._hydrate(db, row, now)
            if snapshot is None:
                return None
            channel_id = channel_id or snapshot.get("channel_id")
            changed = db.execute(
                "UPDATE personal_request_alerts SET state='sending',attempts=attempts+1,"
                "channel_id=? WHERE id=? AND lease_token=? AND state='leased'",
                (channel_id, job["id"], job["lease_token"]),
            ).rowcount
            if not changed:
                return None
            snapshot.update(
                state="sending", attempts=snapshot["attempts"] + 1, channel_id=channel_id
            )
            return snapshot

    def settle(self, job, state, *, now=None, reason=None, message_id=None):
        _now(now)
        if state not in {"sent", "failed", "cancelled", "review"}:
            raise ValueError("Invalid personal delivery settlement.")
        if message_id is not None:
            _user(message_id)
        with self._db() as db:
            return bool(
                db.execute(
                    """UPDATE personal_request_alerts SET state=?,reason=?,message_id=?
                WHERE id=? AND lease_token=? AND state IN ('leased','sending')""",
                    (state, reason, message_id, job["id"], job["lease_token"]),
                ).rowcount
            )

    def retry(self, job, *, now=None, delay=5, reason="remote_retry"):
        now = _now(now)
        if type(delay) not in (int, float) or not math.isfinite(delay):
            delay = 5
        with self._db() as db:
            return bool(
                db.execute(
                    """UPDATE personal_request_alerts SET state='pending',next_attempt=?,reason=?,
                lease_token=NULL,lease_until=NULL
                WHERE id=? AND lease_token=? AND state IN ('leased','sending')""",
                    (now + max(0.05, delay), reason, job["id"], job["lease_token"]),
                ).rowcount
            )

    def revoke_user(self, user_id):
        user_id = _user(user_id)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE personal_request_users SET dm_status='needs_test' WHERE user_id=?",
                (user_id,),
            )
            db.execute(
                "UPDATE personal_request_alerts SET state='cancelled',reason='pro_access_lost' "
                "WHERE user_id=? AND state IN ('pending','leased')",
                (user_id,),
            )

    def stats(self):
        with self._db() as db:
            result = dict.fromkeys(
                (
                    "pending",
                    "leased",
                    "sending",
                    "sent",
                    "failed",
                    "cancelled",
                    "expired",
                    "review",
                ),
                0,
            )
            result.update(
                {
                    row[0]: row[1]
                    for row in db.execute(
                        "SELECT state,COUNT(*) FROM personal_request_alerts GROUP BY state"
                    )
                }
            )
            result["requests"] = db.execute("SELECT COUNT(*) FROM personal_requests").fetchone()[0]
            result["dm_blocked"] = db.execute(
                "SELECT COUNT(*) FROM personal_request_users WHERE dm_status='blocked'"
            ).fetchone()[0]
            return result
