"""Owner-authorized Telegram commands for checking the local notifier remotely.

The notifier is deliberately not reachable from the internet.  Telegram long
polling preserves that boundary: this worker makes outbound HTTPS requests and
accepts commands only from the configured owner's Telegram account. The owner
can securely move the single delivery destination into a private group so two
people receive each alert without duplicate sends.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime

import requests

from notifiers.telegram import TelegramNotifier, _safe_failure_detail
from state import SeenStore

log = logging.getLogger(__name__)

POLL_TIMEOUT_SECONDS = 8
MAX_BACKOFF_SECONDS = 60.0
CHAT_ID_PATTERN = re.compile(r"-?[1-9][0-9]*")


class TelegramCommandPoller:
    """Receive a tiny, authenticated command surface through Telegram."""

    def __init__(
        self,
        notifier: TelegramNotifier,
        store: SeenStore,
        status_text: Callable[[], str],
        *,
        filters_text: Callable[[], str] | None = None,
        poll_timeout: int = POLL_TIMEOUT_SECONDS,
    ):
        self.notifier = notifier
        self.store = store
        self.status_text = status_text
        self.filters_text = filters_text or _current_filters_text
        self.poll_timeout = max(1, int(poll_timeout))
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_poll_at: str | None = None
        self._last_command_at: str | None = None
        self._last_reply_at: str | None = None
        self._poll_diagnostic = ""
        self._reply_diagnostic = ""
        self._menu_diagnostic = ""
        self._webhook_configured = False

        # TELEGRAM_CHAT_ID starts as the private owner/control account. A
        # destination selected with /usehere is stored separately so the owner
        # remains the only account allowed to move it or request status.
        self.owner_chat_id = notifier.owner_chat_id

        # A digest makes the marker distinct per bot without writing any part
        # of the authentication token to the state file.
        bot_identity = notifier.bot_token.partition(":")[0]
        fingerprint = hashlib.sha256(bot_identity.encode("utf-8")).hexdigest()[:16]
        owner_fingerprint = hashlib.sha256(
            f"{bot_identity}:{self.owner_chat_id}".encode()
        ).hexdigest()[:16]
        self._offset_marker = f"telegram:update-offset:{fingerprint}"
        self._delivery_marker = f"telegram:delivery-chat:{owner_fingerprint}"
        self._restore_delivery_chat()
        self._next_offset = self._load_offset()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_guarded,
                name="telegram-status-commands",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self.poll_timeout + 3)

    def health(self) -> dict[str, object]:
        with self._lock:
            diagnostic = self._poll_diagnostic or self._reply_diagnostic
            return {
                "enabled": True,
                "running": self._running,
                "last_poll_at": self._last_poll_at,
                "last_command_at": self._last_command_at,
                "last_reply_at": self._last_reply_at,
                "diagnostic": diagnostic[:500],
                "menu_diagnostic": self._menu_diagnostic[:500],
                "webhook_configured": self._webhook_configured,
                "delivery_chat": (
                    "private"
                    if self.notifier.chat_id == self.owner_chat_id
                    else "group"
                ),
            }

    def _restore_delivery_chat(self) -> None:
        saved = self.store.get_marker(self._delivery_marker)
        if saved is None:
            return
        normalized = str(saved).strip()
        if CHAT_ID_PATTERN.fullmatch(normalized) is None:
            log.warning("Ignoring an invalid saved Telegram delivery chat.")
            return
        self.notifier.set_chat_id(normalized)

    def _load_offset(self) -> int | None:
        raw = self.store.get_marker(self._offset_marker)
        if raw is None:
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            log.warning("Ignoring an invalid saved Telegram update offset.")
            return None
        if value < 0:
            log.warning("Ignoring an invalid saved Telegram update offset.")
            return None
        return value

    def _save_offset(self, offset: int) -> None:
        self._next_offset = offset
        try:
            self.store.set_marker(self._offset_marker, str(offset))
        except Exception as exc:  # state failure must not kill alert delivery
            log.error(
                "Could not persist the Telegram update offset (%s).",
                type(exc).__name__,
            )

    def _run_guarded(self) -> None:
        with self._lock:
            self._running = True
        try:
            if not self._prepare():
                return
            delay = 1.0
            while not self._stop.is_set():
                if self._poll_once():
                    delay = 1.0
                    continue
                wait = min(MAX_BACKOFF_SECONDS, delay) * random.uniform(0.8, 1.2)
                self._stop.wait(wait)
                delay = min(MAX_BACKOFF_SECONDS, delay * 2)
        except Exception as exc:
            # Never render exception text here. A requests exception commonly
            # embeds the token-bearing Telegram URL.
            self._set_poll_diagnostic(
                f"Telegram command worker stopped unexpectedly ({type(exc).__name__})"
            )
            log.error("Telegram command worker stopped unexpectedly (%s).", type(exc).__name__)
        finally:
            with self._lock:
                self._running = False

    def _prepare(self) -> bool:
        result, detail = self._api_call("getWebhookInfo", {}, timeout=10)
        if result is None:
            # A transient check failure is not proof that a webhook exists.
            # getUpdates below will either work or return Telegram's conflict.
            log.warning("%s while checking Telegram command mode.", detail)
        elif isinstance(result, dict) and result.get("url"):
            with self._lock:
                self._webhook_configured = True
            self._set_poll_diagnostic(
                "Telegram command polling is disabled because this bot has a webhook configured"
            )
            log.error(
                "Telegram command polling is disabled because this bot has a webhook configured."
            )
            return False

        # This only controls the convenient command menu. Failure here must not
        # prevent typed commands or ordinary item notifications.
        self._register_status_menu()
        return True

    def _register_status_menu(self) -> None:
        commands = [
            {"command": "help", "description": "See what I can do"},
            {"command": "start", "description": "Show help and the alert destination"},
            {"command": "status", "description": "Check scanning and alert delivery"},
            {"command": "test", "description": "Send a labeled test to the alert destination"},
            {"command": "filters", "description": "See which items can send an alert"},
        ]
        destinations = [(self.owner_chat_id, "useprivate", "Send alerts to your private chat")]
        if self.notifier.chat_id != self.owner_chat_id:
            destinations.append((self.notifier.chat_id, "usehere", "Send alerts to this group"))
        details = []
        for chat_id, switch_command, description in destinations:
            _, menu_detail = self._api_call(
                "setMyCommands",
                {
                    "commands": commands
                    + [{"command": switch_command, "description": description}],
                    "scope": {"type": "chat", "chat_id": chat_id},
                },
                timeout=10,
            )
            if menu_detail:
                details.append(menu_detail)
        menu_detail = "; ".join(details)
        with self._lock:
            self._menu_diagnostic = menu_detail[:500]
        if menu_detail:
            log.warning("%s while registering the Telegram command menu.", menu_detail)

    def _poll_once(self) -> bool:
        payload: dict[str, object] = {
            "timeout": self.poll_timeout,
            "allowed_updates": ["message"],
        }
        if self._next_offset is not None:
            payload["offset"] = self._next_offset

        result, detail = self._api_call(
            "getUpdates",
            payload,
            timeout=self.poll_timeout + 5,
        )
        if result is None:
            if "HTTP 409" in detail:
                detail = (
                    "Telegram command polling conflict (HTTP 409); another poller or "
                    "webhook is using this bot"
                )
            self._set_poll_diagnostic(detail)
            log.warning("%s.", detail)
            return False
        if not isinstance(result, list):
            detail = "Telegram returned an invalid update list"
            self._set_poll_diagnostic(detail)
            log.warning("%s.", detail)
            return False

        with self._lock:
            self._last_poll_at = _now_iso()
            self._poll_diagnostic = ""

        highest_offset = self._next_offset
        for update in result:
            if not isinstance(update, dict):
                continue
            update_id = update.get("update_id")
            if isinstance(update_id, bool) or not isinstance(update_id, int):
                continue
            try:
                self._handle_update(update)
            except Exception as exc:
                # One broken command must not stop the worker or keep replaying
                # its entire batch. Exception text may contain bot credentials.
                failure = f"Telegram could not handle a command ({type(exc).__name__})"
                self._record_reply(False, failure)
                log.error("%s.", failure)
            finally:
                next_offset = update_id + 1
                if highest_offset is None or next_offset > highest_offset:
                    highest_offset = next_offset
        # SeenStore markers share the recorded-item JSON file. Persist once per
        # Telegram batch, not once per update, so unsolicited bot messages
        # cannot amplify into as many as 100 full state-file rewrites.
        if highest_offset is not None and highest_offset != self._next_offset:
            self._save_offset(highest_offset)
        return True

    def _handle_update(self, update: dict) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return

        chat_id = str(chat.get("id"))
        chat_type = chat.get("type")
        if str(sender.get("id")) != self.owner_chat_id:
            # The bot username is public. Unauthorized messages are silently
            # consumed so they reveal nothing and cannot turn this into a reply
            # oracle or notification spammer.
            return

        text = message.get("text")
        if not isinstance(text, str):
            return

        if _is_command(text, "usehere"):
            if chat_type not in {"group", "supergroup"}:
                return
            self._record_command()
            self._switch_delivery_chat(chat_id)
            return

        if _is_command(text, "useprivate"):
            if chat_type != "private" or chat_id != self.owner_chat_id:
                return
            self._record_command()
            self._switch_delivery_chat(self.owner_chat_id)
            return

        private_owner = chat_id == self.owner_chat_id and chat_type == "private"
        selected_group = (
            chat_id == self.notifier.chat_id
            and chat_type in {"group", "supergroup"}
            and chat_id != self.owner_chat_id
        )
        if not (private_owner or selected_group):
            return

        command = next(
            (
                name
                for name in ("help", "start", "status", "test", "filters")
                if _is_command(text, name)
            ),
            None,
        )
        if command is None:
            return
        self._record_command()
        if command == "test":
            self._send_delivery_test(chat_id)
            return
        if command in {"help", "start"}:
            reply = self._help_text(private_owner)
        else:
            try:
                reply = self.status_text() if command == "status" else self.filters_text()
            except Exception as exc:
                log.error("Could not build Telegram %s text (%s).", command, type(exc).__name__)
                reply = (
                    f"I'm online, but I couldn't build your {command} details. "
                    "Check the local notifier log, or use /test to check alert delivery."
                )

        delivered = self.notifier.send_text(
            reply[:4096],
            chat_id=chat_id,
            update_diagnostic=False,
            log_context=f"the {command} reply",
        )
        self._record_reply(delivered, f"Telegram could not deliver the last {command} reply")

    def _help_text(self, private_owner: bool) -> str:
        destination = (
            "your private chat"
            if self.notifier.chat_id == self.owner_chat_id
            else "your selected group"
        )
        switches = (
            "/useprivate — send alerts to this private chat.\n"
            "To share alerts, add me to a private group and send /usehere there."
            if private_owner
            else "/usehere — send alerts to this group.\n"
            "To switch back, send /useprivate in your private chat with me."
        )
        return (
            f"I'm watching for new Walmart Reviewer items. Alerts go to {destination}.\n\n"
            "/status — see whether scanning and notifications are working.\n"
            "/test — send a labeled test to the alert destination, without item filters.\n"
            "/filters — see your current alert rules.\n"
            "/help — show these commands again.\n\n"
            f"{switches}\n\n"
            "These commands are available to the configured owner. "
            "If alerts are quiet, start with /status and /test."
        )

    def _send_delivery_test(self, reply_chat_id: str) -> None:
        destination = self.notifier.chat_id
        delivered = self.notifier.send_text(
            "🔔 Delivery test — this is a test message, not a new Walmart item.\n"
            "Telegram delivery is working. This test bypasses your item filters and history.\n"
            "Use /status to check the Walmart page, or /filters to review your rules.",
            chat_id=destination,
            update_diagnostic=False,
            log_context="the delivery test",
        )
        self._record_reply(delivered, "Telegram could not deliver the last delivery test")
        if reply_chat_id == destination:
            return
        confirmation = (
            "✅ Test delivered to your alert group. New item alerts still depend on "
            "an active Walmart page and matching filters."
            if delivered
            else "I could not deliver the test to your alert group. Check that I'm still "
            "in the group and allowed to post, or use /useprivate to send alerts here."
        )
        confirmed = self.notifier.send_text(
            confirmation,
            chat_id=reply_chat_id,
            update_diagnostic=False,
            log_context="the delivery test confirmation",
        )
        if not confirmed:
            self._record_reply(False, "Telegram could not deliver the delivery test confirmation")

    def _record_command(self) -> None:
        with self._lock:
            self._last_command_at = _now_iso()

    def _record_reply(self, delivered: bool, failure: str) -> None:
        with self._lock:
            if delivered:
                self._last_reply_at = _now_iso()
                self._reply_diagnostic = ""
            else:
                self._reply_diagnostic = failure

    def _switch_delivery_chat(self, chat_id: str) -> None:
        try:
            self.store.set_marker(self._delivery_marker, chat_id)
            self.notifier.set_chat_id(chat_id)
        except Exception as exc:
            log.error(
                "Could not save the Telegram delivery destination (%s).",
                type(exc).__name__,
            )
            delivered = self.notifier.send_text(
                "Could not save that Telegram destination. Existing alerts were not changed.",
                chat_id=chat_id,
                update_diagnostic=False,
                log_context="the destination failure reply",
            )
            self._record_reply(
                delivered,
                "Telegram could not deliver the destination failure reply",
            )
            return

        is_private = chat_id == self.owner_chat_id
        reply = (
            "✅ Reviewer alerts now go to your private chat."
            if is_private
            else "✅ Reviewer alerts now go to this group, so everyone here can receive them."
        )
        delivered = self.notifier.send_text(
            reply,
            chat_id=chat_id,
            update_diagnostic=False,
            log_context="the destination confirmation",
        )
        self._record_reply(
            delivered,
            "Telegram could not deliver the destination confirmation",
        )
        # Refresh the menu for the newly selected chat. This is convenience
        # only; typed commands continue to work if Telegram rejects it.
        self._register_status_menu()

    def _api_call(
        self,
        method: str,
        payload: dict[str, object],
        *,
        timeout: int,
    ) -> tuple[object | None, str]:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.notifier.bot_token}/{method}",
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            try:
                body = response.json()
            except (AttributeError, TypeError, ValueError):
                body = None
            if not isinstance(body, dict) or body.get("ok") is not True:
                return None, _safe_failure_detail(
                    response,
                    secrets=(
                        self.notifier.bot_token,
                        self.owner_chat_id,
                        self.notifier.chat_id,
                    ),
                )
            return body.get("result"), ""
        except requests.RequestException as exc:
            return None, _safe_failure_detail(
                getattr(exc, "response", None),
                exc,
                secrets=(
                    self.notifier.bot_token,
                    self.owner_chat_id,
                    self.notifier.chat_id,
                ),
            )

    def _set_poll_diagnostic(self, detail: str) -> None:
        with self._lock:
            self._poll_diagnostic = detail[:500]


def disabled_command_health() -> dict[str, object]:
    return {
        "enabled": False,
        "running": False,
        "last_poll_at": None,
        "last_command_at": None,
        "last_reply_at": None,
        "diagnostic": "",
        "menu_diagnostic": "",
        "webhook_configured": False,
        "delivery_chat": None,
    }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _current_filters_text() -> str:
    from config import rules_snapshot

    rules, _ = rules_snapshot()
    lines = ["Your current alert rules (an item only needs to match one):"]
    for rule in rules:
        clauses = []
        if rule.min_value_usd is not None:
            clauses.append(f"value at least ${rule.min_value_usd:g}")
        if rule.max_value_usd is not None:
            clauses.append(f"value at most ${rule.max_value_usd:g}")
        if rule.keywords:
            mode = "all" if rule.match_all_keywords else "any"
            clauses.append(f"{mode} keywords: {', '.join(rule.keywords)}")
        if rule.exclude_keywords:
            clauses.append(f"exclude: {', '.join(rule.exclude_keywords)}")
        if rule.categories:
            clauses.append(f"categories: {', '.join(rule.categories)}")
        if rule.min_value_usd is not None or rule.max_value_usd is not None:
            clauses.append(
                "unknown values allowed"
                if rule.alert_on_unknown_value
                else "unknown values excluded"
            )
        lines.append(f"• {rule.name}: {'; '.join(clauses) if clauses else 'all items'}")
    summary = "\n".join(lines)
    if len(summary) > 3500:
        summary = summary[:3500] + "\n… More rules are listed in the extension options."
    return summary + "\n\nChange these in the extension options. Already alerted items are skipped."


def _is_command(text: str, command: str) -> bool:
    return re.fullmatch(
        rf"/{re.escape(command)}(?:@[a-z0-9_]{{5,32}})?",
        text.strip(),
        flags=re.IGNORECASE,
    ) is not None
