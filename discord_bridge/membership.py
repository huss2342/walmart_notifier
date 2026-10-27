"""Celebrate paid-role activation using complete, durable member snapshots.

Discord roles establish access, not a billing receipt. No payment/customer data
is read. Existing paid members seed a quiet baseline; Pro wins over Starter.
"""

from __future__ import annotations

import hashlib
import uuid

from .api import DiscordError
from .welcome import WelcomeWorker, _snowflake

_PAGE_SIZE = 1000
_MAX_PENDING = 1000
_COOLDOWN = 86400
_TIERS = {"starter": ("Starter", 0x1ABC9C), "pro": ("Pro", 0xF1C40F)}


class MembershipWorker(WelcomeWorker):
    """Reuse the exact-user, at-most-once outbox and paid-delivery priority."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.enabled:
            return
        self.roles = self.state.get("roles", {})
        if (
            not all(_snowflake(self.roles.get(tier)) for tier in _TIERS)
            or self.roles["starter"] == self.roles["pro"]
        ):
            raise ValueError("Membership celebrations require distinct paid roles")
        with self._db() as db:
            binding = "|".join(self.roles[tier] for tier in _TIERS)
            previous = db.execute("SELECT value FROM settings WHERE key='roles'").fetchone()
            if previous and previous[0] != binding:
                raise ValueError("Membership ledger belongs to another role configuration")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('roles',?)", (binding,))
            db.executescript("""
                CREATE TABLE IF NOT EXISTS members (user_hash TEXT PRIMARY KEY, tier TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS member_scan (
                    user_id TEXT PRIMARY KEY, tier TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS celebrations (
                    event_id TEXT PRIMARY KEY, tier TEXT NOT NULL,
                    paid_members INTEGER NOT NULL, upgrade INTEGER NOT NULL);
            """)

    def _ready(self):
        from .provision import audit

        assessment = (self.auditor or audit)(self, self.guild, self.state)
        self._configured = isinstance(assessment, dict) and assessment.get("ok") is True
        if not self._configured:
            self._last_error = "privacy_audit"
        return self._configured

    def _collect(self, result, now):
        with self._db() as db:
            settings = dict(db.execute("SELECT key,value FROM settings"))
            db.execute(
                "DELETE FROM celebrations WHERE event_id NOT IN (SELECT event_id FROM welcomes)"
            )
        if "member_complete" not in settings:
            self._available()
            try:
                rows = self.client.guild_members(
                    self.guild, limit=_PAGE_SIZE, after=settings.get("member_after")
                )
            except DiscordError as exc:
                if exc.status in {401, 403}:
                    self._configured = False
                    self._last_error = "member_access"
                    result["blocked"] = True
                    return True
                raise
            with self._db() as db:
                for row in rows:
                    user = row["user"]
                    if user.get("bot", False) or user["id"] == self.bot:
                        continue
                    tier = next(
                        (t for t in ("pro", "starter") if self.roles[t] in row["roles"]), None
                    )
                    if tier:
                        db.execute(
                            "INSERT OR REPLACE INTO member_scan VALUES (?,?)", (user["id"], tier)
                        )
                if len(rows) == _PAGE_SIZE:
                    after = str(max(int(row["user"]["id"]) for row in rows))
                    db.execute(
                        "INSERT OR REPLACE INTO settings VALUES ('member_after',?)", (after,)
                    )
                    return True
                db.execute("INSERT OR REPLACE INTO settings VALUES ('member_complete','1')")
        self._available()
        with self._db() as db:
            baseline = "member_baseline" not in settings
            result["baseline"] = baseline
            members = db.execute("SELECT * FROM member_scan ORDER BY user_id").fetchall()
            count = len(members)
            pending = db.execute(
                "SELECT COUNT(*) FROM welcomes WHERE state IN ('pending','sending')"
            ).fetchone()[0]
            complete = True
            for member in members:
                user, tier = member["user_id"], member["tier"]
                digest = hashlib.sha256(user.encode()).hexdigest()
                previous = db.execute(
                    "SELECT tier FROM members WHERE user_hash=?", (digest,)
                ).fetchone()
                upgrade = bool(previous and previous[0] == "starter" and tier == "pro")
                changed = previous is None or upgrade
                cooldown_key = hashlib.sha256(f"{user}|{tier}".encode()).hexdigest()
                cooldown = db.execute(
                    "SELECT until FROM cooldowns WHERE user_hash=?", (cooldown_key,)
                ).fetchone()
                celebrate = not baseline and changed and (not cooldown or cooldown[0] <= now)
                if celebrate and pending >= _MAX_PENDING:
                    complete = False
                    continue
                if celebrate:
                    event = uuid.uuid4().hex
                    nonce = hashlib.sha256((self.guild + event).encode()).hexdigest()[:24]
                    db.execute(
                        "INSERT INTO welcomes VALUES (?,?,?,'pending',?,NULL,?,NULL)",
                        (event, user, nonce, now, now),
                    )
                    db.execute(
                        "INSERT INTO celebrations VALUES (?,?,?,?)",
                        (event, tier, count, int(upgrade)),
                    )
                    pending += 1
                if baseline or celebrate:
                    db.execute(
                        "INSERT OR REPLACE INTO cooldowns VALUES (?,?)",
                        (cooldown_key, now + _COOLDOWN),
                    )
                db.execute("INSERT OR REPLACE INTO members VALUES (?,?)", (digest, tier))
            db.execute("INSERT OR REPLACE INTO settings VALUES ('paid_members',?)", (str(count),))
            if complete:
                # Replace only after a full scan. Partial scans/provider failures
                # must never manufacture removals or reset the active count.
                current = {hashlib.sha256(m["user_id"].encode()).hexdigest() for m in members}
                for row in db.execute("SELECT user_hash FROM members").fetchall():
                    if row[0] not in current:
                        db.execute("DELETE FROM members WHERE user_hash=?", (row[0],))
                db.execute("INSERT OR REPLACE INTO settings VALUES ('member_baseline','1')")
                db.execute("DELETE FROM member_scan")
                db.execute("DELETE FROM settings WHERE key IN ('member_after','member_complete')")
            return complete

    def _send_pending(self, result, now):
        if result["blocked"]:
            return
        super()._send_pending(result, now)

    def _payload(self, row):
        with self._db() as db:
            detail = db.execute(
                "SELECT * FROM celebrations WHERE event_id=?", (row["event_id"],)
            ).fetchone()
        name, color = _TIERS[detail["tier"]]
        payload = super()._payload(row)
        payload["content"] = f"🎉 Congratulations <@{row['user_id']}>!"
        payload["embeds"] = [
            {
                "title": "🚀 Pro upgrade unlocked!"
                if detail["upgrade"]
                else f"🎊 Welcome to {name}!",
                "description": (
                    f"Your **{name}** membership is active! "
                    "Thanks for being part of the community. 💛"
                ),
                "color": color,
            }
        ]
        return payload

    def health(self):
        result = super().health()
        result["paid_members"] = None
        if self.enabled:
            with self._db() as db:
                count = db.execute("SELECT value FROM settings WHERE key='paid_members'").fetchone()
            result["paid_members"] = int(count[0]) if count else None
        return result
