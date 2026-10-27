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


def test_prepare_registers_useful_menu_for_owner(monkeypatch):
    poller = make_poller()
    calls = []

    def fake_call(method, payload, *, timeout):
        calls.append((method, payload, timeout))
        return ({}, "") if method == "getWebhookInfo" else (True, "")

    monkeypatch.setattr(poller, "_api_call", fake_call)

    assert poller._prepare() is True
    method, payload, _ = calls[1]
    assert method == "setMyCommands"
    assert {entry["command"] for entry in payload["commands"]} == {
        "help", "start", "status", "test", "filters", "useprivate"
    }
    assert payload["scope"] == {"type": "chat", "chat_id": "456"}


def test_selected_group_menu_keeps_owner_private_menu(monkeypatch):
    poller = make_poller()
    poller.notifier.set_chat_id("-100987")
    calls = []
    monkeypatch.setattr(
        poller, "_api_call", lambda method, payload, **kwargs: calls.append(payload) or (True, "")
    )

    poller._register_status_menu()

    assert {payload["scope"]["chat_id"] for payload in calls} == {"456", "-100987"}
    menus = {
        payload["scope"]["chat_id"]: {entry["command"] for entry in payload["commands"]}
        for payload in calls
    }
    assert "useprivate" in menus["456"]
    assert "usehere" in menus["-100987"]
    assert {"help", "status", "test", "filters"}.issubset(menus["-100987"])


@pytest.mark.parametrize("command", ["/help", "/start", "/status", "/filters"])
def test_private_owner_commands_still_work_when_alerts_go_to_group(monkeypatch, command):
    poller = make_poller()
    poller.notifier.set_chat_id("-100987")
    sent = []
    monkeypatch.setattr(
        poller.notifier,
        "send_text",
        lambda text, **kwargs: sent.append((text, kwargs)) or True,
    )

    poller._handle_update(make_update(text=command))

    assert len(sent) == 1
    assert sent[0][1]["chat_id"] == "456"
    assert sent[0][1]["update_diagnostic"] is False
    assert sent[0][0]
    if command in {"/help", "/start"}:
        assert all(name in sent[0][0] for name in ("/status", "/test", "/filters"))
        assert "group" in sent[0][0].lower()


@pytest.mark.parametrize("command", ["/help", "/start", "/status", "/test", "/filters"])
@pytest.mark.parametrize("location", ["another-owner", "unselected-group"])
def test_new_commands_preserve_owner_and_destination_boundary(monkeypatch, command, location):
    poller = make_poller()
    poller.notifier.set_chat_id("-100987")
    sent = []
    monkeypatch.setattr(poller.notifier, "send_text", lambda *a, **k: sent.append(1) or True)
    update = (
        make_update(chat_id=999, sender_id=999, text=command)
        if location == "another-owner"
        else make_update(chat_id=-100222, sender_id=456, chat_type="group", text=command)
    )

    poller._handle_update(update)

    assert sent == []


def test_delivery_test_checks_group_destination_and_confirms_privately(monkeypatch):
    import config

    store = SeenStore("")
    poller = make_poller(store)
    poller.notifier.set_chat_id("-100987")
    sent = []
    monkeypatch.setattr(
        poller.notifier,
        "send_text",
        lambda text, **kwargs: sent.append((text, kwargs)) or True,
    )
    monkeypatch.setattr(config, "rules_snapshot", lambda: pytest.fail("A test must bypass filters"))

    poller._handle_update(make_update(text="/test"))

    assert len(sent) == 2
    assert sent[0][1]["chat_id"] == "-100987"
    assert "test" in sent[0][0].lower()
    assert "item" in sent[0][0].lower()
    assert sent[1][1]["chat_id"] == "456"
    assert "group" in sent[1][0].lower()
    assert "delivered" in sent[1][0].lower()
    assert len(store) == 0


def test_failed_group_delivery_test_reports_failure_to_private_owner(monkeypatch):
    poller = make_poller()
    poller.notifier.set_chat_id("-100987")
    sent = []
    monkeypatch.setattr(
        poller.notifier,
        "send_text",
        lambda text, **kwargs: sent.append((text, kwargs)) or kwargs["chat_id"] == "456",
    )

    poller._handle_update(make_update(text="/test"))

    assert len(sent) == 2
    assert "could not" in sent[1][0].lower()
    assert "test" in poller.health()["diagnostic"].lower()


