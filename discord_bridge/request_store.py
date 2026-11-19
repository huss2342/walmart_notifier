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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from .monitoring import emit_event
from .request_render import pack_catchup

ALERT_LIFETIME_SECONDS = 20 * 60
LEASE_SECONDS = 60
# Matches for listings that already existed when the member added the request
# ("backlog") keep gathering per member until BACKLOG_GATHER_SECONDS pass with
# no new older match (at most BACKLOG_MAX_GATHER_SECONDS after the first), so
# every search wave lands in one catch-up: the BUNDLE_LIMIT highest-value
# items, packed into as few DMs as fit Discord's limits. The rest stay visible
# in /request listed. New listings still alert at once.
BACKLOG_GATHER_SECONDS = 60
BACKLOG_MAX_GATHER_SECONDS = 5 * 60
BUNDLE_LIMIT = 10
# Due older matches revalidated for one catch-up, highest value first; any
# beyond this are counted and skipped with it, never sent as another DM.
CATCHUP_SCAN_LIMIT = 1000
# A send that may have reached Discord is parked in review. Once its lease is
# this old, one bounded read of the DM's history settles it: sent if the DM is
# there, otherwise failed (re-armed by the next in-stock sighting, never resent
# straight away). Unreadable or inconclusive history stays parked.
RECONCILE_REASONS = ("ambiguous_send", "interrupted_send", "unexpected_send_error")
RECONCILE_DELAY_SECONDS = 120
# /request listed shows older matches seen on a listing this recently.
LISTED_FRESH_SECONDS = 2 * 3600
_SNOWFLAKE = re.compile(r"[0-9]{17,20}")
_PRODUCT = re.compile(r"(?:ip-)?([0-9]{6,20})", re.IGNORECASE)


def member_ref(user_id):
    """Stable local correlation without putting Discord account IDs in logs."""
    return hashlib.sha256(f"personal-member-v1:{user_id}".encode()).hexdigest()[:32]


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


def _minimum_cents(value):
    if value is None:
        return None
    if type(value) not in (int, float, str):
        raise ValueError("Minimum value must be a nonnegative USD amount using whole cents.")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(
            "Minimum value must be a nonnegative USD amount using whole cents."
        ) from None
    if not amount.is_finite() or amount < 0 or amount > 100_000_000:
        raise ValueError("Minimum value must be between $0 and $100,000,000.")
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise ValueError("Minimum value must use whole cents, such as 100 or 100.25.")
    return int(cents) or None


def _parse_input(value, min_value, *, bulk):
    """Separate a shared trailing minimum from independently matched phrases."""
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError("Enter item keywords, IDs, or Walmart links, up to 512 characters total.")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("Use a single line of ordinary text for your request.")
    minimum = _minimum_cents(min_value)
    specified = min_value is not None
    value = value.strip()
    suffix = re.search(r"\s+\$([0-9]+(?:\.[0-9]{1,2})?)\+$", value)
    if suffix:
        inline = _minimum_cents(suffix.group(1))
        if specified and inline != minimum:
            raise ValueError("Use the same minimum value in the item text and min_value option.")
        value, minimum, specified = value[: suffix.start()], inline, True
    if "$" in value:
        raise ValueError("Place one minimum at the end, such as recliner, chair $100+.")
    parts = value.split(",")
    if not bulk and len(parts) != 1:
        raise ValueError("Edit one request at a time; use /request add for comma-separated items.")
    # Preserve the first spelling but store each equivalent normalized matcher
    # only once. Exact IDs and links for the same product are equivalent too.
    queries = {}
    for part in parts:
        query, kind, matcher = parse_query(part)
        queries.setdefault((kind, matcher), (query, kind, matcher))
    return list(queries.values()), minimum, specified


def _all(db, statement, parameters=()):
    cursor = db.execute(statement, parameters)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _one(db, statement, parameters=()):
    rows = _all(db, statement, parameters)
    return rows[0] if rows else None


