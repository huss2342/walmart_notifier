import pytest
import requests

from models import Item
from notifiers import build_notifier
from notifiers.base import NullNotifier, format_message
from notifiers.ntfy import NtfyNotifier
from notifiers.pushover import PushoverNotifier
from notifiers.telegram import TelegramNotifier, _escape


class TestFormatMessage:
    def test_leads_with_value_when_known(self):
        title, _ = format_message(Item(title="4K Monitor", value_usd=249.0))
        assert title.startswith("$249")

    def test_falls_back_to_plain_title(self):
        title, _ = format_message(Item(title="Mystery Box"))
        assert title == "Mystery Box"

    def test_body_carries_url_and_category(self):
        _, body = format_message(
            Item(title="TV", value_usd=99.0, url="https://example.com/x", category="Electronics")
        )
        assert "https://example.com/x" in body and "Electronics" in body

    def test_fields_are_truncated(self):
        title, body = format_message(Item(title="x" * 5000, value_usd=1.0))
        assert len(title) <= 250 and len(body) <= 1000


class TestConstruction:
    def test_missing_config_raises(self):
        with pytest.raises(ValueError):
            NtfyNotifier(topic="")
        with pytest.raises(ValueError):
            PushoverNotifier(token="", user_key="")
        with pytest.raises(ValueError):
            TelegramNotifier(bot_token="abc", chat_id="")

    def test_build_notifier_selects_provider(self, monkeypatch):
        monkeypatch.setenv("NOTIFY_PROVIDER", "ntfy")
        monkeypatch.setenv("NTFY_TOPIC", "some-topic")
        assert isinstance(build_notifier(), NtfyNotifier)

    def test_unknown_provider_degrades_to_null(self, monkeypatch):
        monkeypatch.setenv("NOTIFY_PROVIDER", "carrier-pigeon")
        assert isinstance(build_notifier(), NullNotifier)

    def test_misconfigured_provider_degrades_to_null(self, monkeypatch):
        # A missing topic must not crash the whole polling run.
        monkeypatch.setenv("NOTIFY_PROVIDER", "ntfy")
        monkeypatch.delenv("NTFY_TOPIC", raising=False)
        assert isinstance(build_notifier(), NullNotifier)

    def test_null_notifier_reports_failure(self):
        assert NullNotifier().send(Item(title="X")) is False


def test_message_reports_claims_remaining():
    item = Item(title="Air fryer tray", value_usd=24.59,
                raw={"claims_remaining": 3, "query": "air fryer"})
    _, body = format_message(item)
    assert "Claims remaining: 3" in body
    assert "Search: air fryer" in body


def test_message_calls_out_having_no_claims_left():
    """Zero claims changes whether the alert is actionable, so it must be said."""
    item = Item(title="Air fryer tray", value_usd=24.59, raw={"claims_remaining": 0})
    _, body = format_message(item)
    assert "No claims left this cycle" in body


def test_message_omits_claims_when_unknown():
    _, body = format_message(Item(title="Air fryer tray", value_usd=24.59))
    assert "claims" not in body.lower()


def test_ntfy_adds_an_email_header_when_configured(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "topic")
    monkeypatch.setenv("NTFY_EMAIL", "you@example.com")
    monkeypatch.setenv("NTFY_TOKEN", "tk_account")
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured.update(headers)

        class Resp:
            def raise_for_status(self):
                pass
        return Resp()

    import notifiers.ntfy as ntfy_module
    monkeypatch.setattr(ntfy_module.requests, "post", fake_post)

    assert NtfyNotifier.from_env().send(Item(title="TV", value_usd=25.0)) is True
    assert captured["Email"] == "you@example.com"


