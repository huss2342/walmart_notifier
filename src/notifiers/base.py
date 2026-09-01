"""Common notifier interface."""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from models import Item

log = logging.getLogger(__name__)


def _money(value: float) -> str:
    """`$24.59`, but `$25` rather than `$25.00`.

    Rounding to whole dollars is wrong here: listings cluster between $5 and
    $30, so a $24.59 item shown as "$25" reads like it cleared a $25 rule that
    it actually failed.
    """
    return f"${value:,.2f}".removesuffix(".00")


def format_message(item: Item) -> tuple[str, str]:
    """Render an item as (title, body) for a phone notification."""
    if item.value_usd is not None:
        title = f"{_money(item.value_usd)} - {item.title}"
    else:
        title = item.title or "New review item"
    lines = [item.title]
    if item.value_usd is not None:
        lines.append(f"Retail value: ${item.value_usd:,.2f}")
    if item.category:
        lines.append(f"Category: {item.category}")

    # Whether there is a claim available decides whether this alert is
    # actionable right now or just interesting, so it belongs in the message
    # rather than in a log the user will never read.
    claims = item.raw.get("claims_remaining")
    if isinstance(claims, int):
        lines.append(
            "No claims left this cycle" if claims == 0
            else f"Claims remaining: {claims}"
        )

    query = item.raw.get("query")
    if query:
        lines.append(f"Search: {query}")
    if item.url:
        lines.append(item.url)
    return title[:250], "\n".join(line for line in lines if line)[:1000]


@runtime_checkable
class Notifier(Protocol):
    @classmethod
    def from_env(cls) -> Notifier: ...

    def send(self, item: Item, priority: str = "normal") -> bool:
        """Deliver one alert. Returns True on success."""
        ...


class NullNotifier:
    """Used when configuration is missing, so a misconfigured push channel
    degrades to a log line instead of crashing the whole polling run."""

    @classmethod
    def from_env(cls) -> NullNotifier:
        return cls()

    def send(self, item: Item, priority: str = "normal") -> bool:
        log.warning("No notifier configured; would have alerted: %s", item.title)
        return False
