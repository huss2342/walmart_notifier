"""Push-notification backends."""

from __future__ import annotations

import logging
import os

from .base import Notifier, NullNotifier
from .ntfy import NtfyNotifier
from .pushover import PushoverNotifier
from .telegram import TelegramNotifier

log = logging.getLogger(__name__)

_REGISTRY: dict[str, type[Notifier]] = {
    "ntfy": NtfyNotifier,
    "pushover": PushoverNotifier,
    "telegram": TelegramNotifier,
}


def build_notifier(provider: str | None = None) -> Notifier:
    """Instantiate the configured provider from environment variables."""
    name = (provider or os.environ.get("NOTIFY_PROVIDER") or "ntfy").strip().lower()
    cls = _REGISTRY.get(name)
    if cls is None:
        log.error("Unknown NOTIFY_PROVIDER %r; notifications disabled.", name)
        return NullNotifier()
    try:
        return cls.from_env()
    except Exception:
        log.exception("Could not configure %s; notifications disabled.", name)
        return NullNotifier()


__all__ = ["Notifier", "NullNotifier", "build_notifier", "_REGISTRY"]
