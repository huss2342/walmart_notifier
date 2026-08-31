"""Shared HTML/text extraction for reviewer-item content."""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from dataclasses import dataclass

from models import Item, parse_prices

# walmart.com/ip/<slug>/<id> or walmart.com/ip/<id>
ITEM_URL_RE = re.compile(
    r"https?://(?:www\.)?walmart\.com/ip/(?:[A-Za-z0-9\-%._]+/)?(\d{6,})[^\s\"'<>]*",
    re.I,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")

# How far back to look when a listing puts its price before the link.
_LOOKBEHIND = 400


def strip_html(markup: str) -> str:
    """Flatten HTML to text, keeping block boundaries as newlines."""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", markup)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h[1-6]|table)>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    return "\n".join(line.strip() for line in text.split("\n") if line.strip())


def _clean_title(raw: str) -> str:
    title = raw.strip(" \t-–—|·•,")
    title = re.sub(r"(?i)\b(claim|claim now|view item|shop now|free item|add to cart)\b", "", title)
    return _WS_RE.sub(" ", title).strip(" \t-–—|·•,")


@dataclass(slots=True)
class _Candidate:
    item_id: str
    url: str
    forward_text: str
    before_prices: list[float]
    after_prices: list[float]


def extract_items(content: str, source: str, is_html: bool = True) -> Iterator[Item]:
    """Best-effort extraction of items from an email body or page dump.

    Item links act as segment boundaries: the text between one item's link and
    the next item's link belongs to that item, and a lookbehind is clamped to
    the preceding item's link. Prices therefore never cross an item boundary.

    Which side of the link the price sits on is a property of the template, not
    of the individual item, so orientation is decided once for the whole
    document by seeing which side yields prices for more items. Deciding
    per-item instead is what breaks table layouts, where the previous item's
    price and the next item's price are both immediately adjacent to the link.

    Walmart's templates change without notice, so this degrades gracefully: an
    item whose price cannot be located still yields an Item with
    `value_usd=None` rather than being dropped.
    """
    matches = list(ITEM_URL_RE.finditer(content))
    if not matches:
        return

    # Collapse repeated links to the same item (image link + title link) while
    # preserving first-appearance order.
    first_seen: dict[str, re.Match[str]] = {}
    for match in matches:
        first_seen.setdefault(match.group(1), match)
    ordered = list(first_seen.items())

    def flatten(chunk: str) -> str:
        return strip_html(chunk) if is_html else chunk

    candidates: list[_Candidate] = []
    for index, (item_id, match) in enumerate(ordered):
        next_start = ordered[index + 1][1].start() if index + 1 < len(ordered) else len(content)
        forward = flatten(content[match.end() : next_start])

        prev_end = ordered[index - 1][1].end() if index else 0
        behind = flatten(content[max(prev_end, match.start() - _LOOKBEHIND) : match.start()])

        candidates.append(
            _Candidate(
                item_id=item_id,
                url=html.unescape(match.group(0)).rstrip(").,;\"'"),
                forward_text=forward,
                before_prices=parse_prices(behind),
                after_prices=parse_prices(forward),
            )
        )

    prefer_after = sum(bool(c.after_prices) for c in candidates) >= sum(
        bool(c.before_prices) for c in candidates
    )

    for candidate in candidates:
        # Within a direction, take the price nearest the link.
        primary = candidate.after_prices[:1] if prefer_after else candidate.before_prices[-1:]
        fallback = candidate.before_prices[-1:] if prefer_after else candidate.after_prices[:1]
        value = (primary or fallback or [None])[0]

        title = _title_from_slug(candidate.url) or _clean_title(
            candidate.forward_text.split("\n")[0] if candidate.forward_text else ""
        )
        yield Item(
            title=title or f"Item {candidate.item_id}",
            item_id=f"ip-{candidate.item_id}",
            value_usd=value,
            url=candidate.url,
            source=source,
        )


def _title_from_slug(url: str) -> str:
    slug = re.search(r"/ip/([A-Za-z0-9\-%._]+)/\d{6,}", url)
    if not slug:
        return ""
    words = slug.group(1).replace("-", " ").strip()
    if len(words) < 3:
        return ""
    return _clean_title(words[:200])
