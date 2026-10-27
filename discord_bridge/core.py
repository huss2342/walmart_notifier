"""Validated public observations and a durable, per-destination delivery queue."""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .api import encode_payload
from .layout import FREE_DELAY_SECONDS, FREE_ROUTE_KEY, STARTER_DELAY_SECONDS
from .request_store import initialize_requests, match_requests

CATEGORIES = (
    "furniture",
    "home-storage-decor",
    "kitchen-appliances",
    "electronics",
    "tools-auto",
    "beauty-health",
    "clothing-accessories",
    "toys-baby-books",
    "pets",
    "sports-office-travel",
    "other",
)
PRICE_BANDS = (
    (1000, "under-10"),
    (2000, "10-20"),
    (4000, "20-40"),
    (8000, "40-80"),
    (20000, "80-200"),
)
JOB_LIFETIME_SECONDS = 1800
FREE_JOB_LIFETIME_SECONDS = 7 * 86400
MAX_BATCH = 500
# A conservative application cap after live rejection of 15–17KiB envelopes;
# this is not a claim about Discord's documented attachment/request limits.
MAX_MESSAGE_BYTES = 10 * 1024
SAMPLING_PARAMS = {
    "filters": '[{"intent":"sampling","values":["Sampling"]}]',
    "classType": "REGULAR",
    "from": "/reviews/claim-product",
}


def _text(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"invalid {field}")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"invalid {field}")
    return value.strip()


def _product_image(value: object) -> str:
    """Keep only public Walmart product photos, without query/fragment data.

    Images are optional: bad evidence must never stop delivery of a valid item.
    Discord fetches the photo; this service does not download or proxy it.
    """
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or parsed.netloc != "i5.walmartimages.com"
        or not re.fullmatch(
            r"/(?:asr|seo)/[A-Za-z0-9][A-Za-z0-9_.~-]*\.(?:avif|jpe?g|png|webp)",
            parsed.path,
            re.IGNORECASE,
        )
    ):
        return ""
    return f"https://i5.walmartimages.com{parsed.path}"


def normalize_item(raw: dict) -> dict:
    """Project only public product fields; never retain the owner's quota/raw data."""
    if not isinstance(raw, dict):
        raise ValueError("item must be an object")
    item_id = _text(raw.get("item_id"), "item_id", 40)
    if not re.fullmatch(r"ip-\d{6,20}", item_id):
        raise ValueError("item_id must be ip- followed by the Walmart item number")
    title = _text(raw.get("title"), "title", 2000)
    if "value_cents" in raw and "value_usd" in raw:
        raise ValueError("supply value_cents or value_usd, not both")
    value = raw.get("value_cents", raw.get("value_usd"))
    cents = None
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("value must be numeric or null")
        if value < 0 or value > 100_000_000 or not math.isfinite(value):
            raise ValueError("invalid retail value")
        amount = Decimal(str(value)) * (1 if "value_cents" in raw else 100)
        if amount != amount.to_integral_value():
            raise ValueError("retail value must use whole cents")
        cents = int(amount)
    if raw.get("currency", "USD") != "USD":
        raise ValueError("only USD is supported")
    if "available" in raw and not isinstance(raw["available"], bool):
        raise ValueError("available must be a boolean")
    number = item_id.removeprefix("ip-")
    url = raw.get("product_url", raw.get("url")) or f"https://www.walmart.com/ip/{number}"
    url = _text(url, "product_url", 4096)
    parsed = urlsplit(url)
    match = re.fullmatch(r"/ip/(?:[^/?#]+/)?(\d{6,20})/?", parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.walmart.com"
        or not match
        or match.group(1) != number
    ):
        raise ValueError("product_url must be the matching HTTPS Walmart item")
    product_url = f"https://www.walmart.com{parsed.path.rstrip('/')}"
    if len(product_url + "?" + urlencode(SAMPLING_PARAMS)) > 512:
        product_url = f"https://www.walmart.com/ip/{number}"
    category = raw.get("primary_category", raw.get("category", "other"))
    category = category if isinstance(category, str) and category in CATEGORIES else "other"
    return {
        "item_id": item_id,
        "title": title,
        "value_cents": cents,
        "currency": "USD",
        "category": category,
        "product_url": product_url,
        "image_url": _product_image(raw.get("image_url")),
        "review_url": product_url + "?" + urlencode(SAMPLING_PARAMS),
        "review_url_status": "constructed-unverified",
        "available": raw.get("available") is not False,
    }


def validate_batch(payload: object) -> list[dict]:
    if isinstance(payload, dict) and "items" in payload:
        payload = payload["items"]
    elif isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or not 1 <= len(payload) <= MAX_BATCH:
        raise ValueError("expected 1 to 500 items")
    return [normalize_item(raw) for raw in payload]


def route_keys(item: dict) -> list[str]:
    if not item["available"]:
        return [FREE_ROUTE_KEY]
    routes = ["pro-all", f"type-{item['category']}"]
    value = item["value_cents"]
    if value is not None and value > 0:
        band = next((name for maximum, name in PRICE_BANDS if value < maximum), "200-plus")
        routes.append(f"price-{band}")
        if value < 4000:
            routes.append("starter-items")
    return [*routes, FREE_ROUTE_KEY]


