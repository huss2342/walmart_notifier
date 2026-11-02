"""Durable, account-bound referral identities and one-payment reward intents.

The caller must verify purchases and invoice outcomes with the provider. This
store never treats a share link or an HTTP callback as proof of payment, and
never automatically retries a possibly submitted discount application.
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

_STATES = ("pending", "applying", "applied", "used", "void", "exception")


def _discord(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[0-9]{17,20}", value)
        or not 0 < int(value) < 1 << 64
    ):
        raise ValueError("Invalid Discord member identity.")
    return value


def _provider(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r"[A-Za-z0-9]{1,96}", value):
        raise ValueError("Invalid provider identity.")
    return value


def _time(value=None):
    value = time.time() if value is None else value
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid referral time.")
    return value


def _reward_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"rr_[0-9a-f]{32}", value):
        raise ValueError("Invalid reward identity.")
    return value


def _reason(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
        raise ValueError("Invalid referral reason.")
    return value


def _lease(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value):
        raise ValueError("Invalid reward lease.")
    return value


def _one(db, statement, parameters=()):
    row = db.execute(statement, parameters).fetchone()
    return dict(row) if row is not None else None


class ReferralStore:
    """A separate SQLite ledger with atomic identity, credit and lease writes."""

    def __init__(self, path, *, guild_id, company_id):
        self.guild_id = _discord(guild_id)
        self.company_id = _provider(company_id, "biz_")
        self.path = Path(path)
        if self.path.is_symlink():
            raise ValueError("Referral database must not be a symbolic link.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db(write=True) as db:
            existing = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if existing and "referral_meta" not in existing:
                raise ValueError("Referral database binding is missing.")
            db.execute("CREATE TABLE IF NOT EXISTS referral_meta (key TEXT PRIMARY KEY,value TEXT)")
            binding = dict(db.execute("SELECT key,value FROM referral_meta"))
            expected = {"guild_id": self.guild_id, "company_id": self.company_id, "schema": "1"}
            if binding and binding != expected:
                raise ValueError("Referral database binding does not match this account.")
            for key, value in expected.items():
                db.execute("INSERT OR IGNORE INTO referral_meta VALUES (?,?)", (key, value))
            statements = (
                """CREATE TABLE IF NOT EXISTS referral_profiles (
                    discord_id TEXT PRIMARY KEY,share_code TEXT UNIQUE NOT NULL,
                    whop_user_id TEXT UNIQUE,friend_promo_id TEXT,friend_promo_code TEXT,
                    created_at REAL NOT NULL,updated_at REAL NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS referral_friend_promos (
                    promo_id TEXT PRIMARY KEY,promo_code TEXT UNIQUE NOT NULL,
                    discord_id TEXT NOT NULL REFERENCES referral_profiles(discord_id))""",
                """CREATE TABLE IF NOT EXISTS referral_rewards (
                    reward_id TEXT PRIMARY KEY,
                    referrer_discord_id TEXT NOT NULL REFERENCES referral_profiles(discord_id),
                    referee_whop_user_id TEXT UNIQUE NOT NULL,referee_discord_id TEXT,
                    qualifying_payment_id TEXT UNIQUE NOT NULL,referee_membership_id TEXT NOT NULL,
                    reward_code TEXT UNIQUE NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                        ('pending','applying','applied','used','void','exception')),
                    membership_id TEXT,lease_token TEXT,lease_until REAL,intent_key TEXT,
                    application_generation INTEGER NOT NULL DEFAULT 0,
                    provider_fingerprint TEXT,apply_submitted_at REAL,
                    promo_id TEXT UNIQUE,used_payment_id TEXT UNIQUE,reason TEXT,
                    created_at REAL NOT NULL,updated_at REAL NOT NULL)""",
                """CREATE INDEX IF NOT EXISTS referral_rewards_member
                    ON referral_rewards(referrer_discord_id,state,created_at,reward_id)""",
                """CREATE UNIQUE INDEX IF NOT EXISTS referral_active_membership
                    ON referral_rewards(membership_id) WHERE membership_id IS NOT NULL
                    AND state IN ('applying','applied','exception')""",
                """CREATE UNIQUE INDEX IF NOT EXISTS referral_active_member
                    ON referral_rewards(referrer_discord_id) WHERE membership_id IS NOT NULL
                    AND state IN ('applying','applied','exception')""",
            )
            for statement in statements:
                db.execute(statement)
            columns = {row[1] for row in db.execute("PRAGMA table_info(referral_rewards)")}
            additions = (
                ("application_generation", "INTEGER NOT NULL DEFAULT 0"),
                ("provider_fingerprint", "TEXT"),
                ("apply_submitted_at", "REAL"),
            )
            for name, definition in additions:
                if name not in columns:
                    db.execute(f"ALTER TABLE referral_rewards ADD COLUMN {name} {definition}")
                    if name == "apply_submitted_at":
                        # Older applying records cannot prove that no provider
                        # write occurred. Never migrate ambiguity into a safe-to-
                        # retry prepare phase; the missing fingerprint also
                        # prevents credential-scoped automatic replay.
                        db.execute(
                            "UPDATE referral_rewards SET apply_submitted_at=updated_at "
                            "WHERE membership_id IS NOT NULL "
                            "AND state IN ('applying','applied','exception')"
                        )

    @contextmanager
    def _db(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def ensure_profile(self, discord_id, *, now=None):
        discord_id, now = _discord(discord_id), _time(now)
        with self._db(write=True) as db:
            profile = _one(db, "SELECT * FROM referral_profiles WHERE discord_id=?", (discord_id,))
            if profile is not None:
                return profile
            # A generated-code collision retries locally without replacing an identity.
            for _ in range(5):
                code = "RR" + secrets.token_hex(8).upper()
                try:
                    db.execute(
                        "INSERT INTO referral_profiles "
                        "(discord_id,share_code,created_at,updated_at) VALUES (?,?,?,?)",
                        (discord_id, code, now, now),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise ValueError("Referral code could not be allocated.")
            return _one(db, "SELECT * FROM referral_profiles WHERE discord_id=?", (discord_id,))

    def profile(self, discord_id):
        discord_id = _discord(discord_id)
        with self._db() as db:
            return _one(db, "SELECT * FROM referral_profiles WHERE discord_id=?", (discord_id,))

    def profiles(self):
        with self._db() as db:
            return [
                dict(row)
                for row in db.execute("SELECT * FROM referral_profiles ORDER BY discord_id")
            ]

    def bind_whop_user(self, discord_id, whop_user_id, *, now=None):
        """Bind provider-verified identity; no display-name or supplied-ID lookup."""
        discord_id = _discord(discord_id)
        whop_user_id, now = _provider(whop_user_id, "user_"), _time(now)
        with self._db(write=True) as db:
            row = _one(db, "SELECT * FROM referral_profiles WHERE discord_id=?", (discord_id,))
            if row is None:
                raise ValueError("Create the referral profile before binding an account.")
            if row["whop_user_id"] not in (None, whop_user_id):
                raise ValueError("This member is already bound to another Whop account.")
            try:
                db.execute(
                    "UPDATE referral_profiles SET whop_user_id=?,updated_at=? WHERE discord_id=?",
                    (whop_user_id, now, discord_id),
                )
            except sqlite3.IntegrityError:
                raise ValueError("This Whop account is already bound to another member.") from None
            # Codes may exist before the tester has a paid membership. A later
            # verified account connection can expose an earlier self-referral.
            db.execute(
                "UPDATE referral_rewards SET state='exception',reason='self_referral',updated_at=? "
                "WHERE referrer_discord_id=? AND referee_whop_user_id=? "
                "AND state NOT IN ('used','void')",
                (now, discord_id, whop_user_id),
            )
            return _one(db, "SELECT * FROM referral_profiles WHERE discord_id=?", (discord_id,))

    def set_friend_promo(self, discord_id, *, promo_id, promo_code, now=None):
        discord_id, promo_id = _discord(discord_id), _provider(promo_id, "promo_")
        now = _time(now)
        if not isinstance(promo_code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", promo_code):
            raise ValueError("Invalid friend promo code.")
        with self._db(write=True) as db:
            if (
                _one(
                    db, "SELECT discord_id FROM referral_profiles WHERE discord_id=?", (discord_id,)
                )
                is None
            ):
                raise ValueError("Create the referral profile before attaching a promo.")
            previous = _one(
                db,
                "SELECT * FROM referral_friend_promos WHERE promo_id=? OR promo_code=?",
                (promo_id, promo_code),
            )
            if previous and (
                previous["discord_id"] != discord_id
                or previous["promo_id"] != promo_id
                or previous["promo_code"] != promo_code
            ):
                raise ValueError("Friend promo identity belongs to another mapping.")
            db.execute(
                "INSERT OR IGNORE INTO referral_friend_promos VALUES (?,?,?)",
                (promo_id, promo_code, discord_id),
            )
            db.execute(
                "UPDATE referral_profiles SET friend_promo_id=?,friend_promo_code=?,updated_at=? "
                "WHERE discord_id=?",
                (promo_id, promo_code, now, discord_id),
            )
            return _one(db, "SELECT * FROM referral_profiles WHERE discord_id=?", (discord_id,))

    def profile_for_promo(self, promo_id):
        promo_id = _provider(promo_id, "promo_")
        with self._db() as db:
            return _one(
                db,
                "SELECT p.* FROM referral_profiles p JOIN referral_friend_promos f "
                "ON f.discord_id=p.discord_id WHERE f.promo_id=?",
                (promo_id,),
            )

    def credit_referral(
        self,
        *,
        referrer_discord_id,
        referee_whop_user_id,
        payment_id,
        membership_id,
        referee_discord_id=None,
        now=None,
    ):
        """Record a caller-verified first paid purchase, exactly once per friend."""
        referrer = _discord(referrer_discord_id)
        referee = _provider(referee_whop_user_id, "user_")
        payment_id = _provider(payment_id, "pay_")
        membership_id, now = _provider(membership_id, "mem_"), _time(now)
        if referee_discord_id is not None:
            referee_discord_id = _discord(referee_discord_id)
        with self._db(write=True) as db:
            owner = _one(db, "SELECT * FROM referral_profiles WHERE discord_id=?", (referrer,))
            if owner is None:
                raise ValueError("Referrer profile does not exist.")
            if owner["whop_user_id"] == referee or referee_discord_id == referrer:
                raise ValueError("A self referral does not qualify for a reward.")
            previous = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM referral_rewards WHERE referee_whop_user_id=? "
                    "OR qualifying_payment_id=?",
                    (referee, payment_id),
                )
            ]
            if previous:
                if any(
                    row["referrer_discord_id"] != referrer or row["referee_whop_user_id"] != referee
                    for row in previous
                ):
                    raise ValueError("Referral attribution is already recorded for this purchase.")
                return previous[0]
            digest = hashlib.sha256(
                f"referral-reward-v1:{self.company_id}:{referee}".encode()
            ).hexdigest()
            reward_id = "rr_" + digest[:32]
            # A referee knows their own provider ID. Do not let them derive
            # and redeem the referrer's single-use reward coupon from it.
            reward_code = "RRR" + secrets.token_hex(16).upper()
            db.execute(
                "INSERT INTO referral_rewards (reward_id,referrer_discord_id,referee_whop_user_id,"
                "referee_discord_id,qualifying_payment_id,referee_membership_id,reward_code,state,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,'pending',?,?)",
                (
                    reward_id,
                    referrer,
                    referee,
                    referee_discord_id,
                    payment_id,
                    membership_id,
                    reward_code,
                    now,
                    now,
                ),
            )
            return _one(db, "SELECT * FROM referral_rewards WHERE reward_id=?", (reward_id,))

    def get_reward(self, reward_id):
        reward_id = _reward_id(reward_id)
        with self._db() as db:
            return _one(db, "SELECT * FROM referral_rewards WHERE reward_id=?", (reward_id,))

    def reward_for_promo(self, promo_id):
        promo_id = _provider(promo_id, "promo_")
        with self._db() as db:
            return _one(db, "SELECT * FROM referral_rewards WHERE promo_id=?", (promo_id,))

    def rewards(self, discord_id):
        discord_id = _discord(discord_id)
        with self._db() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM referral_rewards WHERE referrer_discord_id=? "
                    "ORDER BY created_at,rowid",
                    (discord_id,),
                )
            ]

    def summary(self, discord_id):
        discord_id = _discord(discord_id)
        counts = dict.fromkeys(_STATES, 0)
        with self._db() as db:
            for row in db.execute(
                "SELECT state,COUNT(*) FROM referral_rewards "
                "WHERE referrer_discord_id=? GROUP BY state",
                (discord_id,),
            ):
                counts[row[0]] = row[1]
        return {**counts, "total": sum(counts.values())}

    def claim_reward(
        self,
        discord_id,
        membership_id,
        *,
        now=None,
        lease_seconds=60,
        provider_fingerprint=None,
    ):
        """Lease the oldest payable reward; expired applications stay held."""
        discord_id, membership_id = _discord(discord_id), _provider(membership_id, "mem_")
        now = _time(now)
        if provider_fingerprint is not None and (
            not isinstance(provider_fingerprint, str)
            or not re.fullmatch(r"[0-9a-f]{64}", provider_fingerprint)
        ):
            raise ValueError("Invalid provider credential fingerprint.")
        if (
            type(lease_seconds) not in (int, float)
            or not math.isfinite(lease_seconds)
            or not 0 < lease_seconds <= 3600
        ):
            raise ValueError("Invalid reward lease duration.")
        with self._db(write=True) as db:
            profile = _one(db, "SELECT * FROM referral_profiles WHERE discord_id=?", (discord_id,))
            if profile is None or profile["whop_user_id"] is None:
                return None
            active = _one(
                db,
                "SELECT reward_id FROM referral_rewards WHERE "
                "(referrer_discord_id=? OR membership_id=?) AND membership_id IS NOT NULL "
                "AND state IN ('applying','applied','exception')",
                (discord_id, membership_id),
            )
            if active:
                return None
            reward = _one(
                db,
                "SELECT * FROM referral_rewards WHERE referrer_discord_id=? AND state='pending' "
                "AND referee_whop_user_id<>? ORDER BY created_at,rowid LIMIT 1",
                (discord_id, profile["whop_user_id"]),
            )
            if reward is None:
                return None
            generation = reward["application_generation"] + 1
            intent = hashlib.sha256(
                f"referral-apply-v2:{reward['reward_id']}:{membership_id}:{generation}".encode()
            ).hexdigest()
            db.execute(
                "UPDATE referral_rewards SET state='applying',membership_id=?,lease_token=?,"
                "lease_until=?,intent_key=?,application_generation=?,provider_fingerprint=?,"
                "apply_submitted_at=NULL,reason=NULL,updated_at=? WHERE reward_id=?",
                (
                    membership_id,
                    secrets.token_urlsafe(24),
                    now + lease_seconds,
                    intent,
                    generation,
                    provider_fingerprint,
                    now,
                    reward["reward_id"],
                ),
            )
            return _one(
                db, "SELECT * FROM referral_rewards WHERE reward_id=?", (reward["reward_id"],)
            )

    def mark_submission(self, reward_id, lease_token, *, now=None):
        """Durably mark the apply boundary before submitting to the provider."""
        reward_id, lease_token, now = _reward_id(reward_id), _lease(lease_token), _time(now)
        with self._db(write=True) as db:
            row = self._application(db, reward_id, lease_token)
            if row["state"] != "applying":
                raise ValueError("Reward lease is no longer preparing an application.")
            db.execute(
                "UPDATE referral_rewards SET apply_submitted_at=COALESCE(apply_submitted_at,?),"
                "updated_at=? WHERE reward_id=?",
                (now, now, reward_id),
            )
            return _one(db, "SELECT * FROM referral_rewards WHERE reward_id=?", (reward_id,))

    @staticmethod
    def _application(db, reward_id, lease_token):
        row = _one(db, "SELECT * FROM referral_rewards WHERE reward_id=?", (reward_id,))
        if (
            row is None
            or row["lease_token"] != lease_token
            or row["state"] not in {"applying", "applied"}
        ):
            raise ValueError("Reward lease does not own this application.")
        return row

    def mark_applied(self, reward_id, lease_token, *, promo_id, now=None):
        reward_id, lease_token = _reward_id(reward_id), _lease(lease_token)
        promo_id, now = _provider(promo_id, "promo_"), _time(now)
        with self._db(write=True) as db:
            row = self._application(db, reward_id, lease_token)
            if row["state"] == "applied" and row["promo_id"] != promo_id:
                raise ValueError("Reward already has another promo application.")
            self._save_applied(db, reward_id, promo_id, now)
            return _one(db, "SELECT * FROM referral_rewards WHERE reward_id=?", (reward_id,))

    @staticmethod
    def _save_applied(db, reward_id, promo_id, now):
        try:
            db.execute(
                "UPDATE referral_rewards SET state='applied',promo_id=?,lease_until=NULL,"
                "reason=NULL,updated_at=? WHERE reward_id=?",
                (promo_id, now, reward_id),
            )
        except sqlite3.IntegrityError:
            raise ValueError("Reward promo is already attached to another application.") from None

    def release_reward(
        self, reward_id, lease_token, *, reason, now=None, require_unsubmitted=False
    ):
        """Only call when the provider confirms no application was submitted."""
        reward_id, lease_token = _reward_id(reward_id), _lease(lease_token)
        reason, now = _reason(reason), _time(now)
        if type(require_unsubmitted) is not bool:
            raise ValueError("Invalid application release guard.")
        with self._db(write=True) as db:
            row = self._application(db, reward_id, lease_token)
            if row["state"] != "applying":
                raise ValueError("An applied reward cannot be released without reconciliation.")
            if require_unsubmitted and row["apply_submitted_at"] is not None:
                raise ValueError("Reward application may already be submitted.")
            self._save_pending(db, reward_id, reason, now)
            return _one(db, "SELECT * FROM referral_rewards WHERE reward_id=?", (reward_id,))

    @staticmethod
    def _save_pending(db, reward_id, reason, now):
        db.execute(
            "UPDATE referral_rewards SET state='pending',membership_id=NULL,lease_token=NULL,"
            "lease_until=NULL,promo_id=NULL,apply_submitted_at=NULL,reason=?,updated_at=? "
            "WHERE reward_id=?",
            (reason, now, reward_id),
        )

    def flag_exception(self, reward_id, reason, *, now=None):
        reward_id, reason, now = _reward_id(reward_id), _reason(reason), _time(now)
        with self._db(write=True) as db:
            row = _one(db, "SELECT * FROM referral_rewards WHERE reward_id=?", (reward_id,))
            if row is None:
                raise ValueError("Reward not found.")
            # A historical consumed/void reward is not reversed into a new debt.
            state = row["state"] if row["state"] in {"used", "void"} else "exception"
            db.execute(
                "UPDATE referral_rewards SET state=?,reason=?,updated_at=? WHERE reward_id=?",
                (state, reason, now, reward_id),
            )
            return _one(db, "SELECT * FROM referral_rewards WHERE reward_id=?", (reward_id,))

    def reconciliation_rewards(self, *, now=None):
        now = _time(now)
        with self._db() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM referral_rewards WHERE state IN ('applied','exception') "
                    "OR (state='applying' AND lease_until<=?) ORDER BY created_at,rowid",
                    (now,),
                )
            ]

    def reconcile_application(self, reward_id, *, outcome, promo_id=None, now=None):
        """Resolve an ambiguous application only from authoritative provider data."""
        reward_id, now = _reward_id(reward_id), _time(now)
        if outcome not in {"applied", "not_applied", "unknown"}:
            raise ValueError("Invalid application reconciliation outcome.")
        if outcome == "applied":
            promo_id = _provider(promo_id, "promo_")
        with self._db(write=True) as db:
            row = _one(db, "SELECT * FROM referral_rewards WHERE reward_id=?", (reward_id,))
            if row is None or row["state"] not in {"applying", "applied", "exception"}:
                raise ValueError("Reward has no application to reconcile.")
            if row["reason"] == "self_referral":
                raise ValueError("A self referral cannot be reconciled as a payable reward.")
            if outcome == "applied":
                if row["membership_id"] is None:
                    raise ValueError("Reward application has no membership identity.")
                if row["promo_id"] not in (None, promo_id):
                    raise ValueError("Reward application has a different promo identity.")
                self._save_applied(db, reward_id, promo_id, now)
            elif outcome == "not_applied":
                if row["state"] == "applied":
                    raise ValueError("Applied reward requires invoice reconciliation.")
                self._save_pending(db, reward_id, "confirmed_not_applied", now)
            return _one(db, "SELECT * FROM referral_rewards WHERE reward_id=?", (reward_id,))

    def mark_used(self, reward_id, *, payment_id, membership_id, now=None):
        """Consume a reward only after its successful discounted invoice is verified."""
        reward_id, payment_id = _reward_id(reward_id), _provider(payment_id, "pay_")
        membership_id, now = _provider(membership_id, "mem_"), _time(now)
        with self._db(write=True) as db:
            row = _one(db, "SELECT * FROM referral_rewards WHERE reward_id=?", (reward_id,))
            if row is None or row["state"] not in {"applied", "used"}:
                raise ValueError("Reward must be applied before invoice confirmation.")
            if row["membership_id"] != membership_id:
                raise ValueError("Invoice membership does not match the reward application.")
            if row["state"] == "used":
                if row["used_payment_id"] != payment_id:
                    raise ValueError("Reward has already been used on another payment.")
                return row
            try:
                db.execute(
                    "UPDATE referral_rewards SET state='used',used_payment_id=?,reason=NULL,"
                    "updated_at=? WHERE reward_id=?",
                    (payment_id, now, reward_id),
                )
            except sqlite3.IntegrityError:
                raise ValueError("Invoice payment already consumed another reward.") from None
            return _one(db, "SELECT * FROM referral_rewards WHERE reward_id=?", (reward_id,))

    def void_reward(self, reward_id, *, reason, now=None):
        """Void an unused pending credit; never reverse an attached or used discount."""
        reward_id, reason, now = _reward_id(reward_id), _reason(reason), _time(now)
        with self._db(write=True) as db:
            row = _one(db, "SELECT * FROM referral_rewards WHERE reward_id=?", (reward_id,))
            if row is None or row["state"] not in {"pending", "void"}:
                raise ValueError(
                    "Only a pending reward may be voided without provider reconciliation."
                )
            db.execute(
                "UPDATE referral_rewards SET state='void',reason=?,updated_at=? WHERE reward_id=?",
                (reason, now, reward_id),
            )
            return _one(db, "SELECT * FROM referral_rewards WHERE reward_id=?", (reward_id,))
