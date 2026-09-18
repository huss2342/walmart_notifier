"""ntfy push notifications; public push can be anonymous, email cannot."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta

import requests

from models import Item

from .base import format_message

log = logging.getLogger(__name__)

_PRIORITY = {"low": "2", "normal": "3", "high": "4", "urgent": "5"}
EMAIL_COOLDOWN_SECONDS = 24 * 60 * 60
_EMAIL_ONLY_ERROR_CODES = {40001, 40050, 40052, 40053, 42902}
_DAILY_MESSAGE_QUOTA_CODE = 42908


class NtfyNotifier:
    def __init__(self, topic: str, server: str = "https://ntfy.sh",
                 token: str | None = None, email: str | None = None):
        if not topic:
            raise ValueError("NTFY_TOPIC is required")
        self.topic = topic
        self.server = server.rstrip("/")
        self.token = token
        self.email = email
        self.last_diagnostic = ""
        self._email_suspended_until = 0.0
        self._email_pause_reason = ""
        self._publishing_suspended_until = 0.0
        if self.email and self.server == "https://ntfy.sh" and not self.token:
            # ntfy.sh no longer permits anonymous e-mail forwarding. Sending
            # the header only burns a request and rejects the phone push too.
            self._email_suspended_until = float("inf")
            self._email_pause_reason = (
                "NTFY_EMAIL needs a verified ntfy.sh account and NTFY_TOKEN; "
                "using push-only delivery"
            )
            self.last_diagnostic = self._email_pause_reason
        # Avoid several concurrent ingests all consuming an email-forwarding
        # attempt before the first HTTP 429 has opened the circuit breaker.
        self._send_lock = threading.Lock()

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
        with self._send_lock:
            return self._send_locked(item, priority, title, body)

    def _send_locked(self, item: Item, priority: str, title: str, body: str) -> bool:
        if time.time() < self._publishing_suspended_until:
            self.last_diagnostic = self._daily_quota_diagnostic()
            return False

        now = time.monotonic()
        email_enabled = bool(self.email and now >= self._email_suspended_until)
        headers = self._headers(item, priority, title, include_email=email_enabled)

        try:
            resp = self._post(body, headers)
            ntfy_code = self._error_code(resp)
            if email_enabled and ntfy_code in _EMAIL_ONLY_ERROR_CODES:
                self._email_suspended_until = now + EMAIL_COOLDOWN_SECONDS
                self._email_pause_reason = (
                    f"ntfy rejected email forwarding (code {ntfy_code}); email forwarding "
                    "is paused for 24 hours"
                )
                self.last_diagnostic = self._email_pause_reason + "; retrying push-only"
                log.warning("%s.", self.last_diagnostic)
                headers.pop("Email", None)
                resp = self._post(body, headers)
                resp.raise_for_status()
                self.last_diagnostic = self._email_pause_reason + "; push-only delivery succeeded"
                return True

            resp.raise_for_status()
            if self.email and not email_enabled:
                reason = self._email_pause_reason or "ntfy email forwarding is paused"
                self.last_diagnostic = reason + "; push-only delivery succeeded"
            else:
                self.last_diagnostic = ""
            return True
        except requests.RequestException as exc:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            code = self._error_code(response)
            if code == _DAILY_MESSAGE_QUOTA_CODE:
                tomorrow = datetime.now(UTC).date() + timedelta(days=1)
                self._publishing_suspended_until = datetime.combine(
                    tomorrow, datetime.min.time(), tzinfo=UTC
                ).timestamp()
                self.last_diagnostic = self._daily_quota_diagnostic()
            elif status is not None and code is not None:
                self.last_diagnostic = f"ntfy returned HTTP {status} (code {code})"
            elif status is not None:
                self.last_diagnostic = f"ntfy returned HTTP {status}"
            else:
                self.last_diagnostic = (
                    f"ntfy delivery failed ({type(exc).__name__})"
                )
            # Do not log the exception itself: requests exceptions may contain
            # the publish URL, whose final path segment is the secret topic.
            log.error("%s for item %s.", self.last_diagnostic, item.item_id)
            return False

    @staticmethod
    def _daily_quota_diagnostic() -> str:
        return (
            "ntfy daily message quota reached (code 42908); it resets at "
            "midnight UTC, or use an account/provider with a higher limit"
        )

    @staticmethod
    def _error_code(response) -> int | None:
        if response is None:
            return None
        try:
            code = response.json().get("code")
        except (AttributeError, TypeError, ValueError):
            return None
        return code if isinstance(code, int) and not isinstance(code, bool) else None

    def _headers(self, item: Item, priority: str, title: str,
                 include_email: bool) -> dict[str, str]:
        headers = {
            "Title": title.encode("ascii", "replace").decode(),
            "Priority": _PRIORITY.get(priority, "3"),
            "Tags": "shopping_cart",
        }
        if item.url:
            headers["Click"] = item.url
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if include_email and self.email:
            headers["Email"] = self.email
        return headers

    def _post(self, body: str, headers: dict[str, str]):
        return requests.post(
            f"{self.server}/{self.topic}",
            data=body.encode("utf-8"),
            headers=headers,
            timeout=10,
        )
