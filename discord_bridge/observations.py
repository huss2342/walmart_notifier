"""Validate public product observations and select their canonical feed routes."""

from __future__ import annotations

import math
import re
from decimal import Decimal
from urllib.parse import urlencode, urlsplit

from .policy import (
    FREE_ROUTE_KEY,
    PRICE_BANDS,
    PRODUCT_CATEGORIES,
    TOP_PRICE_BAND,
)

CATEGORIES = PRODUCT_CATEGORIES
MAX_BATCH = 500
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
        band = next((name for maximum, name in PRICE_BANDS if value < maximum), TOP_PRICE_BAND)
        routes.append(f"price-{band}")
    return [*routes, FREE_ROUTE_KEY]
