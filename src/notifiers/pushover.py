"""Pushover push notifications - $5 one-time, supports DND bypass."""

from __future__ import annotations

import logging
import os

import requests

from models import Item
from .base import format_message

log = logging.getLogger(__name__)

_PRIORITY = {"low": -1, "normal": 0, "high": 1, "urgent": 2}
_API = "https://api.pushover.net/1/messages.json"


class PushoverNotifier:
    def __init__(self, token: str, user_key: str, device: str | None = None):
        if not token or not user_key:
            raise ValueError("PUSHOVER_TOKEN and PUSHOVER_USER_KEY are required")
        self.token = token
        self.user_key = user_key
        self.device = device

    @classmethod
    def from_env(cls) -> "PushoverNotifier":
        return cls(
            token=os.environ.get("PUSHOVER_TOKEN", ""),
            user_key=os.environ.get("PUSHOVER_USER_KEY", ""),
            device=os.environ.get("PUSHOVER_DEVICE") or None,
        )

    def send(self, item: Item, priority: str = "normal") -> bool:
        title, body = format_message(item)
        level = _PRIORITY.get(priority, 0)
        payload = {
            "token": self.token,
            "user": self.user_key,
            "title": title,
            "message": body,
        }
        if self.device:
            payload["device"] = self.device
        if item.url:
            payload["url"] = item.url
            payload["url_title"] = "Open item"
        if level:
            payload["priority"] = level
        if level == 2:
            # Emergency priority must specify retry/expire or the API rejects it.
            payload["retry"] = 60
            payload["expire"] = 600
        try:
            resp = requests.post(_API, data=payload, timeout=10)
            resp.raise_for_status()
            return True
        except requests.RequestException:
            log.exception("Pushover delivery failed for %s", item.item_id)
            return False