@pytest.mark.parametrize("group", [False, True])
def test_repeatable_delivery_test_preserves_item_diagnostic(monkeypatch, group):
    poller = make_poller()
    chat_id = -100987 if group else 456
    if group:
        poller.notifier.set_chat_id(str(chat_id))
    poller.notifier.last_diagnostic = "last item failure"
    sent = []
    monkeypatch.setattr(
        poller.notifier,
        "send_text",
        lambda text, **kwargs: sent.append((text, kwargs)) or True,
    )

    for update_id in (41, 42):
        poller._handle_update(
            make_update(
                update_id, chat_id=chat_id, chat_type="group" if group else "private", text="/test"
            )
        )

    assert len(sent) == 2
    assert all(kwargs["chat_id"] == str(chat_id) for _, kwargs in sent)
    assert all(kwargs["update_diagnostic"] is False for _, kwargs in sent)
    assert poller.notifier.last_diagnostic == "last item failure"
    assert poller.health()["last_reply_at"] is not None
    assert poller.health()["diagnostic"] == ""


def test_menu_failure_is_reported_without_disabling_typed_commands(monkeypatch):
    poller = make_poller()

    def fake_call(method, payload, *, timeout):
        return ({}, "") if method == "getWebhookInfo" else (None, "Telegram returned HTTP 400")

    monkeypatch.setattr(poller, "_api_call", fake_call)
    sent = []
    monkeypatch.setattr(poller.notifier, "send_text", lambda *a, **k: sent.append(1) or True)

    assert poller._prepare() is True
    poller._handle_update(make_update(text="/help"))

    assert sent == [1]
    assert "HTTP 400" in poller.health()["menu_diagnostic"]


def test_filters_reports_live_rules_without_mutating_or_showing_priority(monkeypatch):
    import config
    from filters import Rule

    poller = make_poller()
    rules = [Rule(name="electronics", keywords=["TV"], min_value_usd=20, priority="low")]
    sent = []
    monkeypatch.setattr(config, "rules_snapshot", lambda: (rules, "user"))
    monkeypatch.setattr(
        poller.notifier,
        "send_text",
        lambda text, **kwargs: sent.append((text, kwargs)) or True,
    )

    poller._handle_update(make_update(text="/filters"))

    assert len(sent) == 1
    assert "electronics" in sent[0][0]
    assert "TV" in sent[0][0]
    assert "$20" in sent[0][0]
    assert "priority" not in sent[0][0].lower()
    assert "options" in sent[0][0].lower()
    assert rules[0].priority == "low"


def test_filters_callback_failure_is_safe_and_actionable(monkeypatch, caplog):
    def filters_text():
        raise RuntimeError("token 123:top-secret for chat 456")

    poller = TelegramCommandPoller(
        TelegramNotifier("123:top-secret", "456"),
        SeenStore(""),
        lambda: "healthy status",
        filters_text=filters_text,
    )
    sent = []
    monkeypatch.setattr(poller.notifier, "send_text", lambda text, **k: sent.append(text) or True)

    poller._handle_update(make_update(text="/filters"))

    assert len(sent) == 1
    assert "/test" in sent[0]
    assert "123:top-secret" not in caplog.text + sent[0]
    assert "chat 456" not in caplog.text + sent[0]


def test_one_broken_update_does_not_stop_batch_or_lose_offset(monkeypatch, caplog):
    poller = make_poller()
    sent = []
    monkeypatch.setattr(
        poller, "_api_call", lambda *args, **kwargs: ([make_update(41), make_update(42)], "")
    )

    def send(text, **kwargs):
        if not sent:
            sent.append("failed")
            raise RuntimeError("private token 123:top-secret, chat 456")
        sent.append(text)
        return True

    monkeypatch.setattr(poller.notifier, "send_text", send)
    caplog.set_level(logging.ERROR)

    assert poller._poll_once() is True
    assert sent == ["failed", "healthy status"]
    assert poller._next_offset == 43
    assert "123:top-secret" not in caplog.text
    assert "chat 456" not in caplog.text
    assert "RuntimeError" in caplog.text


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