def initialize_requests(db):
    """Create only namespaced tables; never commit a caller's transaction."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
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
        "CREATE INDEX IF NOT EXISTS personal_requests_exact "
        "ON personal_requests(kind,matcher,updated)",
        "CREATE INDEX IF NOT EXISTS personal_requests_updated ON personal_requests(updated)",
        """CREATE TABLE IF NOT EXISTS personal_request_keywords (
            token TEXT NOT NULL,request_id INTEGER NOT NULL,revision INTEGER NOT NULL,
            PRIMARY KEY(token,request_id))""",
        "CREATE INDEX IF NOT EXISTS personal_request_keywords_request "
        "ON personal_request_keywords(request_id)",
        """CREATE TRIGGER IF NOT EXISTS personal_request_keyword_insert
            AFTER INSERT ON personal_requests BEGIN
            INSERT INTO personal_request_keywords(token,request_id,revision)
            SELECT value,NEW.id,NEW.revision
            FROM json_each(CASE WHEN NEW.kind='keywords' THEN NEW.matcher ELSE '[]' END);
            END""",
        """CREATE TRIGGER IF NOT EXISTS personal_request_keyword_update
            AFTER UPDATE OF kind,matcher,revision ON personal_requests BEGIN
            DELETE FROM personal_request_keywords WHERE request_id=NEW.id;
            INSERT INTO personal_request_keywords(token,request_id,revision)
            SELECT value,NEW.id,NEW.revision
            FROM json_each(CASE WHEN NEW.kind='keywords' THEN NEW.matcher ELSE '[]' END);
            END""",
        """CREATE TRIGGER IF NOT EXISTS personal_request_keyword_delete
            AFTER DELETE ON personal_requests BEGIN
            DELETE FROM personal_request_keywords WHERE request_id=OLD.id;
            END""",
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
    alert_columns = {row[1] for row in db.execute("PRAGMA table_info(personal_request_alerts)")}
    if "backlog" not in alert_columns:
        db.execute(
            "ALTER TABLE personal_request_alerts ADD COLUMN backlog INTEGER NOT NULL DEFAULT 0"
        )
    if "catchup" not in alert_columns:
        # JSON {group,part,parts,rank,shown,total}: this older match's place in
        # a catch-up DM, fixed when the catch-up is first claimed.
        db.execute("ALTER TABLE personal_request_alerts ADD COLUMN catchup TEXT")
    match_columns = {row[1] for row in db.execute("PRAGMA table_info(personal_request_matches)")}
    if "via" not in match_columns:
        # 'keywords' (title match, any scan lane) or 'search' (Walmart returned
        # the item for this request's own search). Send-time checks differ.
        db.execute(
            "ALTER TABLE personal_request_matches ADD COLUMN via TEXT NOT NULL DEFAULT 'keywords'"
        )
    columns = {row[1] for row in db.execute("PRAGMA table_info(personal_requests)")}
    if "min_value_cents" not in columns:
        # Existing watches stay unfiltered; upgrades share the caller's intake
        # transaction and never manufacture historical notifications.
        db.execute("ALTER TABLE personal_requests ADD COLUMN min_value_cents INTEGER")
    indexed = db.execute(
        "SELECT value FROM personal_request_settings WHERE key='matching_index_version'"
    ).fetchone()
    if indexed is None:
        # Upgrade old watches in the same transaction as the schema marker.
        # No separate startup seed, commit, or historical observation is needed.
        db.execute("DELETE FROM personal_request_keywords")
        db.execute(
            """INSERT INTO personal_request_keywords(token,request_id,revision)
            SELECT token.value,r.id,r.revision FROM personal_requests r,
            json_each(CASE WHEN r.kind='keywords' THEN r.matcher ELSE '[]' END) token"""
        )
        db.execute("INSERT INTO personal_request_settings VALUES ('matching_index_version','1')")


def _stem(word):
    """Light, symmetric plural folding: chairs/chair, boxes/box, batteries/battery."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _terms(title):
    """Title words, their stems, and adjacent joins (e-bike/e bike -> ebike)."""
    words = _words(title)
    terms = {_stem(word) for word in words}
    terms.update(_stem(first + second) for first, second in zip(words, words[1:], strict=False))
    return terms


def _request_forms(query):
    """A keyword watch matches if every word is present, or its joined form is."""
    words = _words(query)
    forms = [frozenset(_stem(word) for word in words)]
    if 2 <= len(words) <= 3:
        forms.append(frozenset({_stem("".join(words))}))
    return forms


def _search_key(query):
    return " ".join(_words(query)) if isinstance(query, str) else ""


def _value_ok(request, item, *, allow_unknown):
    minimum = request.get("min_value_cents")
    if minimum is None:
        return True
    value = item.get("value_cents")
    return allow_unknown if value is None else value >= minimum


def _related(request, terms):
    """Walmart search is loose (a "NAS" search returns Ethernet cables), so a
    search-only match must still share one significant word with the watch,
    whole or inside a title word (bike -> ebike/motorbike)."""
    significant = [_stem(word) for word in _words(request["query"]) if len(word) >= 3]
    return any(word in term for word in significant for term in terms)


def _unknown_value_ok(item):
    """About a third of cards show no value. A product listing still alerts
    (flagged in the DM); colour/size "variant on" rows of it do not, since
    those are mostly accessories and the parent listing alerts on its own."""
    return ", variant on " not in item.get("title", "")


def _matches(request, item, *, terms=None, via="keywords"):
    """Keyword/exact matches may accept a listing that shows no value; a
    search-only match needs a shown value meeting the member's minimum."""
    if request["kind"] == "exact":
        return request["matcher"] == item["item_id"] and _value_ok(
            request, item, allow_unknown=True
        )
    terms = _terms(item["title"]) if terms is None else terms
    if via == "search":
        return _value_ok(request, item, allow_unknown=False) and _related(request, terms)
    return _value_ok(request, item, allow_unknown=_unknown_value_ok(item)) and any(
        form <= terms for form in _request_forms(request["query"])
    )


