import pytest

from models import Item
from notifiers import build_notifier
from notifiers.base import NullNotifier, format_message
from notifiers.ntfy import NtfyNotifier
from notifiers.pushover import PushoverNotifier
from notifiers.telegram import TelegramNotifier


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
