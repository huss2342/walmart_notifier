"""Telegram /status command security, persistence, and failure isolation."""

import logging

import pytest
import requests

import telegram_commands
from notifiers.telegram import TelegramNotifier
from state import SeenStore
from telegram_commands import TelegramCommandPoller


def make_update(
    update_id=41,
    *,
    chat_id=456,
    sender_id=456,
    chat_type="private",
    text="/status",
):
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": sender_id},
            "text": text,
        },
    }


def make_poller(store=None):
    notifier = TelegramNotifier("123:top-secret", "456")
    return TelegramCommandPoller(
        notifier,
        store if store is not None else SeenStore(""),
        lambda: "healthy status",
        poll_timeout=1,
    )


@pytest.mark.parametrize("command", ["/status", "/STATUS", "/status@Walscanner_bot"])
def test_authorized_private_status_replies_and_persists_batch_offset(monkeypatch, command):
    store = SeenStore("")
    poller = make_poller(store)
    sent = []
    marker_writes = []
    original_set_marker = store.set_marker

    def record_marker(key, value):
        marker_writes.append((key, value))
        original_set_marker(key, value)

    monkeypatch.setattr(store, "set_marker", record_marker)
    monkeypatch.setattr(
        poller,
        "_api_call",
        lambda *args, **kwargs: (
            [make_update(41, text=command), make_update(42, chat_id=999)],
            "",
        ),
    )
    monkeypatch.setattr(
        poller.notifier,
        "send_text",
        lambda text, **kwargs: sent.append((text, kwargs)) or True,
    )

    assert poller._poll_once() is True
    assert sent[0][0] == "healthy status"
    assert sent[0][1]["update_diagnostic"] is False
    assert len(marker_writes) == 1
    assert marker_writes[0][1] == "43"

    # A fresh worker for the same bot resumes beyond the persisted batch.
    restarted = make_poller(store)
    assert restarted._next_offset == 43


@pytest.mark.parametrize(
    "update",
    [
        make_update(chat_id=999, sender_id=999),
        make_update(chat_type="group"),
        make_update(sender_id=999),
        make_update(text="hello"),
        make_update(text="/status now"),
    ],
)
def test_unauthorized_or_unknown_messages_are_silently_consumed(monkeypatch, update):
    poller = make_poller()
    sent = []
    monkeypatch.setattr(
        poller,
        "_api_call",
        lambda *args, **kwargs: ([update], ""),
    )
    monkeypatch.setattr(
        poller.notifier,
        "send_text",
        lambda *args, **kwargs: sent.append((args, kwargs)) or True,
    )

    assert poller._poll_once() is True
    assert sent == []
    assert poller._next_offset == 42


def test_prepare_registers_status_menu_only_for_configured_chat(monkeypatch):
    poller = make_poller()
    calls = []

    def fake_call(method, payload, *, timeout):
        calls.append((method, payload, timeout))
        return ({}, "") if method == "getWebhookInfo" else (True, "")

    monkeypatch.setattr(poller, "_api_call", fake_call)

    assert poller._prepare() is True
    method, payload, _ = calls[1]
    assert method == "setMyCommands"
    assert payload["commands"] == [
        {"command": "status", "description": "Check the reviewer relay"}
    ]
    assert payload["scope"] == {"type": "chat", "chat_id": "456"}


def test_owner_can_move_alerts_to_group_and_choice_survives_restart(monkeypatch):
    store = SeenStore("")
    poller = make_poller(store)
    sent = []

    def fake_call(method, payload, *, timeout):
        if method == "getUpdates":
            return (
                [
                    make_update(
                        chat_id=-100987,
                        sender_id=456,
                        chat_type="supergroup",
                        text="/usehere@Walscanner_bot",
                    )
                ],
                "",
            )
        return True, ""

    monkeypatch.setattr(poller, "_api_call", fake_call)
    monkeypatch.setattr(
        poller.notifier,
        "send_text",
        lambda text, **kwargs: sent.append((text, kwargs)) or True,
    )

    assert poller._poll_once() is True
    assert poller.notifier.chat_id == "-100987"
    assert sent[0][1]["chat_id"] == "-100987"
    assert "everyone here" in sent[0][0]

    restarted = make_poller(store)
    assert restarted.owner_chat_id == "456"
    assert restarted.notifier.chat_id == "-100987"
    assert restarted.health()["delivery_chat"] == "group"


