"""Parse items posted to the HTTP ingest endpoint.

Two things feed this endpoint:
  * the browser-extension companion, reading the reviewer page the user already
    has open in their own signed-in browser session;
  * an email-forwarding webhook (SendGrid Inbound Parse, Cloudflare Email
    Workers, etc.) for true push-latency delivery instead of IMAP polling.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

from models import Item, parse_price

from .parsing import extract_items

log = logging.getLogger(__name__)

MAX_ITEMS = 200

_STRING_LIMITS = {
    "title": 10_000,
    "item_id": 512,
    "url": 8_192,
    "image_url": 8_192,
    "category": 1_000,
    "source": 100,
    "first_seen": 100,
}


def parse_ingest_payload(body: bytes | str, source: str = "ingest") -> list[Item]:
    """Accept either structured JSON items or a raw HTML/text blob."""
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    body = body.strip()
    if not body:
        return []

    if body.startswith(("{", "[")):
        try:
            return _from_json(json.loads(body), source)
        except (json.JSONDecodeError, TypeError, ValueError):
            log.warning("Ingest payload looked like JSON but did not parse; "
                        "falling back to markup extraction.")

    return list(extract_items(body, source=source, is_html="<" in body))[:MAX_ITEMS]


def parse_json_ingest_payload(body: bytes | str, source: str = "ingest") -> list[Item]:
    """Strictly parse the JSON HTTP contract without silently dropping data.

    ``parse_ingest_payload`` remains intentionally forgiving for mail and
    markup callers.  A browser relay is different: acknowledging malformed or
    truncated JSON would let it advance past items the server never processed.
    """
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("request body must be valid UTF-8") from exc
    if not body.strip():
        raise ValueError("request body must not be empty")

    def reject_non_finite(name: str) -> None:
        raise ValueError(f"non-finite JSON number {name!r} is not allowed")

    try:
        data = json.loads(body, parse_constant=reject_non_finite)
    except json.JSONDecodeError as exc:
        raise ValueError("request body must be valid JSON") from exc
    except RecursionError as exc:
        raise ValueError("request body JSON nesting is too deep") from exc

    entries = data.get("items", [data]) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("items must be an array")
    if len(entries) > MAX_ITEMS:
        raise ValueError(f"items must contain at most {MAX_ITEMS} entries")

    return [_strict_json_item(entry, index, source) for index, entry in enumerate(entries)]


def _strict_json_item(entry: Any, index: int, source: str) -> Item:
    label = f"items[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{label} must be an object")

    payload = dict(entry)
    for field_name, limit in _STRING_LIMITS.items():
        if field_name not in payload:
            continue
        value = payload[field_name]
        if not isinstance(value, str):
            raise ValueError(f"{label}.{field_name} must be a string")
        if len(value) > limit:
            raise ValueError(f"{label}.{field_name} is too long (maximum {limit})")
        if "\r" in value or "\n" in value:
            raise ValueError(f"{label}.{field_name} must not contain line breaks")

    title = payload.get("title", "")
    url = payload.get("url", "")
    if not title.strip() and not url.strip():
        raise ValueError(f"{label} must include a non-empty title or url")

    item_id = payload.get("item_id", "")
    if item_id and not item_id.strip():
        raise ValueError(f"{label}.item_id must not be whitespace")

    if "value_usd" in payload and "price" in payload:
        raise ValueError(f"{label} must not include both value_usd and price")
    raw_value = payload.get("value_usd", payload.pop("price", None))
    if raw_value is None:
        value_usd = None
    elif isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise ValueError(f"{label}.value_usd must be a number or null")
    else:
        try:
            value_usd = float(raw_value)
        except OverflowError as exc:
            raise ValueError(
                f"{label}.value_usd must be a finite non-negative number"
            ) from exc
        if not math.isfinite(value_usd) or value_usd < 0:
            raise ValueError(f"{label}.value_usd must be a finite non-negative number")
    payload["value_usd"] = value_usd

    if "raw" in payload:
        raw = payload["raw"]
        if not isinstance(raw, dict):
            raise ValueError(f"{label}.raw must be an object")
        payload["raw"] = dict(raw)

    claims = payload.get("claims_remaining")
    if claims is not None and (
        isinstance(claims, bool) or not isinstance(claims, int) or claims < 0
    ):
        raise ValueError(f"{label}.claims_remaining must be a non-negative integer")
    if "query" in payload and not isinstance(payload["query"], str):
        raise ValueError(f"{label}.query must be a string")

    payload.setdefault("title", "")
    payload.setdefault("source", source)
    try:
        return Item.from_dict(payload)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid") from exc


def _from_json(data: Any, source: str) -> list[Item]:
    if isinstance(data, dict):
        # Accept {"items": [...]} as well as a bare single item.
        data = data.get("items", [data])
    if not isinstance(data, list):
        raise ValueError("expected a list of items")

    items: list[Item] = []
    for entry in data[:MAX_ITEMS]:
        if not isinstance(entry, dict):
            continue
        payload = dict(entry)
        payload.setdefault("source", source)
        # Tolerate the price arriving as "$129.99", 129.99, or "129".
        raw_value = payload.get("value_usd", payload.pop("price", None))
        payload["value_usd"] = (
            raw_value if isinstance(raw_value, (int, float)) else parse_price(raw_value)
        )
        if not payload.get("title") and not payload.get("url"):
            continue
        items.append(Item.from_dict(payload))
    return items
