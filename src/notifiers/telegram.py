"""Telegram bot push notifications - free and instant."""

from __future__ import annotations

import logging
import os
import threading
import time

import requests

from models import Item

from .base import format_message

log = logging.getLogger(__name__)
DEFAULT_RATE_LIMIT_SECONDS = 30
MAX_RATE_LIMIT_SECONDS = 86400
MAX_RATE_LIMIT_DESTINATIONS = 32
_RATE_LIMIT_DIAGNOSTIC = "Telegram delivery is rate limited; waiting for retry window"


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        if not bot_token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        self.bot_token = bot_token
        self.owner_chat_id = str(chat_id).strip()
        self._chat_id = self.owner_chat_id
        self._destination_lock = threading.RLock()
        self._rate_limited_until: dict[str, float] = {}
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

    def _reap_rate_limits(self, now: float) -> None:
        expired = [chat for chat, until in self._rate_limited_until.items() if until <= now]
        for chat in expired:
            del self._rate_limited_until[chat]

    def _is_rate_limited(self, destination: str, update_diagnostic: bool) -> bool:
        with self._destination_lock:
            self._reap_rate_limits(time.monotonic())
            limited = destination in self._rate_limited_until
            if limited and update_diagnostic:
                self.last_diagnostic = _RATE_LIMIT_DIAGNOSTIC
            return limited

    def _defer_rate_limited(
        self, destination: str, seconds: int, update_diagnostic: bool,
    ) -> None:
        with self._destination_lock:
            now = time.monotonic()
            self._reap_rate_limits(now)
            if (destination not in self._rate_limited_until
                    and len(self._rate_limited_until) >= MAX_RATE_LIMIT_DESTINATIONS):
                earliest = min(self._rate_limited_until, key=self._rate_limited_until.get)
                del self._rate_limited_until[earliest]
            self._rate_limited_until[destination] = max(
                self._rate_limited_until.get(destination, 0), now + seconds,
            )
            if update_diagnostic:
                self.last_diagnostic = _RATE_LIMIT_DIAGNOSTIC
        # Use fixed text: provider descriptions and message context can carry
        # tokens, chat IDs, URLs or product text. No HTTP or sleep under lock.
        log.warning("Telegram rate limited delivery; retry deferred for %d seconds.", seconds)

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
        if self._is_rate_limited(destination, update_diagnostic):
            return False
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
            retry_after = _rate_limit_seconds(resp, response_payload)
            if retry_after is not None:
                self._defer_rate_limited(destination, retry_after, update_diagnostic)
                return False
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
            response = getattr(exc, "response", None)
            retry_after = _rate_limit_seconds(response)
            if retry_after is not None:
                self._defer_rate_limited(destination, retry_after, update_diagnostic)
                return False
            detail = _safe_failure_detail(
                response,
                exc,
                secrets=(self.bot_token, self.chat_id, destination),
            )
            # Never log the exception or request URL: Telegram embeds the bot
            # token in the API path, so requests' exception text can expose it.
            if update_diagnostic:
                self.last_diagnostic = detail
            log.error("%s while sending %s.", detail, log_context)
            return False


def _rate_limit_seconds(response, payload=None) -> int | None:
    if payload is None and response is not None:
        try:
            payload = response.json()
        except (AttributeError, TypeError, ValueError):
            payload = None
    json_limited = (
        isinstance(payload, dict) and payload.get("ok") is False
        and type(payload.get("error_code")) is int and payload["error_code"] == 429
    )
    if getattr(response, "status_code", None) != 429 and not json_limited:
        return None
    parameters = payload.get("parameters") if isinstance(payload, dict) else None
    seconds = parameters.get("retry_after") if isinstance(parameters, dict) else None
    if type(seconds) is int and 1 <= seconds <= MAX_RATE_LIMIT_SECONDS:
        return seconds
    return DEFAULT_RATE_LIMIT_SECONDS


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
