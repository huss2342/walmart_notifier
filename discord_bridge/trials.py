"""Durable complimentary Discord access, separate from Whop's paid Pro role."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .api import DiscordError
from .monitoring import emit_event, error_fields

_MAX_DURATION = 366 * 86400
_PAGE_SIZE = 1000


def _id(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[0-9]{17,20}", value)
        or not 0 < int(value) < 1 << 64
    ):
        raise ValueError("Choose a valid Discord member.")
    return value


def _number(value, *, positive=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (positive and value == 0)
    ):
        raise ValueError("Use a positive, finite duration or timestamp.")
    return float(value)


def _seconds(value):
    value = _number(value, positive=True)
    if value > _MAX_DURATION:
        raise ValueError("A trial can last at most 366 days.")
    return value


def _joined(value):
    if not isinstance(value, str) or len(value) > 64:
        raise DiscordError("Invalid Discord join timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return _number(result.timestamp())
    except (ValueError, OverflowError, OSError):
        raise DiscordError("Invalid Discord join timestamp") from None


def _member(value, expected=None):
    user = value.get("user") if isinstance(value, dict) else None
    user_id = user.get("id") if isinstance(user, dict) else None
    try:
        _id(user_id)
        roles = value["roles"]
        if (expected is not None and user_id != expected) or not isinstance(roles, list):
            raise ValueError
        for role in roles:
            _id(role)
        if type(user.get("bot", False)) is not bool:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise DiscordError("Invalid Discord member response") from None
    return user_id, roles, user.get("bot", False)


class TrialStore:
    """Separate SQLite ledger; no role inference or provider calls in storage."""

    def __init__(self, path, *, guild_id, role_id, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.guild_id, self.role_id = _id(guild_id), _id(role_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS trial_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS trial_grants (
                    user_id TEXT PRIMARY KEY, expires_at REAL NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL,
                    actor_id TEXT NOT NULL, updated_at REAL NOT NULL,
                    synced_active INTEGER);
                CREATE TABLE IF NOT EXISTS trial_commands (
                    interaction_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS trial_seen (
                    user_id TEXT PRIMARY KEY, seen_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS trial_reconciliation (
                    user_id TEXT PRIMARY KEY, checked_at REAL NOT NULL);
            """)
            db.execute("BEGIN IMMEDIATE")
            binding = json.dumps([self.guild_id, self.role_id])
            saved = db.execute("SELECT value FROM trial_settings WHERE key='binding'").fetchone()
            if saved is not None and saved[0] != binding:
                raise ValueError("Trial database binding belongs to another server or role.")
            db.execute("INSERT OR IGNORE INTO trial_settings VALUES ('binding',?)", (binding,))
            db.execute(
                "INSERT OR IGNORE INTO trial_settings VALUES ('welcome',?)",
                (json.dumps({"enabled": False, "hours": 24, "enabled_at": 0}),),
            )

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _now(self, now):
        return _number(self.clock() if now is None else now)

    @staticmethod
    def _row(row, now):
        if row is None:
            return None
        result = dict(row)
        result["revoked"] = bool(result["revoked"])
        result["active"] = not result["revoked"] and result["expires_at"] > now
        result["synced"] = (
            result["synced_active"] is not None
            and bool(result["synced_active"]) == result["active"]
        )
        result.pop("synced_active", None)
        return result

    def get(self, user_id, *, now=None):
        user_id, now = _id(user_id), self._now(now)
        with self._db() as db:
            return self._row(
                db.execute("SELECT * FROM trial_grants WHERE user_id=?", (user_id,)).fetchone(), now
            )

    def active(self, user_id, *, now=None):
        row = self.get(user_id, now=now)
        return row is not None and row["active"]

    def settings(self):
        with self._db() as db:
            return json.loads(
                db.execute("SELECT value FROM trial_settings WHERE key='welcome'").fetchone()[0]
            )

    @staticmethod
    def _command(db, interaction_id, payload, now):
        if not isinstance(interaction_id, str) or not re.fullmatch(
            r"[A-Za-z0-9:_-]{1,128}", interaction_id
        ):
            raise ValueError("A stable Discord interaction identity is required.")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        row = db.execute(
            "SELECT fingerprint FROM trial_commands WHERE interaction_id=?", (interaction_id,)
        ).fetchone()
        if row is not None:
            if row[0] != fingerprint:
                raise ValueError("This command identity was already used for a different action.")
            return False
        db.execute("INSERT INTO trial_commands VALUES (?,?,?)", (interaction_id, fingerprint, now))
        return True

    def change(
        self, user_id, *, operation, actor_id, interaction_id, seconds=None, until=None, now=None
    ):
        user_id, actor_id, now = _id(user_id), _id(actor_id), self._now(now)
        if operation not in {"grant", "extend", "revoke"}:
            raise ValueError("Invalid trial operation.")
        if operation == "grant":
            if (seconds is None) == (until is None):
                raise ValueError("Choose a duration or an exact expiration, not both.")
            if until is not None:
                until = _number(until, positive=True)
                _seconds(until - now)
            else:
                seconds = _seconds(seconds)
        elif operation == "extend":
            seconds = _seconds(seconds)
        payload = [operation, user_id, actor_id, seconds, until]
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = self._command(db, interaction_id, payload, now)
            row = db.execute("SELECT * FROM trial_grants WHERE user_id=?", (user_id,)).fetchone()
            if changed:
                if operation == "grant":
                    if row and not row["revoked"] and row["expires_at"] > now:
                        raise ValueError("This member already has a trial. Use extend to add time.")
                    expiry = until if until is not None else now + seconds
                    db.execute(
                        "INSERT OR REPLACE INTO trial_grants VALUES (?,?,0,'owner',?,?,NULL)",
                        (user_id, expiry, actor_id, now),
                    )
                elif operation == "extend":
                    if row is None or row["revoked"]:
                        raise ValueError("Grant a trial before extending it.")
                    expiry = max(row["expires_at"], now) + seconds
                    _seconds(expiry - now)
                    db.execute(
                        "UPDATE trial_grants SET expires_at=?,actor_id=?,updated_at=?,"
                        "synced_active=NULL WHERE user_id=?",
                        (expiry, actor_id, now, user_id),
                    )
                else:
                    if row is None:
                        raise ValueError("This member has no recorded trial.")
                    db.execute(
                        "UPDATE trial_grants SET revoked=1,actor_id=?,updated_at=?,"
                        "synced_active=NULL WHERE user_id=?",
                        (actor_id, now, user_id),
                    )
            return self._row(
                db.execute("SELECT * FROM trial_grants WHERE user_id=?", (user_id,)).fetchone(), now
            )

    def configure(self, enabled, *, hours, actor_id, interaction_id, now=None):
        actor_id, now = _id(actor_id), self._now(now)
        if type(enabled) is not bool or type(hours) is not int or not 1 <= hours <= 168:
            raise ValueError("Welcome trials must be on or off and last 1 to 168 hours.")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            settings = json.loads(
                db.execute("SELECT value FROM trial_settings WHERE key='welcome'").fetchone()[0]
            )
            if self._command(db, interaction_id, ["welcome", enabled, hours, actor_id], now):
                if enabled and not settings["enabled"]:
                    settings["enabled_at"] = now
                settings.update(enabled=enabled, hours=hours)
                db.execute(
                    "UPDATE trial_settings SET value=? WHERE key='welcome'", (json.dumps(settings),)
                )
            return settings

    def observe_members(self, members, *, now=None, allow_welcome=True):
        """Caller validates the complete page before recording any welcome grants."""
        now = self._now(now)
        granted, removals = [], []
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            settings = json.loads(
                db.execute("SELECT value FROM trial_settings WHERE key='welcome'").fetchone()[0]
            )
            for user_id, joined_at, paid, has_trial in members:
                # An unsafe role/provider outage must not consume a new member's
                # welcome opportunity before access can be issued safely.
                eligible_join = settings["enabled"] and joined_at >= settings["enabled_at"]
                inserted = 0
                if allow_welcome or not eligible_join or paid:
                    inserted = db.execute(
                        "INSERT OR IGNORE INTO trial_seen VALUES (?,?)", (user_id, now)
                    ).rowcount
                expiry = joined_at + settings["hours"] * 3600
                if inserted and eligible_join and not paid and expiry > now:
                    issued = db.execute(
                        "INSERT OR IGNORE INTO trial_grants VALUES (?,?,0,'welcome',?,?,NULL)",
                        (user_id, expiry, self.guild_id, now),
                    ).rowcount
                    if issued:
                        granted.append(user_id)
                if has_trial:
                    row = db.execute(
                        "SELECT * FROM trial_grants WHERE user_id=?", (user_id,)
                    ).fetchone()
                    if row is None:
                        db.execute(
                            "INSERT INTO trial_grants VALUES (?,?,1,'orphan',?,?,1)",
                            (user_id, now, self.guild_id, now),
                        )
                        removals.append(user_id)
                    elif row["revoked"] or row["expires_at"] <= now:
                        db.execute(
                            "UPDATE trial_grants SET synced_active=1 WHERE user_id=?", (user_id,)
                        )
                        removals.append(user_id)
        return granted, removals

    def set_sync(self, user_id, active):
        with self._db() as db:
            db.execute(
                "UPDATE trial_grants SET synced_active=? WHERE user_id=?", (int(active), user_id)
            )

    def list(self, *, offset=0, limit=15, now=None):
        now = self._now(now)
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 1000
        ):
            raise ValueError("Invalid trial list page.")
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM trial_grants WHERE (revoked=0 AND expires_at>?) "
                "OR synced_active IS NULL OR synced_active=1 "
                "ORDER BY expires_at,user_id LIMIT ? OFFSET ?",
                (now, limit, offset),
            ).fetchall()
            return [self._row(row, now) for row in rows]

    def reconciliation_users(self, *, now=None):
        now = self._now(now)
        with self._db() as db:
            # Pending removals precede activations so a grant storm cannot postpone expiry.
            return [
                row[0]
                for row in db.execute(
                    "SELECT g.user_id FROM trial_grants AS g LEFT JOIN trial_reconciliation AS r "
                    "ON r.user_id=g.user_id WHERE synced_active IS NULL "
                    "OR (synced_active=1 AND (revoked=1 OR expires_at<=?)) "
                    "OR (synced_active=0 AND revoked=0 AND expires_at>?) ORDER BY "
                    "CASE WHEN revoked=1 OR expires_at<=? THEN 0 ELSE 1 END,"
                    "COALESCE(r.checked_at,0),expires_at,g.user_id LIMIT 60",
                    (now, now, now),
                ).fetchall()
            ]

    def mark_checked(self, user_id, now):
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO trial_reconciliation VALUES (?,?)", (user_id, now))


