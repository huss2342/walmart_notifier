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