@pytest.mark.parametrize(
    "update",
    [
        make_update(
            chat_id=-100987,
            sender_id=999,
            chat_type="group",
            text="/usehere",
        ),
        make_update(chat_id=456, sender_id=456, chat_type="private", text="/usehere"),
    ],
)
def test_only_owner_from_a_group_can_move_alerts(monkeypatch, update):
    poller = make_poller()
    sent = []
    monkeypatch.setattr(poller.notifier, "send_text", lambda *a, **k: sent.append(1) or True)

    poller._handle_update(update)

    assert poller.notifier.chat_id == "456"
    assert sent == []


def test_group_status_is_owner_only_and_replies_to_group(monkeypatch):
    poller = make_poller()
    poller.notifier.set_chat_id("-100987")
    sent = []
    monkeypatch.setattr(
        poller.notifier,
        "send_text",
        lambda text, **kwargs: sent.append((text, kwargs)) or True,
    )

    poller._handle_update(
        make_update(
            chat_id=-100987,
            sender_id=999,
            chat_type="group",
            text="/status",
        )
    )
    poller._handle_update(
        make_update(
            chat_id=-100987,
            sender_id=456,
            chat_type="group",
            text="/status",
        )
    )

    assert len(sent) == 1
    assert sent[0][0] == "healthy status"
    assert sent[0][1]["chat_id"] == "-100987"


def test_owner_can_restore_private_delivery_from_private_chat(monkeypatch):
    store = SeenStore("")
    poller = make_poller(store)
    poller.notifier.set_chat_id("-100987")
    sent = []
    monkeypatch.setattr(poller, "_register_status_menu", lambda: None)
    monkeypatch.setattr(
        poller.notifier,
        "send_text",
        lambda text, **kwargs: sent.append((text, kwargs)) or True,
    )

    poller._handle_update(
        make_update(chat_id=456, sender_id=456, chat_type="private", text="/useprivate")
    )

    assert poller.notifier.chat_id == "456"
    assert sent[0][1]["chat_id"] == "456"
    assert "private chat" in sent[0][0]
    assert make_poller(store).notifier.chat_id == "456"


def test_corrupt_saved_delivery_chat_falls_back_to_owner(caplog):
    store = SeenStore("")
    first = make_poller(store)
    store.set_marker(first._delivery_marker, "not-a-chat")
    caplog.set_level(logging.WARNING)

    restarted = make_poller(store)

    assert restarted.notifier.chat_id == "456"
    assert "invalid saved Telegram delivery chat" in caplog.text


def test_existing_webhook_disables_polling_without_deleting_it(monkeypatch):
    poller = make_poller()
    calls = []

    def fake_call(method, payload, *, timeout):
        calls.append(method)
        return {"url": "https://example.invalid/hook"}, ""

    monkeypatch.setattr(poller, "_api_call", fake_call)

    assert poller._prepare() is False
    assert calls == ["getWebhookInfo"]
    health = poller.health()
    assert health["webhook_configured"] is True
    assert "webhook" in health["diagnostic"]


def test_poll_errors_redact_token_and_chat_id(monkeypatch, caplog):
    token = "123:never-log-this"
    chat_id = "987654321"
    poller = TelegramCommandPoller(
        TelegramNotifier(token, chat_id),
        SeenStore(""),
        lambda: "ok",
        poll_timeout=1,
    )

    class Response:
        status_code = 401

        @staticmethod
        def json():
            return {
                "ok": False,
                "description": f"bad token {token} for chat {chat_id}",
            }

        def raise_for_status(self):
            raise requests.HTTPError(
                f"failure at https://api.telegram.org/bot{token}/getUpdates",
                response=self,
            )

    monkeypatch.setattr(telegram_commands.requests, "post", lambda *a, **k: Response())
    caplog.set_level(logging.WARNING)

    assert poller._poll_once() is False
    diagnostic = poller.health()["diagnostic"]
    assert token not in diagnostic
    assert chat_id not in diagnostic
    assert token not in caplog.text
    assert chat_id not in caplog.text


def test_telegram_conflict_has_actionable_safe_diagnostic(monkeypatch):
    poller = make_poller()
    monkeypatch.setattr(
        poller,
        "_api_call",
        lambda *args, **kwargs: (None, "Telegram returned HTTP 409: Conflict"),
    )

    assert poller._poll_once() is False
    assert "another poller or webhook" in poller.health()["diagnostic"]


def test_status_reply_failure_does_not_replace_alert_diagnostic(monkeypatch):
    poller = make_poller()
    poller.notifier.last_diagnostic = "last item alert failed"
    monkeypatch.setattr(poller.notifier, "send_text", lambda *a, **k: False)

    poller._handle_update(make_update())

    assert poller.notifier.last_diagnostic == "last item alert failed"
    assert "status reply" in poller.health()["diagnostic"]
