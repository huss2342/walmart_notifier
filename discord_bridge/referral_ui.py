"""Restart-stable referral buttons with private, authenticated member replies."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from .request_commands import _sdk

_CUSTOM_ID = "referral:link:v1"
_SNOWFLAKE = re.compile(r"[0-9]{17,20}\Z")
_CODE = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_CHECKOUT = re.compile(r"/(?:[a-z0-9-]+/)?checkout/plan_[A-Za-z0-9]+/?\Z")
_GUIDE_KEYS = ("welcome", "how-it-works", "yesterday-recap", "greeting", "pro-all")
_UNAVAILABLE = "Referrals are temporarily unavailable. Please try the button again later."
_SCOPE_HELP = "Open the referral button in this server's setup guide."
_REPEATED = (
    "Your referral link is already being prepared. Please wait for that reply before trying again."
)
_BUSY = "Referral links are busy right now. Please try again in a moment."
_MAX_JOBS = 2
INVITE_URL = "https://discord.gg/VzwNcSgWBk"
log = logging.getLogger(__name__)


class ReplyError(ValueError):
    """A safe, member-facing answer that ends the click without provider work."""


def _id(value):
    return isinstance(value, str) and bool(_SNOWFLAKE.fullmatch(value))


def referral_button():
    """Return a reusable public component with no member identity or secret."""
    return {"type": 2, "style": 1, "label": "Get my referral link", "custom_id": _CUSTOM_ID}


def _checkout_url(value):
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or not value.isascii()
        or any(char.isspace() or char in "<>\\" for char in value)
    ):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "whop.com"
        and not parsed.fragment
        and bool(_CHECKOUT.fullmatch(parsed.path))
    )


def _number(view, field, *, nullable=False):
    value = view.get(field)
    if value is None and nullable:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("Invalid referral status")
    return value


def _money(cents):
    return f"${cents // 100:,}.{cents % 100:02d}"


def _member_text(view):
    if not isinstance(view, dict) or view.get("ready") is not True:
        return _UNAVAILABLE
    code, url = view.get("code"), view.get("url")
    if not isinstance(code, str) or not _CODE.fullmatch(code) or not _checkout_url(url):
        raise ValueError("Invalid referral link")
    queued, applied, used = (_number(view, key) for key in ("queued", "applied", "used"))
    normal = _number(view, "normal_price_cents")
    friend_normal = (
        _number(view, "friend_normal_price_cents")
        if "friend_normal_price_cents" in view
        else normal
    )
    next_payment = _number(view, "next_payment_cents", nullable=True)
    membership = view.get("membership_state")
    if membership not in {"active", "none", "conflict"}:
        raise ValueError("Invalid referral membership state")
    text = (
        f"**Your referral code:** `{code}`\n\n"
        "**Send your friend:**\n"
        f"1. Join <{INVITE_URL}>\n"
        "2. In #💳-welcome, tap **Have a referral code?**\n"
        f"3. Enter `{code}`\n\n"
        "They get 3 days free, then **30% off their first paid month**. "
        "Pro unlocks for them automatically.\n"
        "Once they pay, you get **30% off your next month** (even your first)."
        f"\nStandard plan price: **{_money(normal)}/month**."
    )
    if friend_normal != normal:
        text += f"\nFriends using this link renew at **{_money(friend_normal)}/month**."
    text += "\n\n**Your rewards**\n"
    if membership == "none":
        saved = queued + applied
        if saved:
            text += f"{saved} discounted month{'s' if saved != 1 else ''} saved. "
        text += "Your discount goes on your first paid month once you start Pro (tap Try Pro free)."
    elif membership == "conflict":
        text += "Another promotion is active. Your referral discounts remain queued."
    elif applied and next_payment is not None:
        text += f"Next payment: **{_money(next_payment)}**, before any applicable tax or fees."
        if queued:
            text += f"\n{queued} more discounted month{'s' if queued != 1 else ''} queued."
    elif applied:
        text += "Your next-payment discount is applied. Whop will show your next invoice amount."
        if queued:
            text += f"\n{queued} more discounted month{'s' if queued != 1 else ''} queued."
    elif queued:
        text += f"{queued} discounted month{'s' if queued != 1 else ''} queued."
    else:
        text += "No paid referrals yet. Share your link to get started."
    if used:
        text += f"\nDiscounted payments used: {used}."
    if len(text) > 2000:
        raise ValueError("Referral reply is too large")
    return text


class ReferralHandler:
    """Handle only this bot's fixed referral buttons; no Pro role is required."""

    # Member-facing texts; subclasses (Pro checkout buttons) override these.
    unavailable_text = _UNAVAILABLE
    repeated_text = _REPEATED
    busy_text = _BUSY
    scope_text = _SCOPE_HELP
    cooldown_text = "Give it a few seconds, then tap again."

    def __init__(self, state, service, *, timeout=30, cooldown=0, clicks=None,
                 clock=time.monotonic):
        if not isinstance(state, dict):
            raise ValueError("Referrals require valid server configuration")
        self.guild, self.bot = state.get("guild_id"), state.get("bot_user_id")
        channels = state.get("channels", {})
        if not isinstance(channels, dict) or not all(
            _id(value) for value in (self.guild, self.bot)
        ):
            raise ValueError("Referrals require a saved guild and bot identity")
        self.channels = {channels[key] for key in _GUIDE_KEYS if _id(channels.get(key))}
        if not self.channels or not callable(getattr(service, "member_view", None)):
            raise ValueError("Referrals require a bound guide and member service")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Invalid referral reply timeout")
        self.service = service
        self._timeout = timeout
        self._format = _member_text
        self._lock = threading.Lock()
        self._closed = False
        self._inflight = set()
        # Per-member click cooldown. ``clicks`` can be shared by every button
        # handler so switching buttons does not get around it.
        self._cooldown, self._clock = cooldown, clock
        self._clicks = {} if clicks is None else clicks
        # Referral provider I/O must not occupy Discord's shared executor.
        # Admission below keeps the executor's normally unbounded queue empty.
        self._executor = ThreadPoolExecutor(max_workers=_MAX_JOBS, thread_name_prefix="referral-ui")

    def accepts(self, interaction):
        data = getattr(interaction, "data", None)
        kind = getattr(getattr(interaction, "type", None), "value", None)
        return kind == 3 and isinstance(data, dict) and data.get("custom_id") == _CUSTOM_ID

    def _owner(self, interaction):
        user = getattr(interaction, "user", None)
        author = getattr(getattr(interaction, "message", None), "author", None)
        owner = str(getattr(user, "id", ""))
        if (
            str(getattr(interaction, "guild_id", None)) != self.guild
            or str(getattr(interaction, "channel_id", None)) not in self.channels
            or not _id(owner)
            or getattr(user, "bot", None) is not False
            or str(getattr(author, "id", None)) != self.bot
        ):
            return None
        return owner

    async def _send(self, interaction, content):
        options = {"ephemeral": True, "allowed_mentions": _sdk().AllowedMentions.none()}
        if interaction.response.is_done():
            await interaction.followup.send(content, **options)
        else:
            await interaction.response.send_message(content, **options)

    def _admit(self, owner):
        now = self._clock()
        with self._lock:
            if self._closed:
                return self.unavailable_text
            if owner in self._inflight:
                return self.repeated_text
            last = self._clicks.get(owner)
            if self._cooldown and last is not None and now - last < self._cooldown:
                return self.cooldown_text
            if len(self._inflight) >= _MAX_JOBS:
                return self.busy_text
            self._inflight.add(owner)
            if self._cooldown:
                self._clicks[owner] = now
                if len(self._clicks) > 5000:
                    cutoff = now - self._cooldown
                    for key in [k for k, v in self._clicks.items() if v < cutoff]:
                        del self._clicks[key]
        return None

    def _release(self, owner):
        with self._lock:
            self._inflight.discard(owner)

    def _argument(self, interaction):
        """Per-click input (e.g. a typed code). Raise ReplyError to answer instead."""

    def _view(self, owner, argument):
        return self.service.member_view(owner)

    def _work(self, owner, argument=None):
        try:
            return self._view(owner, argument)
        finally:
            # A timed-out/canceled Discord waiter cannot cancel a running thread.
            # Keep its admission until provider work actually finishes.
            self._release(owner)

    def _submit(self, owner, argument=None):
        with self._lock:
            if self._closed:
                return None
            future = self._executor.submit(self._work, owner, argument)
            # close() can cancel a job before its wrapper starts. In that case
            # there is no running provider operation whose finally will release.
            future.add_done_callback(lambda row: self._release(owner) if row.cancelled() else None)
            return future

    @staticmethod
    def _observe(future):
        # If the Discord waiter timed out, retrieve late exceptions without
        # logging an unobserved Future traceback containing provider details.
        if not future.cancelled():
            future.exception()

    def close(self):
        """Reject new clicks and cancel unstarted work without waiting for I/O."""
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def handle(self, interaction):
        if not self.accepts(interaction) or interaction.response.is_done():
            return
        owner = self._owner(interaction)
        if owner is None:
            try:
                await self._send(interaction, self.scope_text)
            except Exception:
                log.warning("Referral private acknowledgement failed")
            return
        try:
            argument = self._argument(interaction)
        except ReplyError as exc:
            try:
                await self._send(interaction, str(exc))
            except Exception:
                log.warning("Referral private acknowledgement failed")
            return
        busy = self._admit(owner)
        if busy is not None:
            try:
                await self._send(interaction, busy)
            except Exception:
                log.warning("Referral private acknowledgement failed")
            return
        submitted = False
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            future = self._submit(owner, argument)
            if future is None:
                await self._send(interaction, self.unavailable_text)
                return
            submitted = True
            wrapped = asyncio.wrap_future(future)
            wrapped.add_done_callback(self._observe)
            view = await asyncio.wait_for(asyncio.shield(wrapped), timeout=self._timeout)
            await self._send(interaction, self._format(view))
        except Exception as exc:
            await self.error(interaction, exc)
        finally:
            if not submitted:
                self._release(owner)

    async def error(self, interaction, error):
        # Never include provider, customer or token-bearing exception details.
        if not self.accepts(interaction):
            return
        try:
            await self._send(interaction, self.unavailable_text)
        except Exception:
            log.warning("Referral private reply failed")
