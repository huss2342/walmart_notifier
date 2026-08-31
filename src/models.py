"""Core data model for a claimable reviewer item."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

_PRICE_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")


def parse_prices(text: str | None) -> list[float]:
    """Every dollar amount in a string, in order of appearance."""
    if not text:
        return []
    out: list[float] = []
    for raw in _PRICE_RE.findall(text):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def parse_price(text: str | None) -> float | None:
    """The first dollar amount in a string.

    Returns None when there is no recognisable amount, which callers treat as
    "unknown value" rather than "free" -- see Rule.min_value_usd.
    """
    prices = parse_prices(text)
    return prices[0] if prices else None


@dataclass(slots=True)
class Item:
    """A single item offered for review.

    `value_usd` is the item's *retail* value, not a purchase price. Items in the
    Recognized Reviewer program are free to claim, so a "minimum price" filter is
    really a minimum-retail-value filter: it is how you say "only wake me for the
    expensive stuff".
    """

    title: str
    item_id: str = ""
    value_usd: float | None = None
    url: str = ""
    image_url: str = ""
    category: str = ""
    source: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)
    first_seen: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )

    def __post_init__(self) -> None:
        self.title = (self.title or "").strip()
        if not self.item_id:
            self.item_id = self.fingerprint()

    def fingerprint(self) -> str:
        """Stable identity used for deduplication.

        Prefer the Walmart item id embedded in the URL; fall back to a hash of the
        title so that a retitled-but-identical listing does not re-alert.
        """
        from_url = re.search(r"/ip/(?:[^/]+/)?(\d{6,})", self.url or "")
        if from_url:
            return f"ip-{from_url.group(1)}"
        basis = self.title.lower()
        basis = re.sub(r"\s+", " ", basis).strip()
        return "t-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Item:
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        payload = {k: v for k, v in data.items() if k in known}
        if "value_usd" in payload and isinstance(payload["value_usd"], str):
            payload["value_usd"] = parse_price(payload["value_usd"])
        extra = {k: v for k, v in data.items() if k not in known}
        if extra:
            payload.setdefault("raw", {}).update(extra)
        return cls(**payload)
