"""Durable per-destination jobs and immutable dispatch envelopes.

Observation and rendering names remain reexported for existing integrations.
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .api import encode_payload
from .messages import _embed_title as _embed_title
from .messages import render_batch as render_batch
from .messages import render_message as render_message
from .monitoring import emit_event
from .observations import CATEGORIES as CATEGORIES
from .observations import MAX_BATCH as MAX_BATCH
from .observations import SAMPLING_PARAMS as SAMPLING_PARAMS
from .observations import _product_image as _product_image
from .observations import _text as _text
from .observations import normalize_item as normalize_item
from .observations import route_keys as route_keys
from .observations import validate_batch as validate_batch
from .policy import FREE_DELAY_SECONDS, FREE_ROUTE_KEY, RETIRED_ROUTE_KEYS
from .policy import FREE_JOB_LIFETIME_SECONDS as FREE_JOB_LIFETIME_SECONDS
from .policy import JOB_LIFETIME_SECONDS as JOB_LIFETIME_SECONDS
from .policy import PRICE_BANDS as PRICE_BANDS
from .request_store import initialize_requests, match_requests

# Application wire ceiling; kept here for existing dispatcher configuration/tests.
MAX_MESSAGE_BYTES = 10 * 1024


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
            # Retire old pending/retry envelopes before any worker can resume
            # them. Keep payloads, nonces and terminal history for inspection.
            for route in RETIRED_ROUTE_KEYS:
                db.execute(
                    "UPDATE dispatches SET state='skipped' "
                    "WHERE route=? AND state IN ('pending','leased')",
                    (route,),
                )
                db.execute(
                    "UPDATE jobs SET state='skipped',reason='route_retired',"
                    "lease_token=NULL,lease_until=NULL "
                    "WHERE route=? AND state IN ('pending','leased')",
                    (route,),
                )

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

    def record(
        self,
        items: list[dict],
        *,
        now: float | None = None,
        seed_only=False,
        request_event_sink=None,
        event_sink=None,
    ) -> dict:
        # Validate the entire batch before any side effect, even for direct Python callers.
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH:
            raise ValueError("expected 1 to 500 normalized items")
        items = [normalize_item(item) for item in items]
        now = time.time() if now is None else now
        new = duplicates = queued = 0
        activity = {} if request_event_sink is not None else None
        routed = [] if event_sink is not None else None
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
                    delay = FREE_DELAY_SECONDS if historical else 0
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
                    rearmed = db.execute(
                        "UPDATE jobs SET state='pending',next_attempt=due,reason=NULL "
                        "WHERE item_id=? AND route=? AND state='skipped' AND expires>? "
                        "AND batch_id IS NULL AND reason='observation_no_longer_matches'",
                        (item["item_id"], route, now),
                    )
                    if routed is not None and (inserted.rowcount or rearmed.rowcount):
                        routed.append({"phase": "intake", "route": route,
                                       "item_ids": [item["item_id"]], "job_ids": [key],
                                       "transition": "queued" if inserted.rowcount else "rearmed",
                                       "due": due, "expires": expires})
                queued += int(added)
            match_requests(db, items, now=now, seed_only=seed_only, activity=activity)
        # Receipts describe durable work only; a later transaction failure must
        # never leave logs claiming a route was queued when it was rolled back.
        groups = {}
        for fields in routed or ():
            group = groups.setdefault((fields["route"], fields["transition"]), {
                "phase": "intake", "route": fields["route"], "transition": fields["transition"],
                "item_ids": [], "job_ids": [],
                "due_at_min": fields["due"], "due_at_max": fields["due"],
                "expires_at_min": fields["expires"], "expires_at_max": fields["expires"],
            })
            group["item_ids"].extend(fields["item_ids"])
            group["job_ids"].extend(fields["job_ids"])
            group["due_at_min"] = min(group["due_at_min"], fields["due"])
            group["due_at_max"] = max(group["due_at_max"], fields["due"])
            group["expires_at_min"] = min(group["expires_at_min"], fields["expires"])
            group["expires_at_max"] = max(group["expires_at_max"], fields["expires"])
        for fields in groups.values():
            emit_event(event_sink, "observation_routed", component="channel", **fields,
                       observation_item_ids=fields["item_ids"],
                       observation_job_ids=fields["job_ids"], items=len(fields["item_ids"]))
        for fields in (activity or {}).values():
            emit_event(request_event_sink, "request_match", audience="personal", **fields)
        return {"accepted": len(items), "new": new, "duplicates": duplicates, "queued": queued}

    def claim(self, *, now: float | None = None) -> dict | None:
        """Compatibility API backed by the canonical single-item dispatch path.

        A frozen multi-item envelope cannot be represented by this interface and
        stays available exclusively to claim_batch; it must never be split.
        """
        batch = self.claim_batch(now=now, size=1, singles_only=True)
        return {**batch["jobs"][0], "_dispatch": batch} if batch is not None else None

    @staticmethod
    def _single_dispatch(job):
        batch = job.get("_dispatch")
        if batch is not None:
            return batch
        # Preserve handoff support for a previously serialized single-job lease.
        return {
            "id": job.get("batch_id") or job["id"],
            "route": job["route"],
            "jobs": [job],
            "lease_token": job["lease_token"],
        }

    def finish(
        self, job: dict, state: str, *, message_id: str | None = None, reason: str | None = None
    ) -> bool:
        return self.finish_batch(
            self._single_dispatch(job), state, message_id=message_id, reason=reason
        )

    def retry(
        self, job: dict, *, now: float, delay: float, reason: str, count_attempt: bool = True
    ) -> None:
        self.retry_batch(
            self._single_dispatch(job),
            now=now,
            delay=max(1, delay),
            reason=reason,
            count_attempt=count_attempt,
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

    def claim_batch(
        self,
        *,
        now=None,
        exclude_routes=(),
        audience="any",
        size=5,
        fair=False,
        singles_only=False,
    ):
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
            if singles_only:
                sql += """ AND NOT EXISTS (SELECT 1 FROM dispatches captured
                    WHERE captured.id=jobs.batch_id AND json_array_length(captured.members)>1)"""
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
                    " ORDER BY CASE WHEN jobs.route=? THEN 1 ELSE 0 END,"
                    "CASE WHEN jobs.route!=? THEN "
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
                        if not fair and first["route"] != FREE_ROUTE_KEY:
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
                            and first["route"] != FREE_ROUTE_KEY
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
                "route_retired",
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
