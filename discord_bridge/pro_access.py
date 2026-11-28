"""Discord-first Pro checkout: personal Whop links tied to a Discord member.

A member taps "Get Pro" in Discord and gets a private checkout link whose
metadata carries their Discord ID. Whop copies that metadata onto the resulting
membership, so this worker can grant (and later remove) the Pro role without
the member ever connecting Discord inside Whop.

Whop stays the only billing authority: roles follow membership status polled
from Whop, never a click, a redirect or a callback. Only roles this worker
granted are ever removed by it.
"""

from __future__ import annotations

import contextlib
import logging
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode

from .api import DiscordError
from .monitoring import emit_event, error_fields
from .referral_ui import _CUSTOM_ID as REFERRAL_ID
from .referral_ui import ReferralHandler, ReplyError, _sdk
from .referral_worker import money_cents

log = logging.getLogger(__name__)

CUSTOM_ID = "pro:checkout:v1"
REDEEM_ID = "pro:redeem:v1"
REDEEM_MODAL_ID = "pro:redeem-code:v1"
ACCOUNT_ID = "account:menu:v1"
_CODE_FIELD = "code"
_CODE_TEXT = re.compile(r"[A-Za-z0-9]{6,64}\Z")
ACCOUNT_LINKS = (
    ("Manage subscription", "https://whop.com/@me/settings/orders"),
    ("Payment methods", "https://whop.com/billing/manage/payment-methods/"),
    ("Bought on Whop? Link Discord", "https://whop.com/reviewer-alerts/exp_tWRoVrlmCpMiX6/app/"),
)
ACCOUNT_TEXT = (
    "**Your account**\n\n"
    "Cancel, change plan or see invoices: **Manage subscription**.\n"
    "Update your card: **Payment methods**.\n"
    "Refer a friend, you both save 30%: **Get my referral link**.\n\n"
    "Paid on whop.com but no Pro here? Tap **Bought on Whop? Link Discord**."
)
_SNOWFLAKE = re.compile(r"[0-9]{17,20}\Z")
VALID_STATUSES = frozenset({"active", "trialing", "past_due"})
METADATA_KEY = "discord_user_id"
POLL_SECONDS = 20
FAST_POLL_SECONDS = 3
PENDING_SECONDS = 30 * 60
LINK_TTL_SECONDS = 6 * 3600
# Re-add the role for every entitled member this often. Discord drops roles
# when someone leaves the server; a rejoining payer must get Pro back.
REASSERT_SECONDS = 120
REASSERT_BATCH = 5  # per poll; keeps the idempotent re-PUTs under Discord's role limit
FOUNDING_CACHE_SECONDS = 60
CLICK_COOLDOWN_SECONDS = 10


def _id(value):
    return isinstance(value, str) and bool(_SNOWFLAKE.fullmatch(value))


def paid_memberships(payments, plan_ids=None):
    """Membership IDs with a real, kept charge (never the $0 trial start).

    Refunded or disputed charges do not count.
    """
    if isinstance(plan_ids, str):
        plan_ids = {plan_ids}
    return {
        p["membership_id"] for p in payments
        if (plan_ids is None or p.get("variant_id") in plan_ids)
        and p.get("status") == "paid"
        and p.get("membership_id")
        and money_cents(p.get("total")) not in {None, 0}
        and money_cents(p.get("refunded_amount")) in {None, 0}
        and not p.get("refunded_at")
        and not p.get("dispute_created_at")
    }


def paid_founders(payments, plan_ids):
    """Founding spots taken: founding-plan memberships with a real, kept charge.

    The $0 trial start never counts; a trial takes a spot only once its first
    charge clears. Refunded or disputed charges free the spot again.
    ``plan_ids`` is the founding plan, or it plus its no-trial copy.
    """
    ids = paid_memberships(payments, plan_ids)
    # One spot per person: a founder who leaves and returns keeps one spot.
    people = {
        ((p.get("user") or {}).get("id") if isinstance(p.get("user"), dict) else None)
        or p["membership_id"]
        for p in payments if p.get("membership_id") in ids
    }
    return len(people)