def test_ntfy_sh_skips_email_without_an_account_token(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "topic")
    monkeypatch.setenv("NTFY_EMAIL", "you@example.com")
    monkeypatch.delenv("NTFY_TOKEN", raising=False)
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured.update(headers)

        class Resp:
            def json(self):
                return {}

            def raise_for_status(self):
                pass

        return Resp()

    import notifiers.ntfy as ntfy_module

    monkeypatch.setattr(ntfy_module.requests, "post", fake_post)
    notifier = NtfyNotifier.from_env()

    assert notifier.send(Item(title="TV")) is True
    assert "Email" not in captured
    assert "NTFY_TOKEN" in notifier.last_diagnostic


@pytest.mark.parametrize("email_error", [40053, 42902])
def test_ntfy_email_error_retries_push_only_and_opens_circuit(
    monkeypatch, caplog, email_error
):
    import notifiers.ntfy as ntfy_module

    class Resp:
        def __init__(self, status_code, code=None):
            self.status_code = status_code
            self.code = code

        def json(self):
            return {"code": self.code} if self.code is not None else {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)

    calls = []
    responses = iter([Resp(email_error // 100, email_error), Resp(200), Resp(200)])

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append(dict(headers))
        return next(responses)

    monkeypatch.setattr(ntfy_module.requests, "post", fake_post)
    notifier = NtfyNotifier(
        topic="super-secret-topic", email="private@example.com", token="tk_account"
    )
    item = Item(title="TV", item_id="ip-1")

    assert notifier.send(item) is True
    assert "Email" in calls[0]
    assert "Email" not in calls[1]
    assert "push-only delivery succeeded" in notifier.last_diagnostic

    # The next alert goes straight to push-only instead of spending another
    # request on an email-forwarding quota known to be exhausted.
    assert notifier.send(item) is True
    assert len(calls) == 3
    assert "Email" not in calls[2]
    assert "super-secret-topic" not in caplog.text
    assert "private@example.com" not in caplog.text


def test_ntfy_429_without_email_does_not_retry(monkeypatch):
    import notifiers.ntfy as ntfy_module

    class Resp:
        status_code = 429

        def raise_for_status(self):
            raise requests.HTTPError(response=self)

    calls = []

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append(dict(headers))
        return Resp()

    monkeypatch.setattr(ntfy_module.requests, "post", fake_post)
    notifier = NtfyNotifier(topic="secret-without-email")

    assert notifier.send(Item(title="TV", item_id="ip-1")) is False
    assert len(calls) == 1
    assert notifier.last_diagnostic == "ntfy returned HTTP 429"


def test_ntfy_daily_quota_does_not_retry_without_email(monkeypatch):
    import notifiers.ntfy as ntfy_module

    class Resp:
        status_code = 429

        def json(self):
            return {"code": 42908}

        def raise_for_status(self):
            raise requests.HTTPError(response=self)

    calls = []

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append(dict(headers))
        return Resp()

    monkeypatch.setattr(ntfy_module.requests, "post", fake_post)
    notifier = NtfyNotifier(topic="secret", email="private@example.com", token="tk_account")

    assert notifier.send(Item(title="TV", item_id="ip-1")) is False
    assert notifier.send(Item(title="TV 2", item_id="ip-2")) is False
    assert len(calls) == 1
    assert "midnight UTC" in notifier.last_diagnostic


def test_ntfy_email_circuit_closes_after_cooldown(monkeypatch):
    import notifiers.ntfy as ntfy_module

    calls = []

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append(dict(headers))
        return Resp()

    monkeypatch.setattr(ntfy_module.requests, "post", fake_post)
    now = [1000.0]
    monkeypatch.setattr(ntfy_module.time, "monotonic", lambda: now[0])
    notifier = NtfyNotifier(topic="secret", email="private@example.com", token="tk_account")
    notifier._email_suspended_until = now[0] + ntfy_module.EMAIL_COOLDOWN_SECONDS

    assert notifier.send(Item(title="first")) is True
    assert "Email" not in calls[0]

    now[0] += ntfy_module.EMAIL_COOLDOWN_SECONDS + 1
    assert notifier.send(Item(title="second")) is True
    assert calls[1]["Email"] == "private@example.com"


def test_ntfy_omits_the_email_header_by_default(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "topic")
    monkeypatch.delenv("NTFY_EMAIL", raising=False)
    assert NtfyNotifier.from_env().email is None


def test_title_keeps_cents_so_thresholds_are_not_misread():
    """$24.59 must not render as "$25" beside a $25 rule."""
    title, _ = format_message(Item(title="Air fryer tray", value_usd=24.59))
    assert title.startswith("$24.59 - ")


def test_title_drops_pointless_trailing_zeros():
    title, _ = format_message(Item(title="Apron", value_usd=15.00))
    assert title.startswith("$15 - ")


def test_telegram_sends_to_private_chat_and_low_priority_is_silent(monkeypatch):
    import notifiers.telegram as telegram_module

    captured = {}

    class Resp:
        status_code = 200

        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return {"ok": True, "result": {"message_id": 1}}

    def fake_post(url, json=None, timeout=None):
        captured.update(url=url, json=json, timeout=timeout)
        return Resp()

    monkeypatch.setattr(telegram_module.requests, "post", fake_post)
    notifier = TelegramNotifier("123:secret", "456")

    assert notifier.send(Item(title="TV", item_id="ip-1"), priority="low") is True
    assert captured["url"].endswith("/bot123:secret/sendMessage")
    assert captured["json"]["chat_id"] == "456"
    assert captured["json"]["disable_notification"] is True
    assert notifier.last_diagnostic == ""


def test_telegram_can_switch_one_snapshotted_delivery_destination(monkeypatch):
    import notifiers.telegram as telegram_module

    destinations = []

    class Resp:
        status_code = 200

        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return {"ok": True, "result": {"message_id": 1}}

    def fake_post(url, json=None, timeout=None):
        destinations.append(json["chat_id"])
        return Resp()

    monkeypatch.setattr(telegram_module.requests, "post", fake_post)
    notifier = TelegramNotifier("123:secret", "456")

    notifier.set_chat_id("-100987")
    assert notifier.send(Item(title="TV", item_id="ip-group")) is True
    assert notifier.send_text("owner reply", chat_id="456") is True

    assert notifier.owner_chat_id == "456"
    assert destinations == ["-100987", "456"]


def test_telegram_http_failure_never_logs_token_or_chat_id(monkeypatch, caplog):
    import notifiers.telegram as telegram_module

    token = "123:never-log-this-token"
    chat_id = "987654321"

    class Resp:
        status_code = 401

        @staticmethod
        def json():
            return {"ok": False, "description": "Unauthorized"}

        def raise_for_status(self):
            raise requests.HTTPError(
                f"401 for https://api.telegram.org/bot{token}/sendMessage",
                response=self,
            )

    monkeypatch.setattr(telegram_module.requests, "post", lambda *args, **kwargs: Resp())
    notifier = TelegramNotifier(token, chat_id)

    assert notifier.send(Item(title="TV", item_id="ip-2")) is False
    assert notifier.last_diagnostic == "Telegram returned HTTP 401: Unauthorized"
    assert token not in caplog.text
    assert chat_id not in caplog.text


def test_telegram_markdown_escape_includes_existing_backslashes():
    assert _escape(r"a\b_c") == r"a\\b\_c"


class TelegramResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = {"ok": True} if payload is None else payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("private request URL must not be logged", response=self)


def test_telegram_one_rate_limit_suppresses_one_hundred_sends_until_expiry(monkeypatch):
    import notifiers.telegram as telegram_module

    clock = [1000.0]
    calls = []
    responses = iter([
        TelegramResponse(429, {"ok": False, "parameters": {"retry_after": 45}}),
        TelegramResponse(),
    ])

    def post(*args, **kwargs):
        calls.append(kwargs["json"]["chat_id"])
        return next(responses)

    monkeypatch.setattr(telegram_module.requests, "post", post)
    monkeypatch.setattr(telegram_module.time, "monotonic", lambda: clock[0])
    notifier = TelegramNotifier("private-token", "group")
    assert notifier.send_text("first") is False
    assert all(notifier.send_text("retry") is False for _ in range(100))
    assert calls == ["group"]
    clock[0] += 44.99
    assert notifier.send_text("still waiting") is False
    clock[0] += 0.01
    assert notifier.send_text("retry after expiry") is True
    assert calls == ["group", "group"]
    assert notifier.last_diagnostic == ""


def test_telegram_group_cooldown_does_not_block_owner_private_chat_or_erase_item_diagnostic(
    monkeypatch,
):
    import notifiers.telegram as telegram_module

    calls = []

    def post(*args, **kwargs):
        destination = kwargs["json"]["chat_id"]
        calls.append(destination)
        if destination == "group":
            return TelegramResponse(429, {"ok": False, "parameters": {"retry_after": 60}})
        return TelegramResponse()

    monkeypatch.setattr(telegram_module.requests, "post", post)
    notifier = TelegramNotifier("private-token", "owner")
    notifier.set_chat_id("group")
    assert notifier.send_text("group alert") is False
    alert_diagnostic = notifier.last_diagnostic
    assert notifier.send_text("owner status", chat_id="owner", update_diagnostic=False) is True
    assert notifier.last_diagnostic == alert_diagnostic
    notifier.last_diagnostic = "previous alert diagnostic"
    assert notifier.send_text("group status", update_diagnostic=False) is False
    assert notifier.last_diagnostic == "previous alert diagnostic"
    assert calls == ["group", "owner"]


@pytest.mark.parametrize("hint", [None, True, False, 0, -1, 86401, 5.5, "60", [], {},
                                 float("nan"), float("inf")])
def test_telegram_malformed_retry_hint_uses_thirty_second_fallback(monkeypatch, hint):
    import notifiers.telegram as telegram_module

    clock = [1000.0]
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return TelegramResponse(429, {"ok": False, "parameters": {"retry_after": hint}})
        return TelegramResponse()

    monkeypatch.setattr(telegram_module.requests, "post", post)
    monkeypatch.setattr(telegram_module.time, "monotonic", lambda: clock[0])
    notifier = TelegramNotifier("token", "group")
    assert notifier.send_text("first") is False
    clock[0] += 29
    assert notifier.send_text("waiting") is False
    assert len(calls) == 1
    clock[0] += 1
    assert notifier.send_text("ready") is True


def test_telegram_http_200_json_rate_limit_uses_provider_hint(monkeypatch):
    import notifiers.telegram as telegram_module

    clock = [1000.0]
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return TelegramResponse(200, {
            "ok": False, "error_code": 429, "parameters": {"retry_after": 86400},
        })

    monkeypatch.setattr(telegram_module.requests, "post", post)
    monkeypatch.setattr(telegram_module.time, "monotonic", lambda: clock[0])
    notifier = TelegramNotifier("token", "group")
    assert notifier.send_text("first") is False
    clock[0] += 86399
    assert notifier.send_text("waiting") is False
    assert len(calls) == 1


def test_telegram_rate_limit_logs_do_not_include_provider_description_or_message_context(
    monkeypatch, caplog,
):
    import notifiers.telegram as telegram_module

    payload = {
        "ok": False, "error_code": 429, "parameters": {"retry_after": 5},
        "description": "https://private.example/private-token owner group PRIVATE PRODUCT",
    }
    monkeypatch.setattr(telegram_module.requests, "post",
                        lambda *args, **kwargs: TelegramResponse(429, payload))
    notifier = TelegramNotifier("private-token", "owner")
    notifier.last_diagnostic = "preserve this"
    assert notifier.send_text("PRIVATE PRODUCT", chat_id="group", update_diagnostic=False,
                              log_context="PRIVATE PRODUCT") is False
    assert notifier.last_diagnostic == "preserve this"
    assert notifier.send_text("PRIVATE PRODUCT", chat_id="group") is False
    for forbidden in ("private-token", "owner", "group", "PRIVATE PRODUCT", "https://"):
        assert forbidden not in caplog.text
        assert forbidden not in notifier.last_diagnostic


def test_telegram_pipeline_rate_limit_keeps_all_items_retryable_then_delivers(monkeypatch):
    import notifiers.telegram as telegram_module
    from filters import Rule
    from pipeline import process
    from state import SeenStore

    clock = [1000.0]
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return TelegramResponse(429, {"ok": False, "parameters": {"retry_after": 10}})
        return TelegramResponse()

    monkeypatch.setattr(telegram_module.requests, "post", post)
    monkeypatch.setattr(telegram_module.time, "monotonic", lambda: clock[0])
    notifier = TelegramNotifier("token", "group")
    store = SeenStore(":memory:")
    items = [Item(item_id=f"item-{i}", title="Example", value_usd=25) for i in range(100)]
    first = process(items, [Rule()], store, notifier)
    assert first.failed == 100
    assert first.notified == 0
    assert len(calls) == 1
    assert all(store.is_new(item.item_id) for item in items)
    clock[0] += 10
    retried = process(items, [Rule()], store, notifier)
    assert retried.notified == 100
    assert all(not store.is_new(item.item_id) for item in items)


def test_telegram_inflight_concurrent_rate_limits_keep_longest_deadline_without_network_lock(
    monkeypatch,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    import notifiers.telegram as telegram_module

    clock = [1000.0]
    barrier = Barrier(2)
    calls = []

    def post(*args, **kwargs):
        text = kwargs["json"]["text"]
        calls.append(text)
        if text == "after expiry":
            return TelegramResponse()
        barrier.wait(timeout=3)
        return TelegramResponse(429, {
            "ok": False, "parameters": {"retry_after": int(text)},
        })

    monkeypatch.setattr(telegram_module.requests, "post", post)
    monkeypatch.setattr(telegram_module.time, "monotonic", lambda: clock[0])
    notifier = TelegramNotifier("token", "group")
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(notifier.send_text, ["30", "120"])) == [False, False]
    clock[0] += 31
    assert notifier.send_text("still waiting") is False
    assert len(calls) == 2
    clock[0] += 89
    assert notifier.send_text("after expiry") is True


def test_telegram_cooldown_memory_is_bounded_and_expired_entries_are_reaped(monkeypatch):
    import notifiers.telegram as telegram_module

    clock = [1000.0]

    def post(*args, **kwargs):
        if kwargs["json"]["text"] == "success":
            return TelegramResponse()
        return TelegramResponse(429, {"ok": False, "parameters": {"retry_after": 30}})

    monkeypatch.setattr(telegram_module.requests, "post", post)
    monkeypatch.setattr(telegram_module.time, "monotonic", lambda: clock[0])
    notifier = TelegramNotifier("token", "owner")
    for index in range(100):
        assert notifier.send_text("limited", chat_id=f"chat-{index}") is False
        assert len(notifier._rate_limited_until) <= 32
    clock[0] += 30
    assert notifier.send_text("success", chat_id="owner") is True
    assert notifier._rate_limited_until == {}


@pytest.mark.parametrize("parameters", [None, "private raw data", [], {}])
def test_telegram_missing_or_invalid_parameters_use_fallback(monkeypatch, parameters):
    import notifiers.telegram as telegram_module

    clock = [1000.0]
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return TelegramResponse(429, {"ok": False, "parameters": parameters})
        return TelegramResponse()

    monkeypatch.setattr(telegram_module.requests, "post", post)
    monkeypatch.setattr(telegram_module.time, "monotonic", lambda: clock[0])
    notifier = TelegramNotifier("token", "group")
    assert notifier.send_text("first") is False
    clock[0] += 29
    assert notifier.send_text("waiting") is False
    clock[0] += 1
    assert notifier.send_text("ready") is True
    assert len(calls) == 2
