"""Private configuration and lifecycle for outbound-only referral billing."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .referral_store import ReferralStore
from .referral_ui import ReferralHandler
from .referral_worker import ReferralService
from .whop_client import WhopClient

FOUNDING_TOTAL = 25
_FIELDS = {"guild_id", "company_id", "product_ids", "plan_prices", "checkout_plan_id"}


def _identifier(value, prefix):
    return isinstance(value, str) and bool(re.fullmatch(prefix + r"[A-Za-z0-9]{1,100}", value))


def _credential(path):
    try:
        if path.stat().st_size > 16384:
            raise ValueError("Invalid referral credential file")
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise ValueError("Referral credential is not configured") from None
    values = [line.partition("=")[2].strip() for line in lines if line.startswith("WHOP_API_KEY=")]
    if len(values) != 1 or not values[0] or any(char.isspace() for char in values[0]):
        raise ValueError("Invalid referral credential")
    return values[0]


def _no_trial_plans(path, prices):
    """Optional {trial plan: hidden no-trial copy} map for returning members."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        raise ValueError("No-trial plan map is unreadable") from None
    if not isinstance(data, dict) or any(
        key not in prices or not _identifier(value, "plan_") or value in prices
        for key, value in data.items()
    ):
        raise ValueError("Invalid no-trial plan map")
    return data


def _billing_db(private_dir, name):
    """Billing SQLite path: DISCORD_BILLING_DB_DIR (Docker volume) when set.

    The Windows bind mount makes SQLite ~13x slower with unreliable locking. On
    the first start with the new directory the old file is copied over with
    SQLite's backup API (consistent even mid-write); the original stays put as a
    backup and is never read again.
    """
    target_dir = os.environ.get("DISCORD_BILLING_DB_DIR")
    old = Path(private_dir) / name
    if not target_dir:
        return old
    new = Path(target_dir) / name
    if not new.exists() and old.exists():
        import sqlite3

        source = sqlite3.connect(f"file:{old}?mode=ro", uri=True, timeout=30)
        try:
            dest = sqlite3.connect(new)
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
    return new


class ReferralRuntime:
    def __init__(self, state, config_path, *, provider_factory=WhopClient, event_sink=None,
                 discord_client=None):
        path = Path(config_path)
        try:
            if path.stat().st_size > 16384:
                raise ValueError("Referral configuration is too large")
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise ValueError("Referral configuration is unavailable") from None
        if not isinstance(config, dict) or set(config) != _FIELDS:
            raise ValueError("Invalid referral configuration fields")
        if config["guild_id"] != state.get("guild_id"):
            raise ValueError("Referral configuration belongs to another server")
        if not _identifier(config["company_id"], "biz_"):
            raise ValueError("Invalid referral business")
        products, prices = config["product_ids"], config["plan_prices"]
        if (
            not isinstance(products, list)
            or not products
            or any(not _identifier(value, "prod_") for value in products)
            or len(set(products)) != len(products)
            or not isinstance(prices, dict)
            or not prices
            or any(
                not _identifier(key, "plan_") or type(value) is not int or value <= 0
                for key, value in prices.items()
            )
            or config["checkout_plan_id"] not in prices
        ):
            raise ValueError("Invalid referral monthly products or prices")
        # Hidden no-trial copies (returning members) are billed like their trial
        # plan. Both workers see them; friend codes still hash plan_prices only.
        no_trial = _no_trial_plans(path.parent / "no-trial-plans.json", prices)
        extra_prices = {copy: prices[plan] for plan, copy in no_trial.items()}
        provider = provider_factory(
            _credential(path.parent / "whop-referrals.env"),
            company_id=config["company_id"],
            product_ids=products,
            variant_ids=list(prices) + list(extra_prices),
        )
        store = ReferralStore(
            _billing_db(path.parent, "referrals.sqlite3"),
            guild_id=state["guild_id"],
            company_id=config["company_id"],
        )
        self.service = ReferralService(
            provider,
            store,
            company_id=config["company_id"],
            product_ids=products,
            plan_prices=prices,
            checkout_plan_id=config["checkout_plan_id"],
            event_sink=event_sink,
            extra_plan_prices=extra_prices,
            linked_cache_seconds=600,
        )
        # One click registry for every button: switching buttons doesn't skip
        # the per-member cooldown that protects the shared Whop rate limit.
        clicks = {}
        referral_handler = ReferralHandler(state, self.service, cooldown=10, clicks=clicks)
        self.pro = None
        self.free = None
        pro_handler = redeem_handler = account_handler = None
        if discord_client is not None:
            from .pro_access import (
                AccountHandler,
                ProAccessService,
                ProAccessStore,
                ProCheckoutHandler,
                RedeemHandler,
            )

            by_price = sorted(prices, key=prices.get)
            channels = state.get("channels", {})
            welcome = channels.get("welcome")
            self.pro = ProAccessService(
                provider,
                discord_client,
                ProAccessStore(_billing_db(path.parent, "pro_access.sqlite3")),
                guild_id=state["guild_id"],
                pro_role=state.get("roles", {}).get("pro"),
                founding_plan=by_price[0],
                standard_plan=by_price[-1],
                founding_total=FOUNDING_TOTAL,
                redirect_url=f"https://discord.com/channels/{state['guild_id']}/{welcome}",
                welcome_dm=(
                    "✅ **Pro is unlocked!** Your alerts are live now. Start with "
                    f"<#{channels.get('pro-all')}> or pick a category or value feed, then turn on "
                    f"notifications (steps in <#{channels.get('how-it-works')}>).\n"
                    "On the 3-day free trial? You won't be charged until it ends. "
                    "Cancel anytime: <https://whop.com/@me/settings/orders>"
                ),
                event_sink=event_sink,
                code_owner=self.service.code_owner,
                no_trial_plans=no_trial,
                sold_out_file=path.parent / "founding-sold-out",
            )
            pro_handler = ProCheckoutHandler(state, self.pro, clicks=clicks)
            redeem_handler = RedeemHandler(state, self.pro, clicks=clicks)
            account_handler = AccountHandler(state, self.pro, clicks=clicks)
            free_role = os.environ.get("DISCORD_FREE_ROLE_ID")
            if free_role:
                from .free_role import FreeRoleSync

                roles = state.get("roles", {})
                self.free = FreeRoleSync(
                    discord_client, guild_id=state["guild_id"], free_role=free_role,
                    paid_roles=(roles.get("pro"), roles.get("trial")), event_sink=event_sink,
                )
        from .pro_access import CompositeHandler

        self.handler = CompositeHandler(
            referral_handler, pro_handler, redeem_handler, account_handler)

    def start(self):
        self.service.start()
        if self.pro is not None:
            self.pro.start()
        if self.free is not None:
            self.free.start()

    def stop(self):
        close_handler = getattr(self.handler, "close", None)
        if close_handler:
            close_handler()
        close_provider = getattr(self.service.provider, "close", None)
        if close_provider:
            close_provider()
        self.service.stop()
        if self.pro is not None:
            self.pro.stop()
        if self.free is not None:
            self.free.stop()

    def is_alive(self):
        return self.service.is_alive()

    def health(self):
        return {"configured": True, **self.service.health()}
