"""Telegram bot push notifications - free and instant."""

from __future__ import annotations

import logging
import os

import requests

from models import Item
from .base import format_message

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        if not bot_token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        self.bot_token = bot_token
        self.chat_id = chat_id

    @classmethod
    def from_env(cls) -> "TelegramNotifier":
        return cls(
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        )

    def send(self, item: Item, priority: str = "normal") -> bool:
        title, body = format_message(item)
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": f"*{_escape(title)}*\n{_escape(body)}",
                    "parse_mode": "MarkdownV2",
                    "disable_notification": priority == "low",
                },
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except requests.RequestException:
            log.exception("Telegram delivery failed for %s", item.item_id)
            return False


def _escape(text: str) -> str:
    for ch in r"_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, "\\" + ch)
    return text
