"""Common notifier interface."""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from models import Item

log = logging.getLogger(__name__)


def format_message(item: Item) -> tuple[str, str]:
    """Render an item as (title, body) for a phone notification."""
    if item.value_usd is not None:
        title = f"${item.value_usd:,.0f} - {item.title}"
    else:
        title = item.title or "New review item"
    lines = [item.title]
    if item.value_usd is not None:
        lines.append(f"Retail value: ${item.value_usd:,.2f}")
    if item.category:
        lines.append(f"Category: {item.category}")
    if item.url:
        lines.append(item.url)
    return title[:250], "\n".join(l for l in lines if l)[:1000]


@runtime_checkable
class Notifier(Protocol):
    @classmethod
    def from_env(cls) -> "Notifier": ...

    def send(self, item: Item, priority: str = "normal") -> bool:
        """Deliver one alert. Returns True on success."""
        ...


class NullNotifier:
    """Used when configuration is missing, so a misconfigured push channel
    degrades to a log line instead of crashing the whole polling run."""

    @classmethod
    def from_env(cls) -> "NullNotifier":
        return cls()

    def send(self, item: Item, priority: str = "normal") -> bool:
        log.warning("No notifier configured; would have alerted: %s", item.title)
        return False