def _embed_title(text: str) -> str:
    # A conservative UTF-16 bound also covers non-BMP titles; never split a
    # surrogate pair at Discord's per-title limit.
    return text.encode("utf-16-le", errors="replace")[:512].decode("utf-16-le", errors="ignore")


def render_message(item: dict, *, first_seen: float, last_seen: float, route: str) -> dict:
    value = item["value_cents"]
    money = f"${value / 100:,.2f}" if value else "Retail value unverified"
    title = item["title"].replace("@", "@\u200b")
    historical = route == FREE_ROUTE_KEY
    if historical:
        delay = "Historical alert — 24-hour delay"
    elif route == "starter-items":
        delay = "5-minute delay"
    else:
        delay = "No intentional delay"
    description = (
        f"Category: {item['category']}\n{delay}\n"
        f"First detected: <t:{int(first_seen)}:f>\n"
        f"Last observed: <t:{int(last_seen)}:R>\n"
        "Eligibility and availability depend on your Walmart account.\n"
        "The review link is constructed and needs cross-account verification."
    )
    if historical:
        description += (
            "\nTitle and retail value are from the original captured observation. "
            "This is a historical listing, not a current availability alert."
        )
    embed = {
        "title": _embed_title(f"{money} | {title}"),
        "url": item["product_url"],
        "description": description,
        "footer": {"text": item["item_id"]},
    }
    if image := _product_image(item.get("image_url")):
        embed["thumbnail"] = {"url": image}
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [embed],
        "components": [
            {
                "type": 1,
                "components": [
                    {"type": 2, "style": 5, "label": label, "url": url}
                    for label, url in (
                        ("Open review item", item["review_url"]),
                        ("View product", item["product_url"]),
                    )
                ],
            }
        ],
    }


def render_batch(jobs: list[dict]) -> dict:
    """Bundle only already-ready items from one destination, without a fill timer."""
    maximum = 5 if jobs and jobs[0]["route"] == FREE_ROUTE_KEY else 10
    if not 1 <= len(jobs) <= maximum or len({job["route"] for job in jobs}) != 1:
        raise ValueError("invalid dispatch item count or destination")
    messages = [
        render_message(
            job["item"],
            first_seen=job["first_seen"],
            last_seen=job["last_seen"],
            route=job["route"],
        )
        for job in jobs
    ]
    if len(messages) == 1:
        return messages[0]
    result = {
        "content": f"**{len(jobs)} item alerts**",
        "allowed_mentions": {"parse": []},
        "embeds": [],
        "components": [],
    }
    for index, message in enumerate(messages, 1):
        embed = message["embeds"][0]
        embed["title"] = _embed_title(f"[{index}] {embed['title']}")
        # Each card itself opens review context; members needn't scroll through
        # the other cards to reach the numbered button rows below the bundle.
        embed["url"] = jobs[index - 1]["item"]["review_url"]
        result["embeds"].append(embed)
        row = message["components"][0]
        for button in row["components"]:
            button["label"] = f"{index} · {button['label']}"
        # Two item pairs share each row only for larger paid bursts. All
        # review/product links survive within five rows and four buttons/row.
        if len(jobs) > 5 and index % 2 == 0:
            result["components"][-1]["components"].extend(row["components"])
        else:
            result["components"].append(row)
    characters = sum(
        len((embed["title"] + embed["description"] + embed["footer"]["text"]).encode("utf-16-le"))
        // 2
        for embed in result["embeds"]
    )
    if characters > 6000:
        raise ValueError("dispatch exceeds Discord embed text budget")
    return result


class JobStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1, 2, 3, 4):
                raise ValueError("unsupported database schema")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS items (
                    item_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    first_seen REAL NOT NULL, last_seen REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS seeded_items (item_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(item_id),
                    route TEXT NOT NULL, due REAL NOT NULL, expires REAL NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL, lease_token TEXT, lease_until REAL,
                    message_id TEXT, reason TEXT, payload_snapshot TEXT,
                    UNIQUE(item_id, route));
                CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(state, next_attempt, due);
                CREATE TABLE IF NOT EXISTS dispatches (
                    id TEXT PRIMARY KEY, route TEXT NOT NULL, members TEXT NOT NULL,
                    payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                    uncertain INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS route_payload_limits (
                    route TEXT PRIMARY KEY, max_items INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS delivery_gates (
                    key TEXT PRIMARY KEY, until REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS route_turns (
                    route TEXT PRIMARY KEY, last_claim INTEGER NOT NULL);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "payload_snapshot" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN payload_snapshot TEXT")
            if "batch_id" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN batch_id TEXT")
            if "sent_at" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN sent_at REAL")
            dispatch_columns = {row["name"] for row in db.execute("PRAGMA table_info(dispatches)")}
            if "uncertain" not in dispatch_columns:
                # Existing retry reasons do not prove that an earlier POST was
                # never accepted. Migration must not silently authorize repack.
                db.execute("ALTER TABLE dispatches ADD COLUMN uncertain INTEGER NOT NULL DEFAULT 1")
            # An older sender may already have submitted this job's individual
            # nonce. Never repackage an uncertain legacy send into a new batch.
            db.execute(
                "UPDATE jobs SET batch_id=id WHERE batch_id IS NULL AND attempts>0 "
                "AND state IN ('pending','leased')"
            )
            db.executescript("""
                CREATE INDEX IF NOT EXISTS jobs_batch ON jobs(batch_id,state);
                CREATE INDEX IF NOT EXISTS jobs_batch_retry
                    ON jobs(batch_id,state,next_attempt);
                CREATE INDEX IF NOT EXISTS jobs_route ON jobs(route,state,lease_until);
                CREATE INDEX IF NOT EXISTS jobs_expiry ON jobs(state,expires);
                CREATE INDEX IF NOT EXISTS jobs_metrics_pending
                    ON jobs(state,expires,due,route,next_attempt)
                    WHERE state IN ('pending','leased');
                CREATE INDEX IF NOT EXISTS jobs_metrics_sent
                    ON jobs(state,sent_at,route,due,message_id) WHERE state='sent';
                CREATE INDEX IF NOT EXISTS jobs_metrics_reasons
                    ON jobs(state,reason) WHERE reason IS NOT NULL;
            """)
            db.execute("PRAGMA user_version=4")
            initialize_requests(db)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, items: list[dict], *, now: float | None = None, seed_only=False) -> dict:
        # Validate the entire batch before any side effect, even for direct Python callers.
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH:
            raise ValueError("expected 1 to 500 normalized items")
        items = [normalize_item(item) for item in items]
        now = time.time() if now is None else now
        new = duplicates = queued = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for item in items:
                encoded = json.dumps(item, separators=(",", ":"), ensure_ascii=True)
                exists = db.execute(
                    "SELECT first_seen FROM items WHERE item_id=?", (item["item_id"],)
                ).fetchone()
                if exists:
                    duplicates += 1
                    db.execute(
                        "UPDATE items SET payload=?,last_seen=? WHERE item_id=?",
                        (encoded, now, item["item_id"]),
                    )
                    first_seen = exists["first_seen"]
                else:
                    new += 1
                    first_seen = now
                    db.execute(
                        "INSERT INTO items VALUES (?,?,?,?)", (item["item_id"], encoded, now, now)
                    )
                if seed_only:
                    db.execute("INSERT OR IGNORE INTO seeded_items VALUES (?)", (item["item_id"],))
                    db.execute(
                        "UPDATE jobs SET state='skipped',reason='seeded' "
                        "WHERE item_id=? AND state IN ('pending','leased')",
                        (item["item_id"],),
                    )
                if db.execute(
                    "SELECT 1 FROM seeded_items WHERE item_id=?", (item["item_id"],)
                ).fetchone():
                    continue
                added = False
                for route in route_keys(item):
                    historical = route == FREE_ROUTE_KEY
                    if historical:
                        delay = FREE_DELAY_SECONDS
                    elif route == "starter-items":
                        delay = STARTER_DELAY_SECONDS
                    else:
                        delay = 0
                    # A newly captured free snapshot always waits a full day,
                    # including old items first seen before this feature existed.
                    due = (now if historical else first_seen) + delay
                    expires = (
                        due + FREE_JOB_LIFETIME_SECONDS
                        if historical
                        else first_seen + JOB_LIFETIME_SECONDS
                    )
                    key = hashlib.sha256(f"v1:{item['item_id']}:{route}".encode()).hexdigest()[:25]
                    inserted = db.execute(
                        """INSERT INTO jobs
                        (id,item_id,route,due,expires,next_attempt,payload_snapshot)
                        VALUES (?,?,?,?,?,?,?)
                        ON CONFLICT(item_id,route) DO NOTHING""",
                        (
                            key,
                            item["item_id"],
                            route,
                            due,
                            expires,
                            due,
                            encoded if historical else None,
                        ),
                    )
                    added = added or inserted.rowcount == 1
                    db.execute(
                        "UPDATE jobs SET state='pending',next_attempt=due,reason=NULL "
                        "WHERE item_id=? AND route=? AND state='skipped' AND expires>? "
                        "AND batch_id IS NULL AND reason='observation_no_longer_matches'",
                        (item["item_id"], route, now),
                    )
                queued += int(added)
            match_requests(db, items, now=now, seed_only=seed_only)
        return {"accepted": len(items), "new": new, "duplicates": duplicates, "queued": queued}

    def claim(self, *, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE jobs SET state='expired',reason='stale' "
                "WHERE state IN ('pending','leased') AND expires<=?",
                (now,),
            )
            row = db.execute(
                """SELECT jobs.*,items.payload,items.first_seen,items.last_seen
                FROM jobs JOIN items USING(item_id)
                WHERE (state='pending' OR (state='leased' AND lease_until<=?))
                AND due<=? AND next_attempt<=?
                ORDER BY CASE WHEN route=? THEN 1 ELSE 0 END,due,id LIMIT 1""",
                (now, now, now, FREE_ROUTE_KEY),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["lease_token"] = secrets.token_hex(16)
            db.execute(
                "UPDATE jobs SET state='leased',lease_token=?,lease_until=?,attempts=attempts+1 "
                "WHERE id=?",
                (result["lease_token"], now + 60, result["id"]),
            )
            result["attempts"] += 1
            snapshot = result.pop("payload_snapshot")
            payload = result.pop("payload")
            result["item"] = json.loads(snapshot if snapshot is not None else payload)
            if result["route"] == FREE_ROUTE_KEY:
                result["first_seen"] = result["due"] - FREE_DELAY_SECONDS
                result["last_seen"] = result["first_seen"]
            return result

    def finish(
        self, job: dict, state: str, *, message_id: str | None = None, reason: str | None = None
    ) -> bool:
        if state not in {"sent", "failed", "skipped"}:
            raise ValueError("invalid terminal state")
        with self._connect() as db:
            return (
                db.execute(
                    "UPDATE jobs SET state=?,message_id=?,reason=?,lease_token=NULL,"
                    "lease_until=NULL WHERE id=? AND state='leased' AND lease_token=?",
                    (state, message_id, reason, job["id"], job["lease_token"]),
                ).rowcount
                == 1
            )

    def retry(
        self, job: dict, *, now: float, delay: float, reason: str, count_attempt: bool = True
    ) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE jobs SET state='pending',next_attempt=?,reason=?,"
                "attempts=MAX(0,attempts-?),lease_token=NULL,"
                "lease_until=NULL WHERE id=? AND state='leased' AND lease_token=?",
                (
                    now + max(1, delay),
                    reason,
                    int(not count_attempt),
                    job["id"],
                    job["lease_token"],
                ),
            )

    def counts(self) -> dict[str, int]:
        with self._connect() as db:
            return {
                row["state"]: row["n"]
                for row in db.execute("SELECT state,COUNT(*) AS n FROM jobs GROUP BY state")
            }

    @staticmethod
    def _job(row) -> dict:
        job = dict(row)
        snapshot = job.pop("payload_snapshot")
        job["item"] = json.loads(snapshot if snapshot is not None else job.pop("payload"))
        job.pop("payload", None)
        if job["route"] == FREE_ROUTE_KEY:
            job["first_seen"] = job["due"] - FREE_DELAY_SECONDS
            job["last_seen"] = job["first_seen"]
        return job

    @staticmethod
    def _cancel_dispatch(db, batch_id: str, *, token=None):
        sql = (
            "UPDATE jobs SET state='skipped',reason='dispatch_invalidated',lease_token=NULL,"
            "lease_until=NULL WHERE batch_id=? AND state IN ('pending','leased')"
        )
        args = [batch_id]
        if token is not None:
            sql += " AND lease_token=?"
            args.append(token)
        db.execute(sql, args)
        db.execute("UPDATE dispatches SET state='skipped' WHERE id=?", (batch_id,))

    def claim_batch(self, *, now=None, exclude_routes=(), audience="any", size=5, fair=False):
        """Atomically reserve one immutable envelope, at most one active per route."""
        now = time.time() if now is None else now
        if audience not in {"any", "paid", "free"} or not 1 <= size <= 10:
            raise ValueError("invalid dispatch selection")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE jobs SET state='expired',reason='stale' "
                "WHERE state IN ('pending','leased') AND expires<=?",
                (now,),
            )
            if self._global_until(db) > now:
                return None
            sql = """SELECT jobs.*,items.payload,items.first_seen,items.last_seen FROM jobs
                JOIN items USING(item_id)
                LEFT JOIN delivery_gates gate ON gate.key='route:'||jobs.route
                LEFT JOIN route_turns turn ON turn.route=jobs.route
                WHERE (jobs.state='pending' OR (jobs.state='leased' AND jobs.lease_until<=?))
                AND jobs.due<=? AND jobs.next_attempt<=? AND COALESCE(gate.until,0)<=?
                AND NOT EXISTS (SELECT 1 FROM jobs active WHERE active.route=jobs.route
                    AND active.state='leased' AND active.lease_until>?)
                AND NOT EXISTS (SELECT 1 FROM jobs sibling INDEXED BY jobs_batch_retry
                    WHERE jobs.batch_id IS NOT NULL
                    AND sibling.batch_id=jobs.batch_id AND sibling.state='pending'
                    AND sibling.next_attempt>?)"""
            args = [now] * 6
            if exclude_routes:
                sql += " AND jobs.route NOT IN (" + ",".join("?" for _ in exclude_routes) + ")"
                args.extend(exclude_routes)
            if audience != "any":
                sql += " AND jobs.route" + ("=" if audience == "free" else "!=") + "?"
                args.append(FREE_ROUTE_KEY)
            if fair:
                sql += (
                    " ORDER BY CASE WHEN jobs.route=? THEN 1 ELSE 0 END,"
                    "COALESCE(turn.last_claim,0),jobs.due,jobs.id LIMIT 1"
                )
                args.append(FREE_ROUTE_KEY)
            else:
                # Priority slots favor Pro, then descending observed retail value.
                # The separate fair slot still services oldest/quieter routes.
                sql += (
                    " ORDER BY CASE WHEN jobs.route=? THEN 2 "
                    "WHEN jobs.route='starter-items' THEN 1 ELSE 0 END,"
                    "CASE WHEN jobs.route NOT IN (?,'starter-items') THEN "
                    "COALESCE(json_extract(items.payload,'$.value_cents'),-1) "
                    "ELSE -1 END DESC,CASE WHEN jobs.due>=? THEN 0 ELSE 1 END,"
                    "CASE WHEN jobs.route='pro-all' THEN 0 ELSE 1 END,"
                    "jobs.due,jobs.id LIMIT 1"
                )
                args.extend((FREE_ROUTE_KEY, FREE_ROUTE_KEY, now - 60))
            # Invalidated observations are retired without taking worker slots.
            for _ in range(100):
                first = db.execute(sql, args).fetchone()
                if first is None:
                    return None
                batch_id = first["batch_id"]
                saved = db.execute("SELECT * FROM dispatches WHERE id=?", (batch_id,)).fetchone()
                if batch_id:
                    if saved:
                        ids = json.loads(saved["members"])
                        rows_by_id = {
                            row["id"]: row
                            for row in db.execute(
                                "SELECT jobs.*,items.payload,items.first_seen,items.last_seen "
                                "FROM jobs JOIN items USING(item_id) WHERE batch_id=?",
                                (batch_id,),
                            )
                        }
                        rows = [rows_by_id[key] for key in ids]
                    else:
                        rows = [first]  # Uncertain legacy single-job nonce.
                else:
                    rows = [first]
                    batch_size = min(size, 5) if first["route"] == FREE_ROUTE_KEY else size
                    learned = db.execute(
                        "SELECT max_items FROM route_payload_limits WHERE route=?",
                        (first["route"],),
                    ).fetchone()
                    if learned:
                        batch_size = min(batch_size, learned[0])
                    if first["attempts"] == 0:
                        # Priority bundles put the most valuable Pro items first;
                        # catch-up bundles retain age order. Never repack retries.
                        fill_order = "jobs.due,jobs.id"
                        if not fair and first["route"] not in {FREE_ROUTE_KEY, "starter-items"}:
                            fill_order = (
                                "COALESCE(json_extract(items.payload,'$.value_cents'),-1) "
                                "DESC,jobs.due,jobs.id"
                            )
                        eligible_sql = """SELECT jobs.*,items.payload,items.first_seen,
                            items.last_seen FROM jobs JOIN items USING(item_id)
                            WHERE jobs.route=? AND jobs.id!=? AND jobs.batch_id IS NULL
                            AND jobs.attempts=0 AND jobs.state='pending' AND due<=?
                            AND next_attempt<=? AND expires>? AND (expires<=?)=?"""
                        eligible_args = (
                            first["route"],
                            first["id"],
                            now,
                            now,
                            now,
                            now + 60,
                            int(first["expires"] <= now + 60),
                        )
                        rows.extend(
                            db.execute(
                                eligible_sql + f" ORDER BY {fill_order} LIMIT ?",
                                (*eligible_args, batch_size - 1),
                            ).fetchall()
                        )
                        if (
                            not fair
                            and batch_size > 1
                            and len(rows) == batch_size
                            and first["route"] not in {FREE_ROUTE_KEY, "starter-items"}
                        ):
                            # A hot route may always occupy a priority slot and
                            # be excluded from the fair-route slot. Reserve its
                            # final card for chronological catch-up within the
                            # same expiry cohort; other cards retain value order.
                            oldest = db.execute(
                                eligible_sql + " ORDER BY jobs.due,jobs.id LIMIT 1",
                                eligible_args,
                            ).fetchone()
                            if oldest is not None and oldest["id"] not in {
                                row["id"] for row in rows
                            }:
                                rows[-1] = oldest
                    while len(rows) > 1:
                        candidate = render_batch([self._job(row) for row in rows])
                        candidate.update(nonce="0" * 25, enforce_nonce=True)
                        if len(encode_payload(candidate)) <= MAX_MESSAGE_BYTES:
                            break
                        # Preserve the highest-value first card and oldest
                        # catch-up card while shrinking an unattempted body.
                        rows.pop(-2 if not fair and len(rows) > 2 else -1)
                    ids = [row["id"] for row in rows]
                    batch_id = (
                        first["id"]
                        if len(rows) == 1
                        else hashlib.sha256(("dispatch-v1:" + ",".join(ids)).encode()).hexdigest()[
                            :25
                        ]
                    )
                jobs = [self._job(row) for row in rows]
                valid = all(
                    job["state"] in {"pending", "leased"}
                    and job["expires"] > now
                    and job["route"] in route_keys(job["item"])
                    and not db.execute(
                        "SELECT 1 FROM seeded_items WHERE item_id=?", (job["item_id"],)
                    ).fetchone()
                    for job in jobs
                )
                if not valid:
                    if saved or first["batch_id"]:
                        self._cancel_dispatch(db, batch_id)
                    else:
                        for job in jobs:
                            if job["route"] not in route_keys(job["item"]):
                                db.execute(
                                    "UPDATE jobs SET state='skipped',reason="
                                    "'observation_no_longer_matches' WHERE id=?",
                                    (job["id"],),
                                )
                    continue
                payload = json.loads(saved["payload"]) if saved else render_batch(jobs)
                if not saved:
                    payload.update(nonce=batch_id, enforce_nonce=True)
                    db.execute(
                        "INSERT INTO dispatches (id,route,members,payload,uncertain) "
                        "VALUES (?,?,?,?,?)",
                        (
                            batch_id,
                            first["route"],
                            json.dumps([job["id"] for job in jobs]),
                            json.dumps(payload, separators=(",", ":")),
                            int(bool(first["batch_id"])),
                        ),
                    )
                elif any(row["state"] == "leased" for row in rows):
                    # Lease recovery can follow remote acceptance and a crash
                    # or failed SQLite settlement. The old nonce stays frozen.
                    db.execute("UPDATE dispatches SET uncertain=1 WHERE id=?", (batch_id,))
                token = secrets.token_hex(16)
                for job in jobs:
                    db.execute(
                        "UPDATE jobs SET batch_id=?,state='leased',lease_token=?,"
                        "lease_until=?,attempts=attempts+1 WHERE id=?",
                        (batch_id, token, now + 60, job["id"]),
                    )
                    job.update(batch_id=batch_id, lease_token=token, attempts=job["attempts"] + 1)
                db.execute("UPDATE dispatches SET state='leased' WHERE id=?", (batch_id,))
                turn = db.execute(
                    "SELECT COALESCE(MAX(last_claim),0)+1 FROM route_turns"
                ).fetchone()[0]
                db.execute(
                    "INSERT INTO route_turns VALUES (?,?) ON CONFLICT(route) DO UPDATE "
                    "SET last_claim=excluded.last_claim",
                    (first["route"], turn),
                )
                return {
                    "id": batch_id,
                    "route": first["route"],
                    "jobs": jobs,
                    "payload": payload,
                    "lease_token": token,
                }
        return None

    def _owned_batch(self, db, batch):
        rows = db.execute("SELECT * FROM jobs WHERE batch_id=?", (batch["id"],)).fetchall()
        if any(
            row["state"] == "leased" and row["lease_token"] != batch["lease_token"] for row in rows
        ):
            return False
        valid = len(rows) == len(batch["jobs"]) and all(
            row["state"] == "leased" and row["lease_token"] == batch["lease_token"] for row in rows
        )
        if not valid:
            self._cancel_dispatch(db, batch["id"], token=batch["lease_token"])
        return valid

    def validate_dispatch(self, batch, *, now):
        """Do not submit an envelope whose current routing/seed/expiry changed."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._owned_batch(db, batch):
                return False
            rows = db.execute(
                "SELECT jobs.*,items.payload,items.first_seen,items.last_seen "
                "FROM jobs JOIN items USING(item_id) WHERE batch_id=?",
                (batch["id"],),
            ).fetchall()
            for row in rows:
                job = self._job(row)
                if (
                    job["expires"] <= now
                    or job["route"] not in route_keys(job["item"])
                    or db.execute(
                        "SELECT 1 FROM seeded_items WHERE item_id=?", (job["item_id"],)
                    ).fetchone()
                ):
                    self._cancel_dispatch(db, batch["id"], token=batch["lease_token"])
                    return False
            return True

    def finish_batch(self, batch, state, *, now=None, message_id=None, reason=None):
        if state not in {"sent", "failed", "skipped"}:
            raise ValueError("invalid terminal state")
        now = time.time() if now is None else now
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._owned_batch(db, batch):
                return False
            db.execute(
                "UPDATE jobs SET state=?,message_id=?,reason=?,sent_at=?,lease_token=NULL,"
                "lease_until=NULL WHERE batch_id=? AND lease_token=?",
                (
                    state,
                    message_id,
                    reason,
                    now if state == "sent" else None,
                    batch["id"],
                    batch["lease_token"],
                ),
            )
            db.execute("UPDATE dispatches SET state=? WHERE id=?", (state, batch["id"]))
            return True

    def retry_batch(self, batch, *, now, delay, reason, count_attempt=True, ambiguous=True):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._owned_batch(db, batch):
                return False
            db.execute(
                "UPDATE jobs SET state='pending',next_attempt=?,reason=?,"
                "attempts=MAX(0,attempts-?),lease_token=NULL,lease_until=NULL "
                "WHERE batch_id=? AND lease_token=?",
                (
                    now + max(0.01, delay),
                    reason,
                    int(not count_attempt),
                    batch["id"],
                    batch["lease_token"],
                ),
            )
            db.execute(
                "UPDATE dispatches SET state='pending',uncertain=MAX(uncertain,?) WHERE id=?",
                (int(ambiguous), batch["id"]),
            )
            return True

    def split_rejected_batch(self, batch, *, now):
        """Handle only a fresh definitive413; never infer it from a retry reason."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._owned_batch(db, batch):
                return "stale"
            rows = db.execute(
                "SELECT jobs.*,items.payload,items.first_seen,items.last_seen "
                "FROM jobs JOIN items USING(item_id) WHERE batch_id=?",
                (batch["id"],),
            ).fetchall()
            if any(
                row["expires"] <= now
                or row["route"] not in route_keys(self._job(row)["item"])
                or db.execute(
                    "SELECT 1 FROM seeded_items WHERE item_id=?", (row["item_id"],)
                ).fetchone()
                for row in rows
            ):
                self._cancel_dispatch(db, batch["id"], token=batch["lease_token"])
                return "stale"
            saved = db.execute(
                "SELECT uncertain FROM dispatches WHERE id=?", (batch["id"],)
            ).fetchone()
            if saved is None or saved[0]:
                db.execute(
                    "UPDATE jobs SET state='pending',next_attempt=expires,"
                    "reason='http_413_review',lease_token=NULL,lease_until=NULL "
                    "WHERE batch_id=? AND lease_token=?",
                    (batch["id"], batch["lease_token"]),
                )
                db.execute("UPDATE dispatches SET state='pending' WHERE id=?", (batch["id"],))
                return "held"
            if len(rows) == 1:
                db.execute(
                    "UPDATE jobs SET state='failed',reason='http_413',lease_token=NULL,"
                    "lease_until=NULL WHERE batch_id=?",
                    (batch["id"],),
                )
                db.execute("UPDATE dispatches SET state='failed' WHERE id=?", (batch["id"],))
                return "failed"
            db.execute(
                "INSERT INTO route_payload_limits VALUES (?,?) ON CONFLICT(route) DO UPDATE "
                "SET max_items=MIN(max_items,excluded.max_items)",
                (batch["route"], min(5, len(rows) // 2)),
            )
            db.execute("UPDATE dispatches SET state='rejected_413' WHERE id=?", (batch["id"],))
            db.execute(
                "UPDATE jobs SET batch_id=NULL,state='pending',attempts=0,next_attempt=?,"
                "reason='http_413',lease_token=NULL,lease_until=NULL "
                "WHERE batch_id=? AND lease_token=?",
                (now, batch["id"], batch["lease_token"]),
            )
            return "split"

    @staticmethod
    def _global_until(db):
        row = db.execute("SELECT until FROM delivery_gates WHERE key='global'").fetchone()
        return row[0] if row else 0

    def defer_route(self, route, *, until):
        self._defer("route:" + route, until)

    def defer_global(self, *, until):
        self._defer("global", until)

    def _defer(self, key, until):
        if not math.isfinite(until):
            raise ValueError("invalid delivery cooldown")
        with self._connect() as db:
            db.execute(
                "INSERT INTO delivery_gates VALUES (?,?) ON CONFLICT(key) DO "
                "UPDATE SET until=MAX(until,excluded.until)",
                (key, until),
            )

    def next_due_in(self, *, now, audience="any"):
        if audience not in {"any", "paid", "free"}:
            raise ValueError("invalid delivery audience")
        with self._connect() as db:
            sql = """SELECT MIN(MAX(jobs.due,jobs.next_attempt,
                CASE WHEN jobs.state='leased' THEN COALESCE(jobs.lease_until,0) ELSE 0 END,
                COALESCE(active.until,0),COALESCE(gate.until,0))) FROM jobs
                LEFT JOIN (SELECT route,MAX(lease_until) AS until FROM jobs
                    WHERE state='leased' GROUP BY route) active ON active.route=jobs.route
                LEFT JOIN delivery_gates gate
                ON gate.key='route:'||jobs.route
                WHERE jobs.state IN ('pending','leased') AND expires>?"""
            args = [now]
            if audience != "any":
                sql += " AND jobs.route" + ("=" if audience == "free" else "!=") + "?"
                args.append(FREE_ROUTE_KEY)
            row = db.execute(sql, args).fetchone()
            return max(0, max(row[0], self._global_until(db)) - now) if row[0] is not None else 5

    def has_due_paid(self, *, now: float) -> bool:
        """Cheap priority gate; paid retries/in-flight work also defer welcomes.

        The covering pending index avoids loading product payload pages. This
        runs before each low-priority provider request, so do not replace it
        with the multi-query operational analytics below.
        """
        with self._connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM jobs WHERE state IN ('pending','leased') "
                    "AND expires>? AND due<=? AND route!=? LIMIT 1",
                    (now, now, FREE_ROUTE_KEY),
                ).fetchone()
                is not None
            )

    def delivery_metrics(self, *, now=None):
        """Operational counters; future tier releases are not delivery failures.

        Latency starts at scheduled release, excluding intentional tier delays.
        Legacy sends without confirmed sent_at are excluded from latency samples.
        """
        now = time.time() if now is None else now
        with self._connect() as db:
            row = db.execute(
                """SELECT COUNT(*),MIN(due),
                SUM(CASE WHEN route=? THEN 1 ELSE 0 END) FROM jobs
                WHERE state IN ('pending','leased') AND due<=? AND expires>?""",
                (FREE_ROUTE_KEY, now, now),
            ).fetchone()
            recent = db.execute(
                "SELECT COUNT(*),COUNT(DISTINCT message_id) FROM jobs "
                "WHERE state='sent' AND sent_at BETWEEN ? AND ?",
                (now - 300, now),
            ).fetchone()
            global_until = self._global_until(db)
            by_tier = {
                tier: dict.fromkeys(
                    (
                        "pending",
                        "future",
                        "due",
                        "retry_wait",
                        "throttled",
                        "leased",
                        "oldest_due_age_seconds",
                    ),
                    0,
                )
                for tier in ("pro", "starter", "free")
            }

            def tier_for(route):
                return (
                    "free"
                    if route == FREE_ROUTE_KEY
                    else ("starter" if route == "starter-items" else "pro")
                )

            for group in db.execute(
                """SELECT jobs.route,SUM(jobs.state='pending') AS pending,
                SUM(jobs.due>? AND expires>?) AS future,
                SUM(jobs.due<=? AND expires>?) AS due,
                SUM(jobs.state='pending' AND jobs.due<=? AND expires>?
                    AND next_attempt>?) AS retry_wait,
                SUM(jobs.due<=? AND expires>? AND (COALESCE(gate.until,0)>? OR ?>?))
                    AS throttled,
                SUM(jobs.state='leased') AS leased,
                MIN(CASE WHEN jobs.due<=? AND expires>? THEN jobs.due END) AS oldest
                FROM jobs LEFT JOIN delivery_gates gate ON gate.key='route:'||jobs.route
                WHERE jobs.state IN ('pending','leased') GROUP BY jobs.route""",
                (now, now, now, now, now, now, now, now, now, now, global_until, now, now, now),
            ):
                bucket = by_tier[tier_for(group["route"])]
                for key in ("pending", "future", "due", "retry_wait", "throttled", "leased"):
                    bucket[key] += group[key] or 0
                if group["oldest"] is not None:
                    bucket["oldest_due_age_seconds"] = max(
                        bucket["oldest_due_age_seconds"], max(0, now - group["oldest"])
                    )
            known_reasons = {
                "stale",
                "seeded",
                "observation_no_longer_matches",
                "dispatch_invalidated",
                "rate_limited",
                "remote_retry",
                "stopping",
                "retry_limit",
                "missing_destination",
                "http_400",
                "http_401",
                "http_403",
                "http_404",
                "http_413",
                "http_413_review",
            }
            reasons, terminal_counts = {}, {"failed": 0, "expired": 0, "skipped": 0}
            for group in db.execute(
                "SELECT state,reason,COUNT(*) AS n FROM jobs WHERE reason IS NOT NULL "
                "GROUP BY state,reason"
            ):
                state = group["state"]
                if state not in {"pending", "leased", "sent", "failed", "expired", "skipped"}:
                    continue
                reason = group["reason"] if group["reason"] in known_reasons else "other"
                bucket = reasons.setdefault(state, {})
                bucket[reason] = bucket.get(reason, 0) + group["n"]
            for group in db.execute(
                "SELECT state,COUNT(*) AS n FROM jobs WHERE state IN "
                "('failed','expired','skipped') GROUP BY state"
            ):
                terminal_counts[group["state"]] = group["n"]
            samples = {tier: [] for tier in by_tier}
            for sent in db.execute(
                "SELECT route,MAX(0,sent_at-due) AS delay FROM jobs WHERE state='sent' "
                "AND sent_at BETWEEN ? AND ?",
                (now - 300, now),
            ):
                samples[tier_for(sent["route"])].append(sent["delay"])
            latency = {}
            for tier, delays in samples.items():
                delays.sort()
                latency[tier] = {
                    "samples": len(delays),
                    "p50": round(delays[math.ceil(len(delays) * 0.5) - 1], 3) if delays else None,
                    "p95": round(delays[math.ceil(len(delays) * 0.95) - 1], 3) if delays else None,
                    "max": round(delays[-1], 3) if delays else None,
                }
            return {
                "due_items": row[0],
                "due_free_items": row[2] or 0,
                "oldest_due_age_seconds": max(0, now - row[1]) if row[1] else 0,
                "sent_items_last_5m": recent[0],
                "sent_messages_last_5m": recent[1],
                "global_wait_seconds": max(0, global_until - now),
                "active_route_cooldowns": db.execute(
                    "SELECT COUNT(*) FROM delivery_gates WHERE key LIKE 'route:%' AND until>?",
                    (now,),
                ).fetchone()[0],
                "by_tier": by_tier,
                "reasons": reasons,
                **{state + "_items": count for state, count in terminal_counts.items()},
                "recent_delivery_latency_seconds": latency,
            }

    def has_due(self, *, now: float) -> bool:
        """Expire stale work and avoid polling Discord while no delivery is due."""
        with self._connect() as db:
            db.execute(
                "UPDATE jobs SET state='expired',reason='stale' "
                "WHERE state IN ('pending','leased') AND expires<=?",
                (now,),
            )
            return (
                db.execute(
                    "SELECT 1 FROM jobs WHERE (state='pending' OR "
                    "(state='leased' AND lease_until<=?)) AND due<=? AND next_attempt<=? LIMIT 1",
                    (now, now, now),
                ).fetchone()
                is not None
            )
