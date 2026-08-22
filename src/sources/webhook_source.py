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
from typing import Any

from models import Item, parse_price
from .parsing import extract_items

log = logging.getLogger(__name__)

MAX_ITEMS = 200


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