class TrialService:
    """One service serializes owner commands, expiry and welcome reconciliation.

    Only the configured trial role is managed. PUT/DELETE role operations are
    idempotent, so an interrupted write is reconciled safely after restart.
    """

    def __init__(
        self, client, state, path, *, request_store=None, event_sink=None, clock=time.time
    ):
        self.client, self.state, self.clock = client, state, clock
        self.guild = _id(state.get("guild_id"))
        self.pro = _id(state.get("roles", {}).get("pro"))
        self.role = _id(state.get("roles", {}).get("trial"))
        if len({self.guild, self.pro, self.role}) != 3:
            raise ValueError("Trial access requires a distinct server, paid role and trial role.")
        self.store = TrialStore(path, guild_id=self.guild, role_id=self.role, clock=clock)
        self.requests, self.event_sink = request_store, event_sink
        self._lock, self._stop = threading.RLock(), threading.Event()
        self._thread = None
        self._member_after = self._request_after = None
        self._last_error = self._last_poll = None

    def _error(self, exc, phase):
        self._last_error = "trial_reconciliation"
        emit_event(
            self.event_sink, "worker_error", component="trials", phase=phase, **error_fields(exc)
        )

    def _member(self, user_id):
        row = self.client.request("GET", f"/guilds/{self.guild}/members/{user_id}")
        _, roles, bot = _member(row, user_id)
        if bot:
            raise ValueError("Trials are available to human members only.")
        return roles

    def _safe_role(self):
        rows = self.client.request("GET", f"/guilds/{self.guild}/roles")
        if not isinstance(rows, list):
            raise DiscordError("Invalid Discord role inventory")
        roles = {}
        for row in rows:
            if not isinstance(row, dict):
                raise DiscordError("Invalid Discord role inventory")
            try:
                role_id = _id(row.get("id"))
            except ValueError:
                raise DiscordError("Invalid Discord role inventory") from None
            if role_id in roles:
                raise DiscordError("Duplicate Discord role identity")
            roles[role_id] = row
        role = roles.get(self.role, {})
        permissions = role.get("permissions")
        if (
            self.pro not in roles
            or role.get("name") != "Pro Trial"
            or role.get("managed") is not False
            or role.get("mentionable") is not False
            or type(permissions) not in (int, str)
            or permissions not in (0, "0")
        ):
            raise ValueError("The Pro Trial role must be unprivileged and match its audited setup.")
        return True

    def _eligible(self, user_id, roles, now):
        return self.pro in roles or (self.role in roles and self.store.active(user_id, now=now))

    def _current(self, now):
        # An explicit test/sweep timestamp is a lower bound. Provider calls may
        # cross an expiry while blocked, so never keep authorizing at that age.
        return max(now, _number(self.clock()))

    def _cleanup(self, user_id, now, *, absent=False, roles=None):
        if self.requests is None:
            return False
        if not absent:
            if roles is None:
                try:
                    roles = self._member(user_id)
                except DiscordError as exc:
                    if exc.status != 404 or exc.provider_code != 10007:
                        raise
                    absent = True
            if not absent and self._eligible(user_id, roles, self._current(now)):
                return False
        self.requests.revoke_user(user_id)
        return True

    def _sync(self, user_id, now, *, grant_safe=None):
        if self._stop.is_set():
            return False
        now = self._current(now)
        row = self.store.get(user_id, now=now)
        if row is None:
            return False
        self.store.mark_checked(user_id, now)
        wanted = row["active"]
        try:
            roles = self._member(user_id)
        except DiscordError as exc:
            if exc.status != 404 or exc.provider_code != 10007:
                raise
            self.store.set_sync(user_id, False)
            self._cleanup(user_id, now, absent=True)
            return not wanted
        # Re-read eligibility after each blocking boundary. A PUT accepted just
        # after expiry is followed by DELETE in this same bounded reconciliation.
        for _attempt in range(3):
            now = self._current(now)
            wanted = self.store.active(user_id, now=now)
            if (self.role in roles) == wanted or self._stop.is_set():
                break
            if wanted:
                if grant_safe is False or not self._safe_role():
                    raise ValueError(
                        "Trial activation is paused until the role passes its safety check."
                    )
                now = self._current(now)
                if not self.store.active(user_id, now=now):
                    continue
            self.client.request(
                "PUT" if wanted else "DELETE",
                f"/guilds/{self.guild}/members/{user_id}/roles/{self.role}",
            )
            # A successful response acknowledges the write; readback supplies the
            # current paid-role state before request deletion or confirmed status.
            roles = self._member(user_id)
        now = self._current(now)
        wanted = self.store.active(user_id, now=now)
        observed = self.role in roles
        self.store.set_sync(user_id, observed)
        if not wanted:
            self._cleanup(user_id, now, roles=roles)
        return observed == wanted

    def _change(self, user_id, *, operation, actor_id, interaction_id, seconds=None, until=None):
        with self._lock:
            if self._stop.is_set():
                raise ValueError("Trial management is stopping. Try again after it reconnects.")
            user_id = _id(user_id)
            # A bogus member or provider failure is not a grantable identity.
            if operation != "revoke":
                self._safe_role()
                self._member(user_id)
            now = _number(self.clock())
            self.store.change(
                user_id,
                operation=operation,
                actor_id=actor_id,
                interaction_id=interaction_id,
                seconds=seconds,
                until=until,
                now=now,
            )
            try:
                self._sync(user_id, now)
            except Exception as exc:
                self._error(exc, "worker")
            return self.store.get(user_id, now=self._current(now))

    def grant(self, user_id, *, seconds=None, until=None, actor_id, interaction_id):
        return self._change(
            user_id,
            operation="grant",
            seconds=seconds,
            until=until,
            actor_id=actor_id,
            interaction_id=interaction_id,
        )

    def extend(self, user_id, *, seconds, actor_id, interaction_id):
        return self._change(
            user_id,
            operation="extend",
            seconds=seconds,
            actor_id=actor_id,
            interaction_id=interaction_id,
        )

    def revoke(self, user_id, *, actor_id, interaction_id):
        return self._change(
            user_id, operation="revoke", actor_id=actor_id, interaction_id=interaction_id
        )

    def configure_welcome(self, enabled, *, hours=24, actor_id, interaction_id):
        with self._lock:
            return self.store.configure(
                enabled, hours=hours, actor_id=actor_id, interaction_id=interaction_id
            )

    def settings(self):
        return self.store.settings()

    def list(self, *, offset=0, limit=15):
        return self.store.list(offset=offset, limit=limit)

    def _scan_members(self, now, *, grant_safe):
        rows = self.client.guild_members(self.guild, limit=_PAGE_SIZE, after=self._member_after)
        now = self._current(now)
        if not isinstance(rows, list) or len(rows) > _PAGE_SIZE:
            raise DiscordError("Invalid Discord member page")
        members, ids, restore = [], set(), []
        for row in rows:
            user_id, roles, bot = _member(row)
            if user_id in ids or (
                self._member_after is not None and int(user_id) <= int(self._member_after)
            ):
                raise DiscordError("Invalid Discord member pagination")
            ids.add(user_id)
            if bot:
                continue
            joined_at = _joined(row.get("joined_at"))
            if joined_at > now:
                raise DiscordError("Invalid future join timestamp")
            members.append((user_id, joined_at, self.pro in roles, self.role in roles))
            if self.role not in roles and self.store.active(user_id, now=now):
                restore.append(user_id)
        granted, removals = self.store.observe_members(members, now=now, allow_welcome=grant_safe)
        self._member_after = str(max(map(int, ids))) if len(rows) == _PAGE_SIZE else None
        for user_id in restore:
            self.store.set_sync(user_id, False)
        for user_id in (removals + granted)[:60]:
            if self._stop.is_set():
                break
            try:
                self._sync(user_id, now, grant_safe=grant_safe)
            except Exception as exc:
                self._error(exc, "worker")
        return len(granted)

    def _cleanup_requests(self, now):
        if self.requests is None:
            return 0
        owners = self.requests.owners(limit=50, after=self._request_after)
        cleaned = 0
        for user_id in owners:
            if self._stop.is_set():
                break
            try:
                cleaned += self._cleanup(_id(user_id), now)
            except Exception as exc:
                self._error(exc, "member")
        self._request_after = owners[-1] if len(owners) == 50 else None
        return cleaned

    def step(self, now=None):
        with self._lock:
            now = _number(self.clock() if now is None else now)
            self._last_error = None
            result = {"synced": 0, "welcome_grants": 0, "cleaned": 0}
            try:
                grant_safe = self._safe_role()
            except Exception as exc:
                self._error(exc, "audit")
                grant_safe = False
            now = self._current(now)
            for user_id in self.store.reconciliation_users(now=now):
                if self._stop.is_set():
                    break
                try:
                    result["synced"] += self._sync(user_id, now, grant_safe=grant_safe)
                except Exception as exc:
                    self._error(exc, "worker")
            for phase, operation, key in (
                ("member", self._cleanup_requests, "cleaned"),
                (
                    "member",
                    lambda tick: self._scan_members(tick, grant_safe=grant_safe),
                    "welcome_grants",
                ),
            ):
                try:
                    result[key] = operation(now)
                except Exception as exc:
                    self._error(exc, phase)
            self._last_poll = self._current(now)
            return result

    def start(self):
        if self._stop.is_set() or self.is_alive():
            return

        def run():
            while not self._stop.is_set():
                try:
                    self.step()
                except Exception as exc:
                    self._error(exc, "worker")
                self._stop.wait(15)

        self._thread = threading.Thread(target=run, name="discord-trials", daemon=True)
        self._thread.start()
        emit_event(self.event_sink, "worker_lifecycle", component="trials", transition="started")

    def stop(self):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        emit_event(self.event_sink, "worker_lifecycle", component="trials", transition="stopped")

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def health(self):
        return {
            "enabled": True,
            "running": self.is_alive(),
            "last_error": self._last_error,
            "last_poll_at": self._last_poll,
            "welcome": self.settings(),
        }
