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
