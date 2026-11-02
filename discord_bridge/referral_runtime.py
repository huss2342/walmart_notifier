"""Private configuration and lifecycle for outbound-only referral billing."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .referral_store import ReferralStore
from .referral_ui import ReferralHandler
from .referral_worker import ReferralService
from .whop_client import WhopClient

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


class ReferralRuntime:
    def __init__(self, state, config_path, *, provider_factory=WhopClient, event_sink=None):
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
        provider = provider_factory(
            _credential(path.parent / "whop-referrals.env"),
            company_id=config["company_id"],
            product_ids=products,
            variant_ids=list(prices),
        )
        store = ReferralStore(
            path.parent / "referrals.sqlite3",
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
        )
        self.handler = ReferralHandler(state, self.service)

    def start(self):
        self.service.start()

    def stop(self):
        close_handler = getattr(self.handler, "close", None)
        if close_handler:
            close_handler()
        close_provider = getattr(self.service.provider, "close", None)
        if close_provider:
            close_provider()
        self.service.stop()

    def is_alive(self):
        return self.service.is_alive()

    def health(self):
        return {"configured": True, **self.service.health()}
