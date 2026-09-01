"""ntfy.sh push notifications - free, instant, no account required."""

from __future__ import annotations

import logging
import os

import requests

from models import Item

from .base import format_message

log = logging.getLogger(__name__)

_PRIORITY = {"low": "2", "normal": "3", "high": "4", "urgent": "5"}


class NtfyNotifier:
    def __init__(self, topic: str, server: str = "https://ntfy.sh",
                 token: str | None = None, email: str | None = None):
        if not topic:
            raise ValueError("NTFY_TOPIC is required")
        self.topic = topic
        self.server = server.rstrip("/")
        self.token = token
        self.email = email

    @classmethod
    def from_env(cls) -> NtfyNotifier:
        return cls(
            topic=os.environ.get("NTFY_TOPIC", ""),
            server=os.environ.get("NTFY_SERVER", "https://ntfy.sh"),
            token=os.environ.get("NTFY_TOKEN") or None,
            email=os.environ.get("NTFY_EMAIL") or None,
        )

    def send(self, item: Item, priority: str = "normal") -> bool:
        title, body = format_message(item)
        headers = {
            "Title": title.encode("ascii", "replace").decode(),
            "Priority": _PRIORITY.get(priority, "3"),
            "Tags": "shopping_cart",
        }
        if item.url:
            headers["Click"] = item.url
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.email:
            # ntfy forwards the message to this address as well as to any
            # subscribed app, for people who would rather get mail than a push.
            headers["Email"] = self.email
        try:
            resp = requests.post(
                f"{self.server}/{self.topic}",
                data=body.encode("utf-8"),
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except requests.RequestException:
            log.exception("ntfy delivery failed for %s", item.item_id)
            return False