def _catchup(value):
    """A stored catch-up position, or None for an unplaced/legacy row."""
    if isinstance(value, dict):
        return value
    try:
        meta = json.loads(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return meta if isinstance(meta, dict) else None


def _value_order(job):
    """Highest shown value first; listings without a value last."""
    value = job["item"].get("value_cents")
    return (value is None, -(value or 0), job["created"], job["id"])


def _activity_row(activity, request, user_id):
    return activity.setdefault(
        request["id"],
        {
            "request_id": request["id"],
            "member_ref": member_ref(user_id),
            "matched": 0,
            "queued": 0,
            "deduplicated": 0,
            "held": 0,
            "search_matched": 0,
            "below_minimum": 0,
            "unavailable": 0,
            "item_ids": [],
            "near_item_ids": [],
        },
    )


def _near_miss(activity, request, reason, item_id):
    """Record why a watch's text matched but no alert was queued."""
    if activity is None:
        return
    row = _activity_row(activity, request, request["user_id"])
    row[reason] += 1
    if item_id not in row["near_item_ids"] and len(row["near_item_ids"]) < 10:
        row["near_item_ids"].append(item_id)


def match_requests(
    db, normalized_items, *, now, seed_only=False, activity=None, search_queries=None
):
    """Match only this freshly accepted observation inside its intake transaction.

    Existing product IDs, including previously seeded IDs, remain eligible when
    they are freshly reobserved. Seed/import batches themselves never alert.
    Returns the number of newly queued member/product pairs.
    """
    if (
        db.execute("SELECT 1 FROM personal_requests WHERE updated<=? LIMIT 1", (now,)).fetchone()
        is None
    ):
        return 0
    now = _now(now)
    db.execute(
        "UPDATE personal_request_alerts SET state='expired',reason='stale' "
        "WHERE state IN ('pending','leased') AND expires<=?",
        (now,),
    )
    queued = 0
    # Load keyword watches once per batch and index each match form by one of
    # its terms; an item only checks watches sharing at least one title term.
    by_term, by_search = {}, {}
    for request in _all(
        db, "SELECT * FROM personal_requests WHERE kind='keywords' AND updated<=?", (now,)
    ):
        for form in _request_forms(request["query"]):
            by_term.setdefault(min(form), []).append((request, form))
        by_search.setdefault(_search_key(request["query"]), []).append(request)
    search_queries = search_queries or {}
    gathering = set()  # Members with a new or re-armed older match in this batch.
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
            if activity is not None and not seed_only:
                # Out of stock: log which watches it would have matched.
                terms = _terms(item["title"])
                for term in terms:
                    for request, form in by_term.get(term, ()):
                        if form <= terms:
                            _near_miss(activity, request, "unavailable", item_id)
            continue
        found = {}
        for request in _all(
            db,
            "SELECT * FROM personal_requests WHERE kind='exact' AND matcher=? AND updated<=?",
            (item_id, now),
        ):
            if _matches(request, item):
                found[request["id"]] = (request, "keywords")
        terms = _terms(item["title"])
        for term in terms:
            for request, form in by_term.get(term, ()):
                if request["id"] in found or not form <= terms:
                    continue
                if _value_ok(request, item, allow_unknown=_unknown_value_ok(item)):
                    found[request["id"]] = (request, "keywords")
                else:
                    _near_miss(activity, request, "below_minimum", item_id)
        # Walmart returned this item for a watch's own search (requests lane,
        # or any lane that ran the same query): trust the search's relevance.
        for request in by_search.get(_search_key(search_queries.get(item_id)), ()):
            if request["id"] in found:
                continue
            if _matches(request, item, via="search"):
                found[request["id"]] = (request, "search")
            else:
                _near_miss(activity, request, "below_minimum", item_id)
        by_user = {}
        for request, via in found.values():
            by_user.setdefault(request["user_id"], []).append({**request, "_via": via})
        try:
            seen = db.execute("SELECT first_seen FROM items WHERE item_id=?", (item_id,)).fetchone()
        except sqlite3.OperationalError:  # Standalone request databases have no item log.
            seen = None
        for user_id, matches in by_user.items():
            # Listed before every matching request existed: an older match.
            backlog = int(seen is not None and seen[0] < min(r["created"] for r in matches))
            due = now + BACKLOG_GATHER_SECONDS if backlog else now
            alert_id = hashlib.sha256(f"request-v1:{user_id}:{item_id}".encode()).hexdigest()[:32]
            nonce = str(int(hashlib.sha256(alert_id.encode()).hexdigest()[:20], 16))
            inserted = db.execute(
                """INSERT OR IGNORE INTO personal_request_alerts
                (id,user_id,item_id,payload,observed,created,expires,next_attempt,nonce,backlog)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    alert_id,
                    user_id,
                    item_id,
                    encoded,
                    now,
                    now,
                    now + ALERT_LIFETIME_SECONDS,
                    due,
                    nonce,
                    backlog,
                ),
            ).rowcount
            queued += inserted
            state, older = db.execute(
                "SELECT state,backlog FROM personal_request_alerts WHERE id=?", (alert_id,)
            ).fetchone()
            previous_state = state
            if state in {"cancelled", "expired", "failed"}:
                # These states are definitely unsent. A new eligible live
                # observation can try again; sent, sending and review remain
                # protected permanently against an unknown duplicate DM.
                db.execute("DELETE FROM personal_request_matches WHERE alert_id=?", (alert_id,))
                db.execute(
                    """UPDATE personal_request_alerts SET payload=?,observed=?,created=?,expires=?,
                    state='pending',next_attempt=?,attempts=0,lease_token=NULL,lease_until=NULL,
                    channel_id=NULL,message_id=NULL,reason=NULL,nonce=?,backlog=?,catchup=NULL
                    WHERE id=?""",
                    (
                        encoded,
                        now,
                        now,
                        now + ALERT_LIFETIME_SECONDS,
                        due,
                        str(secrets.randbits(80)),
                        backlog,
                        alert_id,
                    ),
                )
                state = "pending"
                queued += 1
            if backlog and (inserted or state != previous_state):
                gathering.add(user_id)
            if activity is not None:
                for request in matches:
                    row = _activity_row(activity, request, user_id)
                    row["matched"] += 1
                    row["search_matched"] += int(request["_via"] == "search")
                    if len(row["item_ids"]) < 10:
                        row["item_ids"].append(item_id)
                    row["queued"] += int(
                        bool(inserted) or previous_state in {"cancelled", "expired", "failed"}
                    )
                    row["deduplicated"] += int(previous_state == "sent")
                    row["held"] += int(previous_state in {"sending", "review"})
            # Sent or skipped older matches never re-arm, but keep their current
            # associations so /request listed still finds them after an edit.
            if state not in {"pending", "leased"} and not (
                older and state in {"sent", "skipped"}
            ):
                continue
            for request in matches:
                db.execute(
                    "INSERT OR REPLACE INTO personal_request_matches"
                    "(alert_id,request_id,revision,via) VALUES (?,?,?,?)",
                    (alert_id, request["id"], request["revision"], request["_via"]),
                )
    for user_id in gathering:
        # Debounce: each new older match moves this member's whole unplaced
        # catch-up to one shared due time, so later search waves join it.
        first = db.execute(
            "SELECT MIN(created) FROM personal_request_alerts WHERE user_id=? AND backlog=1 "
            "AND catchup IS NULL AND state='pending'",
            (user_id,),
        ).fetchone()[0]
        if first is not None:
            db.execute(
                "UPDATE personal_request_alerts SET next_attempt=? WHERE user_id=? AND backlog=1 "
                "AND catchup IS NULL AND state='pending'",
                (
                    min(now + BACKLOG_GATHER_SECONDS, first + BACKLOG_MAX_GATHER_SECONDS),
                    user_id,
                ),
            )
    return queued


class RequestStore:
    # Set by the runtime: wakes the channel worker after a DM-first hold lifts.
    on_release = None

    def _release_hold(self, item_id, *, force=False):
        """Let Pro posts for this item go now that its request DMs settled."""
        if self.on_release is None or not isinstance(item_id, str):
            return
        now = time.time()
        with self._db() as db:
            released = db.execute(
                """UPDATE jobs SET next_attempt=? WHERE item_id=? AND state='pending'
                AND attempts=0 AND batch_id IS NULL AND next_attempt>? AND route!='yesterday-recap'
                AND (? OR NOT EXISTS (SELECT 1 FROM personal_request_alerts a
                    JOIN personal_request_users u USING(user_id)
                    WHERE a.item_id=? AND a.state IN ('pending','leased','sending')
                    AND u.dm_status='ready'))""",
                (now, item_id, now, int(force), item_id),
            ).rowcount
        if released:
            self.on_release()

    def __init__(self, path: Path | str, *, guild_id=None, event_sink=None):
        self.path = Path(path)
        self.event_sink = event_sink
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

    def _changed(self, action, user_id, row):
        _, kind, matcher = parse_query(row["query"])
        emit_event(
            self.event_sink,
            "request_changed",
            audience="personal",
            action=action,
            request_id=int(row["id"]),
            revision=row["revision"],
            member_ref=member_ref(user_id),
            query_ref=hashlib.sha256(f"{kind}:{matcher}".encode()).hexdigest()[:32],
            min_value_cents=row.get("min_value_cents") or 0,
        )

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _validate_add(
        db, user_id, queries, *, reuse_existing=False, minimum=None, minimum_specified=False
    ):
        existing = _all(
            db,
            "SELECT id,kind,matcher,min_value_cents FROM personal_requests WHERE user_id=?",
            (user_id,),
        )
        identities = {(row["kind"], row["matcher"]): row for row in existing}
        additions = 0
        for _, kind, matcher in queries:
            previous = identities.get((kind, matcher))
            if previous is None:
                additions += 1
            elif not reuse_existing:
                raise ValueError("You already have this request. Use /request list to manage it.")
            elif minimum_specified and previous["min_value_cents"] != minimum:
                raise ValueError(
                    "An existing request has a different minimum value. "
                    "Use /request edit to change it; no requests from this batch were added."
                )
        return identities

    def validate_add(self, user_id, query_text, *, min_value=None):
        user_id = _user(user_id)
        queries, minimum, specified = _parse_input(query_text, min_value, bulk=True)
        with self._db() as db:
            self._validate_add(
                db,
                user_id,
                queries,
                reuse_existing=True,
                minimum=minimum,
                minimum_specified=specified,
            )

    def _add(self, user_id, query_text, *, min_value=None, now=None, bulk):
        user_id, now = _user(user_id), _now(now)
        queries, minimum, specified = _parse_input(query_text, min_value, bulk=bulk)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = self._validate_add(
                db,
                user_id,
                queries,
                reuse_existing=bulk,
                minimum=minimum,
                minimum_specified=specified,
            )
            db.execute(
                "INSERT OR IGNORE INTO personal_request_users(user_id) VALUES (?)", (user_id,)
            )
            rows = []
            for query, kind, matcher in queries:
                previous = existing.get((kind, matcher))
                if previous is not None:
                    row = self._request(db, user_id, previous["id"])
                    rows.append({**row, "already_saved": True})
                    continue
                cursor = db.execute(
                    """INSERT INTO personal_requests
                    (user_id,query,kind,matcher,created,updated,min_value_cents)
                    VALUES (?,?,?,?,?,?,?)""",
                    (user_id, query, kind, matcher, now, now, minimum),
                )
                rows.append(self._request(db, user_id, cursor.lastrowid))
        for row in rows:
            if not row.get("already_saved"):
                self._changed("add", user_id, row)
        return rows

    def add(self, user_id, query, *, min_value=None, now=None):
        return self._add(user_id, query, min_value=min_value, now=now, bulk=False)[0]

    def add_many(self, user_id, query_text, *, min_value=None, now=None):
        return self._add(user_id, query_text, min_value=min_value, now=now, bulk=True)

    @staticmethod
    def _request(db, user_id, request_id):
        row = _one(
            db,
            "SELECT id,query,kind,revision,created,updated,min_value_cents FROM personal_requests "
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

    def edit(self, user_id, request_id, query, *, min_value=None, now=None):
        user_id, request_id, now = _user(user_id), _request_id(request_id), _now(now)
        queries, minimum, specified = _parse_input(query, min_value, bulk=False)
        query, kind, matcher = queries[0]
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self._request(db, user_id, request_id)
            if not specified:
                minimum = previous["min_value_cents"]
            if db.execute(
                "SELECT 1 FROM personal_requests "
                "WHERE user_id=? AND kind=? AND matcher=? AND id!=?",
                (user_id, kind, matcher, request_id),
            ).fetchone():
                raise ValueError("You already have this request. Use /request list to manage it.")
            db.execute(
                """UPDATE personal_requests SET query=?,kind=?,matcher=?,min_value_cents=?,
                revision=revision+1,updated=?
                WHERE user_id=? AND id=?""",
                (query, kind, matcher, minimum, now, user_id, request_id),
            )
            db.execute("DELETE FROM personal_request_matches WHERE request_id=?", (request_id,))
            self._cancel_orphans(db)
            row = self._request(db, user_id, request_id)
        self._changed("edit", user_id, row)
        return row

    def delete(self, user_id, request_id):
        user_id, request_id = _user(user_id), _request_id(request_id)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = _one(
                db,
                "SELECT * FROM personal_requests WHERE user_id=? AND id=?",
                (user_id, request_id),
            )
            deleted = db.execute(
                "DELETE FROM personal_requests WHERE user_id=? AND id=?", (user_id, request_id)
            ).rowcount
            if deleted:
                db.execute("DELETE FROM personal_request_matches WHERE request_id=?", (request_id,))
                self._cancel_orphans(db)
        if deleted:
            self._changed("delete", user_id, row)
        return bool(deleted)

    def delete_many(self, user_id, selectors):
        """Delete several own requests named by ID or by their exact saved text.

        Returns (deleted rows, selectors that matched none of this member's requests).
        """
        user_id = _user(user_id)
        own = self.list(user_id)
        by_id = {str(row["id"]): row for row in own}
        by_text = {}
        for row in own:
            by_text.setdefault(" ".join(row["query"].casefold().split()), []).append(row)
        chosen, missing = {}, []
        for selector in selectors:
            key = " ".join(str(selector).casefold().split())
            rows = [by_id[key]] if key in by_id else by_text.get(key, [])
            if not rows:
                missing.append(str(selector).strip())
            for row in rows:
                chosen[row["id"]] = row
        deleted = [row for request_id, row in chosen.items() if self.delete(user_id, request_id)]
        return deleted, missing

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
        emit_event(
            self.event_sink,
            "request_dm_status",
            audience="personal",
            member_ref=member_ref(user_id),
            dm_status=status,
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
            """SELECT r.*,m.via FROM personal_request_matches m
            JOIN personal_requests r ON r.id=m.request_id
            WHERE m.alert_id=? AND m.revision=r.revision AND r.user_id=? AND r.updated<=?
            ORDER BY r.id""",
            (row["id"], row["user_id"], row["observed"]),
        )
        terms = _terms(item["title"])
        matches = [
            request for request in requests
            if _matches(request, item, terms=terms, via=request["via"])
        ]
        if not matches:
            return None
        return {
            **row,
            "catchup": _catchup(row.get("catchup")),
            "item": item,
            "queries": [request["query"] for request in matches],
            "search_queries": [request["query"] for request in matches
                               if request["via"] == "search"],
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
                ORDER BY a.backlog,a.next_attempt,a.created,a.id LIMIT 20""",
                (now, now, now),
            )
            for row in rows:
                if not self._lease(db, row, secrets.token_hex(16), now + LEASE_SECONDS, now):
                    continue  # An earlier catch-up in this loop already placed it.
                hydrated = self._hydrate(db, row, now)
                if hydrated:
                    if row.get("backlog"):
                        return self._claim_catchup(db, hydrated, now)
                    return hydrated
                self._cancel_unmatched(db, row)
            return None

    @staticmethod
    def _lease(db, row, token, until, now):
        """Lease one due row; False if a concurrent step already moved it on."""
        changed = db.execute(
            "UPDATE personal_request_alerts SET state='leased',lease_token=?,lease_until=? "
            "WHERE id=? AND (state='pending' OR (state='leased' AND lease_until<=?))",
            (token, until, row["id"], now),
        ).rowcount
        if changed:
            row.update(state="leased", lease_token=token, lease_until=until)
        return bool(changed)

    @staticmethod
    def _cancel_unmatched(db, row):
        db.execute(
            "UPDATE personal_request_alerts SET state='cancelled',"
            "reason='no_current_match' WHERE id=? AND lease_token=?",
            (row["id"], row["lease_token"]),
        )

    def _claim_catchup(self, db, first, now):
        """Lease exactly one catch-up DM's rows under the first row's token.

        A new catch-up ranks this member's due, unplaced older matches by value,
        keeps the BUNDLE_LIMIT highest, packs them into as few DMs as pass every
        Discord limit and records each row's place. The rest are skipped: the DM
        points to /request listed for them. A placed row brings back exactly
        its own part, so a retry renders the same DM with the same nonce, and
        settle/retry of the primary job applies to every leased row.
        """
        token, until = first["lease_token"], first["lease_until"]
        meta = first.get("catchup")
        if meta and meta.get("group"):
            rows = _all(
                db,
                """SELECT * FROM personal_request_alerts WHERE user_id=? AND id!=?
                AND backlog=1 AND json_extract(catchup,'$.group')=?
                AND expires>? AND next_attempt<=?
                AND (state='pending' OR (state='leased' AND lease_until<=?))""",
                (first["user_id"], first["id"], meta["group"], now, now, now),
            )
            parts = {}
            for row in rows:
                parts.setdefault((_catchup(row["catchup"]) or {}).get("part", 1), []).append(row)
            parts.setdefault(meta.get("part", 1), []).append(first)
            # Lowest due part first, so continuation DMs keep their order.
            for number in sorted(parts):
                members = []
                for row in parts[number]:
                    if row is first:
                        members.append(first)
                    elif self._lease(db, row, token, until, now):
                        hydrated = self._hydrate(db, row, now)
                        if hydrated:
                            members.append(hydrated)
                        else:
                            self._cancel_unmatched(db, row)
                if not members:
                    continue
                if all(member is not first for member in members):
                    db.execute(
                        "UPDATE personal_request_alerts SET state='pending',lease_token=NULL,"
                        "lease_until=NULL WHERE id=? AND lease_token=?",
                        (first["id"], token),
                    )
                members.sort(key=lambda job: (job.get("catchup") or {}).get("rank", 0))
                return {**members[0], "bundle": members[1:]}
        due = """user_id=? AND backlog=1 AND catchup IS NULL AND expires>? AND next_attempt<=?
            AND (state='pending' OR (state='leased' AND lease_until<=?))"""
        rows = _all(
            db,
            f"""SELECT * FROM personal_request_alerts WHERE {due} AND id!=?
            ORDER BY json_extract(payload,'$.value_cents') IS NULL,
            json_extract(payload,'$.value_cents') DESC,created,id LIMIT ?""",
            (first["user_id"], now, now, now, first["id"], CATCHUP_SCAN_LIMIT - 1),
        )
        candidates = [first]
        for row in rows:
            if not self._lease(db, row, token, until, now):
                continue
            hydrated = self._hydrate(db, row, now)
            if hydrated:
                candidates.append(hydrated)
            else:
                self._cancel_unmatched(db, row)
        # Anything past the scan bound has a lower value than every scanned
        # row: count it in this catch-up and skip it, never a second DM.
        beyond = db.execute(
            f"SELECT COUNT(*) FROM personal_request_alerts WHERE {due}",
            (first["user_id"], now, now, now),
        ).fetchone()[0]
        total = len(candidates) + beyond
        candidates.sort(key=_value_order)
        shown, skipped = candidates[:BUNDLE_LIMIT], candidates[BUNDLE_LIMIT:]
        group = secrets.token_hex(8)
        if beyond:
            db.execute(
                f"""UPDATE personal_request_alerts SET state='skipped',reason='catchup_overflow',
                lease_token=NULL,lease_until=NULL,catchup=? WHERE {due}""",
                (json.dumps({"group": group, "total": total}), first["user_id"], now, now, now),
            )
        for rank, job in enumerate(shown, 1):
            job["catchup"] = {
                "group": group, "rank": rank, "shown": len(shown), "total": total
            }
        try:
            packed = pack_catchup(shown)
        except Exception:
            # Never let one unrenderable listing stall every member's DMs; the
            # sender still validates (and splits or fails) each DM before a POST.
            packed = [[job] for job in shown]
        for number, members in enumerate(packed, 1):
            for job in members:
                job["catchup"].update(part=number, parts=len(packed))
                encoded = json.dumps(job["catchup"], separators=(",", ":"))
                if number == 1:
                    db.execute(
                        "UPDATE personal_request_alerts SET catchup=? WHERE id=?",
                        (encoded, job["id"]),
                    )
                else:
                    # Each later part is its own DM, claimed next with its own nonce.
                    db.execute(
                        "UPDATE personal_request_alerts SET catchup=?,state='pending',"
                        "lease_token=NULL,lease_until=NULL,next_attempt=? WHERE id=?",
                        (encoded, now, job["id"]),
                    )
        for job in skipped:
            db.execute(
                "UPDATE personal_request_alerts SET state='skipped',reason='catchup_overflow',"
                "lease_token=NULL,lease_until=NULL,catchup=? WHERE id=?",
                (json.dumps({"group": group, "total": total}), job["id"]),
            )
        members = packed[0]
        return {**members[0], "bundle": members[1:]}

    def _current(self, db, job, now):
        """The job's rows that still apply, in DM order; cancel only those that don't.

        One sold-out or edited-away match never drops the rest of a catch-up:
        the first row still valid leads the DM. Rows another step already moved
        on (or whose lease ran out) are left alone.
        """
        members, dropped = [], []
        for member in [job, *(job.get("bundle") or ())]:
            row = _one(
                db,
                "SELECT * FROM personal_request_alerts WHERE id=? AND lease_token=?",
                (member["id"], job["lease_token"]),
            )
            if row is None or row["state"] != "leased":
                continue
            snapshot = self._hydrate(db, row, now)
            if snapshot is not None:
                members.append(snapshot)
            elif row["lease_until"] > now and row["expires"] > now:
                db.execute(
                    "UPDATE personal_request_alerts SET state='cancelled',"
                    "reason='no_current_match' WHERE id=? AND lease_token=? AND state='leased'",
                    (row["id"], job["lease_token"]),
                )
                dropped.append(row["item_id"])
        return members, dropped

    @staticmethod
    def _lead(job, members):
        head = members[0]
        return {**head, "bundle": members[1:]} if "bundle" in job else head

    @staticmethod
    def _bundle_ids(job):
        return [job["id"], *(member["id"] for member in job.get("bundle") or ())]

    def validate(self, job, *, now=None):
        now = _now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            members, dropped = self._current(db, job, now)
        for item_id in dropped:
            self._release_hold(item_id)
        return self._lead(job, members) if members else None

    def begin_send(self, job, *, now=None, channel_id=None):
        """Commit possible submission and return its exact validated snapshot."""
        now = _now(now)
        if channel_id is not None:
            _user(channel_id)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            members, dropped = self._current(db, job, now)
            channel_id = channel_id or (members[0].get("channel_id") if members else None)
            for snapshot in members:
                db.execute(
                    "UPDATE personal_request_alerts SET state='sending',attempts=attempts+1,"
                    "channel_id=? WHERE id=? AND lease_token=? AND state='leased'",
                    (channel_id, snapshot["id"], job["lease_token"]),
                )
                snapshot.update(
                    state="sending", attempts=snapshot["attempts"] + 1, channel_id=channel_id
                )
        for item_id in dropped:
            self._release_hold(item_id)
        return self._lead(job, members) if members else None

    def settle(self, job, state, *, now=None, reason=None, message_id=None):
        _now(now)
        if state not in {"sent", "failed", "cancelled", "review"}:
            raise ValueError("Invalid personal delivery settlement.")
        if message_id is not None:
            _user(message_id)
        ids = self._bundle_ids(job)
        with self._db() as db:
            settled = bool(
                db.execute(
                    f"""UPDATE personal_request_alerts SET state=?,reason=?,message_id=?
                WHERE id IN ({",".join("?" * len(ids))}) AND lease_token=?
                AND state IN ('leased','sending')""",
                    (state, reason, message_id, *ids, job["lease_token"]),
                ).rowcount
            )
        self._release_hold(job.get("item_id"))
        return settled

    def retry(self, job, *, now=None, delay=5, reason="remote_retry"):
        now = _now(now)
        if type(delay) not in (int, float) or not math.isfinite(delay):
            delay = 5
        ids = self._bundle_ids(job)
        with self._db() as db:
            retried = bool(
                db.execute(
                    f"""UPDATE personal_request_alerts SET state='pending',next_attempt=?,reason=?,
                lease_token=NULL,lease_until=NULL
                WHERE id IN ({",".join("?" * len(ids))}) AND lease_token=?
                AND state IN ('leased','sending')""",
                    (now + max(0.05, delay), reason, *ids, job["lease_token"]),
                ).rowcount
            )
        # A DM that must wait never holds the public post back.
        self._release_hold(job.get("item_id"), force=True)
        return retried

    def split(self, job, *, now=None, reason="payload_too_large"):
        """Discord refused this DM's size, or would: never send that body again.

        A single item fails with the given reason. A bundle is halved into two
        consecutive parts of its catch-up (later unsent parts move back one
        place) and requeued; the next claims send the smaller DMs, each with its
        own nonce. Returns "split", "failed" or "stale".
        """
        now = _now(now)
        ids = self._bundle_ids(job)
        position = {alert_id: index for index, alert_id in enumerate(ids)}
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = _all(
                db,
                f"""SELECT * FROM personal_request_alerts WHERE id IN ({",".join("?" * len(ids))})
                AND lease_token=? AND state IN ('leased','sending')""",
                (*ids, job["lease_token"]),
            )
            if not rows:
                return "stale"
            if len(rows) == 1:
                db.execute(
                    "UPDATE personal_request_alerts SET state='failed',reason=? "
                    "WHERE id=? AND lease_token=?",
                    (reason, rows[0]["id"], job["lease_token"]),
                )
                outcome = "failed"
            else:
                for row in rows:
                    row["catchup"] = _catchup(row["catchup"]) or {}
                rows.sort(key=lambda row: (row["catchup"].get("rank", 0), position[row["id"]]))
                first = rows[0]["catchup"]
                group = first.get("group") or secrets.token_hex(8)
                part, parts = first.get("part") or 1, first.get("parts") or 1
                for row in _all(
                    db,
                    """SELECT id,catchup FROM personal_request_alerts WHERE user_id=?
                    AND json_extract(catchup,'$.group')=? AND state IN ('pending','leased')""",
                    (rows[0]["user_id"], group),
                ):
                    if row["id"] in position:
                        continue
                    later = _catchup(row["catchup"]) or {}
                    later["parts"] = parts + 1
                    if later.get("part", 1) > part:
                        later["part"] = later.get("part", 1) + 1
                    db.execute(
                        "UPDATE personal_request_alerts SET catchup=? WHERE id=?",
                        (json.dumps(later, separators=(",", ":")), row["id"]),
                    )
                half = (len(rows) + 1) // 2
                for index, row in enumerate(rows):
                    meta = {
                        "rank": index + 1, "shown": len(rows), "total": len(rows),
                        **row["catchup"],
                        "group": group, "part": part + (index >= half), "parts": parts + 1,
                    }
                    db.execute(
                        """UPDATE personal_request_alerts SET state='pending',next_attempt=?,
                        reason=?,lease_token=NULL,lease_until=NULL,catchup=?
                        WHERE id=? AND lease_token=?""",
                        (now, reason, json.dumps(meta, separators=(",", ":")), row["id"],
                         job["lease_token"]),
                    )
                outcome = "split"
        self._release_hold(job.get("item_id"), force=outcome == "split")
        return outcome

    def reconcile_candidate(self, *, now=None):
        """The oldest parked possibly-sent DM that one history read can settle.

        Returns its rows' shared lease, DM channel, a time no later than the
        POST, and the items it carried; None when nothing is waiting.
        """
        now = _now(now)
        reasons = ",".join("?" * len(RECONCILE_REASONS))
        with self._db() as db:
            head = _one(
                db,
                f"""SELECT user_id,lease_token,lease_until,channel_id FROM personal_request_alerts
                WHERE state='review' AND reason IN ({reasons}) AND lease_token IS NOT NULL
                AND channel_id IS NOT NULL AND lease_until<=? ORDER BY lease_until,id LIMIT 1""",
                (*RECONCILE_REASONS, now - RECONCILE_DELAY_SECONDS),
            )
            if head is None:
                return None
            rows = _all(
                db,
                f"""SELECT id,item_id,payload FROM personal_request_alerts WHERE state='review'
                AND reason IN ({reasons}) AND lease_token=? AND user_id=? AND channel_id=?
                ORDER BY id""",
                (*RECONCILE_REASONS, head["lease_token"], head["user_id"], head["channel_id"]),
            )
        items = []
        for row in rows:
            try:
                items.append(json.loads(row["payload"]))
            except ValueError:
                items.append({"item_id": row["item_id"]})
        return {
            "user_id": head["user_id"],
            "lease_token": head["lease_token"],
            "channel_id": head["channel_id"],
            # Claimed (and so sent) no earlier than its lease began.
            "since": head["lease_until"] - LEASE_SECONDS,
            "ids": [row["id"] for row in rows],
            "items": items,
        }

    def reconcile(self, candidate, outcome, *, message_id=None):
        """Settle a parked DM from history: 'sent', 'undelivered' or 'unresolved'."""
        state, reason = {
            "sent": ("sent", "reconciled_sent"),
            "undelivered": ("failed", "reconciled_undelivered"),
            "unresolved": ("review", "reconcile_unresolved"),
        }[outcome]
        if message_id is not None:
            _user(message_id)
        ids = candidate["ids"]
        if not ids:
            return 0
        with self._db() as db:
            return db.execute(
                f"""UPDATE personal_request_alerts SET state=?,reason=?,
                message_id=COALESCE(?,message_id)
                WHERE id IN ({",".join("?" * len(ids))}) AND lease_token=? AND state='review'
                AND reason IN ({",".join("?" * len(RECONCILE_REASONS))})""",
                (state, reason, message_id, *ids, candidate["lease_token"], *RECONCILE_REASONS),
            ).rowcount

    def listed(self, user_id, *, now=None, request_id=None, min_value=None):
        """This member's older matches that are still listed, highest value first.

        "Older" means listed before the matching request existed: the catch-up
        DM shows at most BUNDLE_LIMIT of them. The collector's item log, when
        this store shares its database, supplies the latest observation.
        Optionally only one of the member's requests, and only listings
        showing at least min_value dollars.
        """
        user_id, now = _user(user_id), _now(now)
        minimum = _minimum_cents(min_value)
        only = ""
        parameters = (user_id, now - LISTED_FRESH_SECONDS)
        if request_id is not None:
            only = " AND r.id=?"
            parameters += (_request_id(request_id),)
        columns = """a.id,a.state,a.created,r.query,r.kind,r.matcher,r.min_value_cents,m.via"""
        joins = """FROM personal_request_alerts a
            JOIN personal_request_matches m ON m.alert_id=a.id
            JOIN personal_requests r ON r.id=m.request_id AND r.revision=m.revision
            AND r.user_id=a.user_id"""
        with self._db() as db:
            try:
                rows = _all(
                    db,
                    f"""SELECT {columns},COALESCE(i.payload,a.payload) AS latest,
                    COALESCE(i.last_seen,a.observed) AS seen {joins}
                    LEFT JOIN items i ON i.item_id=a.item_id
                    WHERE a.user_id=? AND a.backlog=1 AND COALESCE(i.last_seen,a.observed)>=?
                    {only} ORDER BY a.id,r.id""",
                    parameters,
                )
            except sqlite3.OperationalError:  # Standalone request databases have no item log.
                rows = _all(
                    db,
                    f"""SELECT {columns},a.payload AS latest,a.observed AS seen {joins}
                    WHERE a.user_id=? AND a.backlog=1 AND a.observed>=?{only}
                    ORDER BY a.id,r.id""",
                    parameters,
                )
        found = {}
        for row in rows:
            entry = found.get(row["id"])
            if entry is None:
                item = json.loads(row["latest"])
                entry = found[row["id"]] = {
                    "id": row["id"],
                    "created": row["created"],
                    "state": row["state"],
                    "seen": row["seen"],
                    "item": item,
                    "queries": [],
                    "terms": _terms(item["title"]) if item.get("available") is True else None,
                }
            if entry["terms"] is not None and _matches(
                row, entry["item"], terms=entry["terms"], via=row["via"]
            ):
                entry["queries"].append(row["query"])
        result = []
        for entry in found.values():
            if entry.pop("terms") is not None and entry["queries"] and (
                minimum is None or (entry["item"].get("value_cents") or 0) >= minimum
            ):
                result.append(entry)
        result.sort(key=_value_order)
        return result

    def search_queries(self):
        """One reviewer search per distinct watch; never export member identities.

        Minimum values remain delivery filters, not catalogue filters. Preserve
        natural word order for Walmart while sharing equivalent AND watches.
        """
        queries = {}
        with self._db() as db:
            for kind, matcher, query in db.execute(
                "SELECT kind,matcher,query FROM personal_requests ORDER BY id"
            ):
                key = (kind, matcher)
                if key not in queries:
                    queries[key] = (
                        matcher.removeprefix("ip-") if kind == "exact" else " ".join(_words(query))
                    )
        return sorted(set(queries.values()))

    def owners(self, *, limit=50, after=None):
        """Bounded membership sweep; saved watches cannot outlive Pro access."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Invalid request-owner page size.")
        after = _user(after) if after is not None else "0"
        with self._db() as db:
            return [row[0] for row in db.execute(
                "SELECT DISTINCT user_id FROM personal_requests WHERE user_id>? "
                "ORDER BY user_id LIMIT ?", (after, limit),
            )]

    def revoke_user(self, user_id):
        user_id = _user(user_id)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            removed = _all(db, "SELECT * FROM personal_requests WHERE user_id=?", (user_id,))
            db.execute(
                "DELETE FROM personal_request_matches WHERE request_id IN "
                "(SELECT id FROM personal_requests WHERE user_id=?)", (user_id,),
            )
            db.execute("DELETE FROM personal_requests WHERE user_id=?", (user_id,))
            db.execute(
                "UPDATE personal_request_users SET dm_status='needs_test' WHERE user_id=?",
                (user_id,),
            )
            db.execute(
                "UPDATE personal_request_alerts SET state='cancelled',reason='pro_access_lost' "
                "WHERE user_id=? AND state IN ('pending','leased')",
                (user_id,),
            )

        for row in removed:
            self._changed("delete", user_id, row)

    def stats(self, *, now=None):
        now = _now(now)
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
            result["needs_test"] = db.execute(
                "SELECT COUNT(*) FROM personal_request_users WHERE dm_status='needs_test'"
            ).fetchone()[0]
            result["due"], oldest = db.execute(
                "SELECT COUNT(*),MIN(created) FROM personal_request_alerts "
                "WHERE state='pending' AND expires>? AND next_attempt<=?",
                (now, now),
            ).fetchone()
            result["oldest_due_age_seconds"] = max(0, now - oldest) if oldest is not None else 0
            result["retry_wait"] = db.execute(
                "SELECT COUNT(*) FROM personal_request_alerts "
                "WHERE state='pending' AND expires>? AND next_attempt>?",
                (now, now),
            ).fetchone()[0]
            result["reasons"] = {}
            for state, reason, count in db.execute(
                "SELECT state,reason,COUNT(*) FROM personal_request_alerts "
                "WHERE reason IS NOT NULL GROUP BY state,reason"
            ):
                result["reasons"].setdefault(state, {})[reason] = count
            return result