def entitled(row, paid_ids):
    """Whether a Whop membership should hold Pro right now.

    past_due (Whop is retrying a declined card) keeps Pro only for members who
    have paid before; a trial whose first charge fails loses Pro at once.
    """
    status = row.get("status")
    if status in {"active", "trialing"}:
        return True
    return status == "past_due" and row.get("id") in paid_ids


def pro_button(label):
    return {"type": 2, "style": 3, "label": label, "custom_id": CUSTOM_ID}


class ProAccessStore:
    def __init__(self, path):
        self.path = Path(path)
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS pro_links(
                    discord_id TEXT NOT NULL, plan_id TEXT NOT NULL, url TEXT NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY(discord_id, plan_id));
                CREATE TABLE IF NOT EXISTS pro_grants(
                    discord_id TEXT PRIMARY KEY, membership_id TEXT NOT NULL,
                    granted REAL NOT NULL, active INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS pro_meta(
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )

    def flag(self, key):
        with self._db() as db:
            row = db.execute("SELECT value FROM pro_meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_flag(self, key, value):
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO pro_meta VALUES(?,?)", (key, str(value)))

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def link(self, discord_id, plan_id, now):
        with self._db() as db:
            row = db.execute(
                "SELECT url, created FROM pro_links WHERE discord_id=? AND plan_id=?",
                (discord_id, plan_id),
            ).fetchone()
        return row[0] if row and now - row[1] < LINK_TTL_SECONDS else None

    def save_link(self, discord_id, plan_id, url, now):
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO pro_links VALUES(?,?,?,?)", (discord_id, plan_id, url, now)
            )

    def pending(self, now):
        """Members with a recent checkout link and no active grant yet."""
        with self._db() as db:
            return db.execute(
                "SELECT COUNT(DISTINCT l.discord_id) FROM pro_links l LEFT JOIN pro_grants g "
                "ON g.discord_id=l.discord_id AND g.active=1 "
                "WHERE l.created>=? AND g.discord_id IS NULL",
                (now - PENDING_SECONDS,),
            ).fetchone()[0]

    def granted(self):
        with self._db() as db:
            return {r[0]: r[1] for r in db.execute(
                "SELECT discord_id, membership_id FROM pro_grants WHERE active=1")}

    def set_grant(self, discord_id, membership_id, active, now):
        with self._db() as db:
            db.execute(
                "INSERT INTO pro_grants VALUES(?,?,?,?) ON CONFLICT(discord_id) DO UPDATE SET "
                "membership_id=excluded.membership_id, active=excluded.active, "
                "granted=CASE WHEN excluded.active=1 THEN excluded.granted ELSE granted END",
                (discord_id, membership_id, now, 1 if active else 0),
            )


class ProAccessService:
    def __init__(self, provider, discord, store, *, guild_id, pro_role, founding_plan,
                 standard_plan, founding_total, redirect_url, welcome_dm=None, event_sink=None,
                 code_owner=None, no_trial_plans=None, sold_out_file=None, clock=time.time):
        if not all(_id(v) for v in (guild_id, pro_role)):
            raise ValueError("Pro access requires guild and Pro role IDs")
        self.provider, self.discord, self.store = provider, discord, store
        self.guild, self.role = guild_id, pro_role
        self.founding_plan, self.standard_plan = founding_plan, standard_plan
        self.founding_total = founding_total
        self.redirect_url = redirect_url
        self.welcome_dm = welcome_dm
        self.code_owner = code_owner
        # Hidden no-trial copies (trial plan -> copy) for returning customers.
        self.no_trial_plans = dict(no_trial_plans or {})
        self.founding_plans = {founding_plan, self.no_trial_plans.get(founding_plan)} - {None}
        # Shared with counter.py (data/discord_bridge/founding-sold-out): either
        # side seeing 25 paid founders closes $15 for both, for good.
        self.sold_out_file = Path(sold_out_file) if sold_out_file else None
        self.event_sink, self.clock = event_sink, clock
        self._founding_used = None
        self._founding_at = None
        self._reasserted = None
        self._reassert_queue = []
        # Discord IDs whose Pro comes only from a free trial; None until the
        # first poll. The membership celebration waits for the paid start.
        self.trial_members = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.last_error = None

    # ---- checkout link (button click) -------------------------------------
    def sold_out(self):
        """Sticky: once 25 founders paid, a later refund never reopens $15."""
        if self.store.flag("founding_sold_out") is not None:
            return True
        return bool(self.sold_out_file and self.sold_out_file.exists())

    def _mark_sold_out(self, now):
        self.store.set_flag("founding_sold_out", int(now))
        if self.sold_out_file and not self.sold_out_file.exists():
            # The store flag alone still keeps the bot sold out if this fails.
            with contextlib.suppress(OSError):
                self.sold_out_file.write_text(str(int(now)), encoding="utf-8")

    def plan(self):
        if self.sold_out():
            return self.standard_plan
        used = self._count_founding()
        return self.founding_plan if used < self.founding_total else self.standard_plan

    def _count_founding(self):
        now = self.clock()
        if self._founding_used is not None and now - self._founding_at < FOUNDING_CACHE_SECONDS:
            return self._founding_used
        used = paid_founders(self.provider.list_payments(), self.founding_plans)
        self._founding_used, self._founding_at = used, now
        if used >= self.founding_total and not self.sold_out():
            self._mark_sold_out(now)
        return used

    def _paid_ids(self, rows):
        """Paid-before memberships; fetched only when a past_due row needs it."""
        if not any(r.get("status") == "past_due" for r in rows):
            return set()
        return paid_memberships(self.provider.list_payments())

    def member_view(self, discord_id, promo_code=None):
        """Return the member's personal checkout link (or that Pro is already active).

        With ``promo_code`` (typed into the Redeem form) the same personal link
        gets the friend's code pre-filled, so the referee never has to connect
        Discord inside Whop. Members who had any membership before get a
        no-trial checkout (one free trial per Discord account).
        """
        if not _id(discord_id):
            raise ValueError("Invalid Discord member")
        if promo_code is not None:
            # Local lookup first: a mistyped code costs no Whop calls.
            owner = self.code_owner(promo_code) if self.code_owner else None
            if owner is None:
                return {"ready": True, "code_error": "unknown"}
            if owner == discord_id:
                return {"ready": True, "code_error": "own"}
        # Every plan of the product, retired ones too: one free trial per person.
        mine = [r for r in self.provider.list_memberships(any_plan=True)
                if _discord_of(r) == discord_id]
        seen = {r["id"] for r in mine}
        mine += [r for r in self.provider.list_memberships(discord_id=discord_id, any_plan=True)
                 if r["id"] not in seen]
        paid_ids = self._paid_ids(mine)
        if any(entitled(r, paid_ids) for r in mine):
            return {"ready": True, "active": True}
        if any(r.get("status") == "past_due" for r in mine):
            return {"ready": True, "payment_failed": True}
        returning = bool(mine)
        if returning and promo_code is not None:
            return {"ready": True, "code_error": "returning"}
        plan = self.plan()
        trial = not returning
        if returning:
            plan = self.no_trial_plans.get(plan)
            if plan is None:  # copies not configured yet
                return {"ready": True, "trial_used": True}
        now = self.clock()
        url = self.store.link(discord_id, plan, now)
        if url is None:
            row = self.provider.create_checkout(
                plan,
                metadata={METADATA_KEY: discord_id, "source": "discord-pro-button"},
                redirect_url=self.redirect_url,
                idempotency_key=f"pro-checkout:{discord_id}:{plan}:{int(now // LINK_TTL_SECONDS)}",
            )
            url = row["purchase_url"]
            self.store.save_link(discord_id, plan, url, now)
            emit_event(self.event_sink, "pro_checkout_link", component="pro-access",
                       founding=plan in self.founding_plans)
        if promo_code is not None:
            url += ("&" if "?" in url else "?") + urlencode({"promoCode": promo_code.lower()})
        self._wake.set()  # poll sooner while they may be paying
        return {"ready": True, "active": False, "url": url,
                "founding": plan in self.founding_plans,
                "trial": trial,
                "promo": promo_code is not None,
                "left": max(0, self.founding_total - (self._founding_used or 0))}

    # ---- role sync (background) --------------------------------------------
    def _role(self, method, discord_id):
        try:
            self.discord.request(method, f"/guilds/{self.guild}/members/{discord_id}/roles/{self.role}")
            return True
        except DiscordError as exc:
            if getattr(exc, "status", None) == 404:  # not in the server (yet)
                return False
            raise

    def _dm(self, discord_id):
        if not self.welcome_dm:
            return
        try:  # DMs can be closed; the role is what matters.
            channel = self.discord.request("POST", "/users/@me/channels", {"recipient_id": discord_id})
            self.discord.request("POST", f"/channels/{channel['id']}/messages", {
                "content": self.welcome_dm,
                "allowed_mentions": {"parse": [], "users": [], "roles": [], "replied_user": False},
            })
        except Exception as exc:
            emit_event(self.event_sink, "pro_access_dm_failed", component="pro-access",
                       **error_fields(exc))

    def poll_interval(self):
        try:
            return FAST_POLL_SECONDS if self.store.pending(self.clock()) else POLL_SECONDS
        except Exception:
            return POLL_SECONDS

    def poll(self):
        rows = self.provider.list_memberships()
        paid_ids = self._paid_ids(rows)
        wanted, paying = {}, set()
        for row in rows:
            discord_id = _discord_of(row)
            if discord_id and entitled(row, paid_ids):
                wanted[discord_id] = row["id"]
                if row.get("status") != "trialing":
                    paying.add(discord_id)
        self.trial_members = frozenset(set(wanted) - paying)
        granted = self.store.granted()
        now = self.clock()
        limited = False
        for discord_id, membership_id in wanted.items():
            if granted.get(discord_id) == membership_id:
                continue
            try:
                if self._role("PUT", discord_id):
                    first = discord_id not in granted
                    self.store.set_grant(discord_id, membership_id, True, now)
                    emit_event(self.event_sink, "pro_access_granted", component="pro-access")
                    if first:
                        self._dm(discord_id)
            except DiscordError as exc:
                # One member's failure must not block everyone else's grant.
                emit_event(self.event_sink, "pro_access_error", component="pro-access",
                           phase="poll", **error_fields(exc))
                if getattr(exc, "status", None) == 429:
                    limited = True
                    break  # rate limited: the rest go next poll
        if not limited:
            self._reassert(wanted, granted, now)
        for discord_id in set(granted) - set(wanted):
            if limited:
                break
            try:
                # Keep the role if Whop itself links an active membership to them.
                linked = self.provider.list_memberships(discord_id=discord_id)
                if any(entitled(r, paid_ids | self._paid_ids(linked)) for r in linked):
                    self.store.set_grant(discord_id, granted[discord_id], False, now)
                    continue
                self._role("DELETE", discord_id)
            except DiscordError as exc:
                emit_event(self.event_sink, "pro_access_error", component="pro-access",
                           phase="poll", **error_fields(exc))
                if getattr(exc, "status", None) == 429:
                    break
                continue
            self.store.set_grant(discord_id, granted[discord_id], False, now)
            emit_event(self.event_sink, "pro_access_removed", component="pro-access")

    def _reassert(self, wanted, granted, now):
        """Re-PUT the role for already-granted members, a few per poll.

        Discord drops roles when a member leaves; the idempotent PUT restores
        Pro after a rejoin. A rate limit only pauses this until the next poll.
        """
        if not self._reassert_queue:
            if self._reasserted is not None and now - self._reasserted < REASSERT_SECONDS:
                return
            self._reassert_queue = sorted(
                d for d, m in wanted.items() if granted.get(d) == m)
            if not self._reassert_queue:
                self._reasserted = now
                return
        for _ in range(REASSERT_BATCH):
            if not self._reassert_queue:
                break
            discord_id = self._reassert_queue[0]
            if granted.get(discord_id) == wanted.get(discord_id):
                try:
                    self._role("PUT", discord_id)
                except DiscordError as exc:
                    if getattr(exc, "status", None) == 429:
                        return  # resume with this member next poll
                    raise
            self._reassert_queue.pop(0)
        if not self._reassert_queue:
            self._reasserted = now

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        def run():
            while not self._stop.is_set():
                try:
                    self.poll()
                    self.last_error = None
                except Exception as exc:  # keep polling; never crash the bot
                    self.last_error = type(exc).__name__
                    emit_event(self.event_sink, "pro_access_error", component="pro-access",
                               **error_fields(exc))
                self._wake.wait(self.poll_interval())
                self._wake.clear()

        self._thread = threading.Thread(target=run, name="pro-access", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def is_alive(self):
        return bool(self._thread and self._thread.is_alive())


def _discord_of(row):
    metadata = row.get("metadata")
    value = metadata.get(METADATA_KEY) if isinstance(metadata, dict) else None
    return value if _id(value) else None


def _text(view):
    if not isinstance(view, dict) or view.get("ready") is not True:
        return "Checkout is temporarily unavailable. Please try again in a minute."
    if view.get("active"):
        return "You already have Pro. Your channels are unlocked. 🎉"
    if view.get("payment_failed"):
        return ("Your last payment didn't go through, so Pro is paused.\n\n"
                "Update your card: tap **My account → Payment methods**. "
                "Pro comes back automatically once Whop charges it.")
    if view.get("trial_used"):
        return ("You've already used your free trial.\n\n"
                "Paid Pro for returning members isn't open yet. "
                "Ask in the server and we'll set you up.")
    if view.get("code_error") == "own":
        return "That's your own code. Share it with friends instead. 🙂"
    if view.get("code_error") == "returning":
        return ("Referral codes are for new members only, and you've had Pro before.\n\n"
                "Tap **Try Pro free** for your checkout (no free trial this time).")
    if view.get("code_error"):
        return ("That code doesn't match a referral code. Check it and tap "
                "**Have a referral code?** again.")
    url = view.get("url")
    if not isinstance(url, str) or not url.startswith("https://whop.com/"):
        raise ValueError("Invalid checkout link")
    if view.get("trial") is False:
        price = "**$15/month** founding price" if view.get("founding") else "**$30/month**"
        return (
            f"**Start Pro:** <{url}>\n\n"
            f"You've had a free trial before, so this starts right away at {price} + tax.\n"
            "Cancel anytime.\n\n"
            "Pro unlocks here automatically. I'll DM you."
        )
    if view.get("promo"):
        first, then = ("$10.50", "$15/month") if view.get("founding") else ("$21", "$30/month")
        return (
            "✅ **Code added: 30% off your first paid month.**\n\n"
            f"**Start your 3 free days:** <{url}>\n\n"
            f"$0 today, then **{first}** for month 1, then {then} (+ tax).\n"
            "Cancel before day 3 and you pay nothing.\n\n"
            "Pro unlocks here automatically. I'll DM you."
        )
    price = (
        f"**$15/month** founding price ({view['left']}/25 left)"
        if view.get("founding") else "**$30/month**"
    )
    return (
        f"**Try Pro free for 3 days:** <{url}>\n\n"
        f"$0 today, then {price} + tax.\n"
        "Cancel before day 3 and you pay nothing.\n\n"
        "Pro unlocks here automatically. I'll DM you.\n"
        "Friend gave you a code? Tap **Have a referral code?** instead."
    )


def _typed_code(components):
    """Find the code text input in a modal submit (action-row or label layout)."""
    stack = list(components or [])
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        if item.get("custom_id") == _CODE_FIELD and "value" in item:
            return item["value"]
        stack.extend(item.get("components") or [])
        if isinstance(item.get("component"), dict):
            stack.append(item["component"])
    return None


def redeem_button():
    return {"type": 2, "style": 2, "label": "Have a referral code?", "custom_id": REDEEM_ID}


def account_button():
    return {"type": 2, "style": 2, "label": "My account", "custom_id": ACCOUNT_ID}


class ProCheckoutHandler(ReferralHandler):
    """Same admission/privacy rules as referrals; different button and reply."""

    unavailable_text = "Checkout is temporarily unavailable. Please try again in a minute."
    repeated_text = "Still getting your link. It'll show up here in a moment."
    busy_text = "Lots of people are tapping right now. Try again in a few seconds."
    scope_text = "Use the buttons in #💳-welcome."

    def __init__(self, state, service, *, extra_channels=(), timeout=30,
                 cooldown=CLICK_COOLDOWN_SECONDS, clicks=None, clock=time.monotonic):
        # Each click lists Whop memberships/payments; the per-member cooldown
        # keeps one person from spending the shared Whop rate limit.
        super().__init__(state, service, timeout=timeout, cooldown=cooldown, clicks=clicks,
                         clock=clock)
        self.channels |= {c for c in extra_channels if _id(c)}
        self._format = _text

    def accepts(self, interaction):
        data = getattr(interaction, "data", None)
        kind = getattr(getattr(interaction, "type", None), "value", None)
        return kind == 3 and isinstance(data, dict) and data.get("custom_id") == CUSTOM_ID


class RedeemHandler(ProCheckoutHandler):
    """"Have a referral code?": a one-field form, then a personal link with the code.

    The button only opens the form (a modal must be the first response). The
    form submit runs the same admission and provider work as Get Pro.
    """

    def accepts(self, interaction):
        data = getattr(interaction, "data", None)
        kind = getattr(getattr(interaction, "type", None), "value", None)
        if not isinstance(data, dict):
            return False
        return (kind == 3 and data.get("custom_id") == REDEEM_ID) or (
            kind == 5 and data.get("custom_id") == REDEEM_MODAL_ID)

    @staticmethod
    def _modal():
        discord = _sdk()
        modal = discord.ui.Modal(title="Redeem a referral code", custom_id=REDEEM_MODAL_ID,
                                 timeout=900)
        modal.add_item(discord.ui.TextInput(
            label="Referral code", custom_id=_CODE_FIELD, min_length=6, max_length=80,
            placeholder="Paste the code your friend sent you"))
        return modal

    async def handle(self, interaction):
        kind = getattr(getattr(interaction, "type", None), "value", None)
        if kind != 3:
            await super().handle(interaction)
            return
        if not self.accepts(interaction) or interaction.response.is_done():
            return
        try:
            if self._owner(interaction) is None:
                await self._send(interaction, self.scope_text)
                return
            await interaction.response.send_modal(self._modal())
        except Exception as exc:
            await self.error(interaction, exc)

    def _argument(self, interaction):
        typed = _typed_code((getattr(interaction, "data", None) or {}).get("components"))
        code = "".join(typed.split()) if isinstance(typed, str) else ""
        if not _CODE_TEXT.fullmatch(code):
            raise ReplyError("That code doesn't look right. Codes are letters and numbers only. "
                             "Tap **Have a referral code?** and try again.")
        return code

    def _view(self, owner, argument):
        return self.service.member_view(owner, promo_code=argument)


class AccountHandler(ProCheckoutHandler):
    """"My account": a private menu of Whop links plus the referral button. No I/O."""

    def accepts(self, interaction):
        data = getattr(interaction, "data", None)
        kind = getattr(getattr(interaction, "type", None), "value", None)
        return kind == 3 and isinstance(data, dict) and data.get("custom_id") == ACCOUNT_ID

    @staticmethod
    def _menu():
        discord = _sdk()
        view = discord.ui.View(timeout=None)
        for index, (label, url) in enumerate(ACCOUNT_LINKS):
            view.add_item(discord.ui.Button(label=label, url=url, row=0 if index < 2 else 1))
        view.add_item(discord.ui.Button(label="Get my referral link", custom_id=REFERRAL_ID,
                                        style=discord.ButtonStyle.primary, row=1))
        # Stopped views are rendered but never stored: the referral click is
        # routed by on_interaction like the pinned card's button.
        view.stop()
        return view

    async def handle(self, interaction):
        if not self.accepts(interaction) or interaction.response.is_done():
            return
        try:
            if self._owner(interaction) is None:
                await self._send(interaction, self.scope_text)
                return
            await interaction.response.send_message(
                ACCOUNT_TEXT, view=self._menu(), ephemeral=True,
                allowed_mentions=_sdk().AllowedMentions.none())
        except Exception as exc:
            await self.error(interaction, exc)


class CompositeHandler:
    """Route fixed bot buttons to their handler (Gateway accepts one handler)."""

    def __init__(self, *handlers):
        self.handlers = [h for h in handlers if h is not None]

    def _pick(self, interaction):
        return next((h for h in self.handlers if h.accepts(interaction)), None)

    def accepts(self, interaction):
        return self._pick(interaction) is not None

    async def handle(self, interaction):
        handler = self._pick(interaction)
        if handler is not None:
            await handler.handle(interaction)

    async def error(self, interaction, error):
        handler = self._pick(interaction)
        if handler is not None:
            await handler.error(interaction, error)

    def close(self):
        for handler in self.handlers:
            close = getattr(handler, "close", None)
            if close:
                close()
