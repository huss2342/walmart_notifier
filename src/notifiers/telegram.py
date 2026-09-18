"""Telegram bot push notifications - free and instant."""

from __future__ import annotations

import logging
import os
import threading

import requests

from models import Item

from .base import format_message

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        if not bot_token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        self.bot_token = bot_token
        self.owner_chat_id = str(chat_id).strip()
        self._chat_id = self.owner_chat_id
        self._destination_lock = threading.RLock()
        self.last_diagnostic = ""

    @property
    def chat_id(self) -> str:
        with self._destination_lock:
            return self._chat_id

    def set_chat_id(self, chat_id: str) -> None:
        """Change the single delivery destination without changing the bot."""
        normalized = str(chat_id).strip()
        if not normalized:
            raise ValueError("Telegram chat ID cannot be empty")
        with self._destination_lock:
            self._chat_id = normalized

    @classmethod
    def from_env(cls) -> TelegramNotifier:
        return cls(
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        )

    def send(self, item: Item, priority: str = "normal") -> bool:
        title, body = format_message(item)
        return self.send_text(
            f"*{_escape(title)}*\n{_escape(body)}",
            parse_mode="MarkdownV2",
            disable_notification=priority == "low",
            log_context=f"item {item.item_id}",
        )

    def send_text(
        self,
        text: str,
        *,
        chat_id: str | None = None,
        parse_mode: str | None = None,
        disable_notification: bool = False,
        update_diagnostic: bool = True,
        log_context: str = "message",
    ) -> bool:
        """Send plain or pre-escaped text without exposing Telegram secrets.

        The status-command worker uses this method with its own diagnostic
        channel.  A failure to answer ``/status`` must not overwrite the last
        diagnostic for ordinary item alerts.
        """
        # Snapshot the destination so a group switch cannot split one request
        # between the old and new chats.
        destination = self.chat_id if chat_id is None else str(chat_id).strip()
        if not destination:
            raise ValueError("Telegram chat ID cannot be empty")
        request_payload: dict[str, object] = {
            "chat_id": destination,
            "text": text,
            "disable_notification": disable_notification,
        }
        if parse_mode:
            request_payload["parse_mode"] = parse_mode
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json=request_payload,
                timeout=10,
            )
            resp.raise_for_status()
            try:
                response_payload = resp.json()
            except (AttributeError, TypeError, ValueError):
                response_payload = None
            if (
                not isinstance(response_payload, dict)
                or response_payload.get("ok") is not True
            ):
                detail = _safe_failure_detail(
                    resp,
                    secrets=(self.bot_token, self.chat_id, destination),
                )
                if update_diagnostic:
                    self.last_diagnostic = detail
                log.error("%s while sending %s.", detail, log_context)
                return False
            if update_diagnostic:
                self.last_diagnostic = ""
            return True
        except requests.RequestException as exc:
            detail = _safe_failure_detail(
                getattr(exc, "response", None),
                exc,
                secrets=(self.bot_token, self.chat_id, destination),
            )
            # Never log the exception or request URL: Telegram embeds the bot
            # token in the API path, so requests' exception text can expose it.
            if update_diagnostic:
                self.last_diagnostic = detail
            log.error("%s while sending %s.", detail, log_context)
            return False


def _safe_failure_detail(
    response,
    exc: Exception | None = None,
    *,
    secrets: tuple[str, ...] = (),
) -> str:
    """Return useful Telegram failure text without its token-bearing URL."""
    status = getattr(response, "status_code", None)
    description = ""
    if response is not None:
        try:
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("description"), str):
                description = payload["description"].strip()
        except (AttributeError, TypeError, ValueError):
            pass
    for secret in secrets:
        if secret:
            description = description.replace(secret, "[redacted]")
    description = description[:300]
    if status is not None and description:
        detail = f"Telegram returned HTTP {status}: {description}"
    elif status is not None:
        detail = f"Telegram returned HTTP {status}"
    elif description:
        detail = f"Telegram delivery failed: {description}"
    else:
        suffix = f" ({type(exc).__name__})" if exc is not None else ""
        detail = f"Telegram delivery failed{suffix}"
    return detail


def _escape(text: str) -> str:
    for ch in "\\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, "\\" + ch)
    return text
