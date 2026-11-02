"""Referral configuration fails closed without interrupting ordinary alerts."""

import json

import pytest
from discord_bridge.referral_runtime import ReferralRuntime

STATE = {
    "guild_id": "1700000000000000001",
    "bot_user_id": "1700000000000000002",
    "channels": {"how-it-works": "1700000000000000003"},
}
CONFIG = {
    "guild_id": STATE["guild_id"],
    "company_id": "biz_referraltests",
    "product_ids": ["prod_referraltests"],
    "plan_prices": {"plan_referraltests": 3000},
    "checkout_plan_id": "plan_referraltests",
}


class Provider:
    def __init__(self, token, **kwargs):
        self.token, self.options = token, kwargs

    def list_payments(self):
        return []

    def list_promos(self):
        return []


def configuration(tmp_path, config=None):
    path = tmp_path / "referral-config.json"
    path.write_text(json.dumps(CONFIG if config is None else config), encoding="utf-8")
    (tmp_path / "whop-referrals.env").write_text(
        "WHOP_API_KEY=offline_test_secret\n", encoding="utf-8"
    )
    return path


def test_valid_configuration_builds_private_handler_and_separate_durable_store(tmp_path):
    runtime = ReferralRuntime(STATE, configuration(tmp_path), provider_factory=Provider)
    assert runtime.handler is not None
    assert runtime.service.store.path != tmp_path / "collector.sqlite3"
    assert "offline_test_secret" not in json.dumps(runtime.health())
    runtime.service.poll()
    assert runtime.health()["last_poll_at"] is not None


@pytest.mark.parametrize(
    "changes",
    [
        {"guild_id": "1700000000000000999"},
        {"company_id": "https://bad.example"},
        {"product_ids": []},
        {"plan_prices": {"plan_referraltests": -30}},
        {"plan_prices": {"plan_referraltests": True}},
        {"checkout_plan_id": "plan_retired"},
        {"api_key": "must_not_put_secrets_in_config"},
    ],
)
def test_invalid_config_does_not_enable_billing(tmp_path, changes):
    with pytest.raises(ValueError):
        ReferralRuntime(
            STATE, configuration(tmp_path, {**CONFIG, **changes}), provider_factory=Provider
        )


def test_missing_key_is_a_configuration_error_not_an_unauthenticated_api_call(tmp_path):
    path = configuration(tmp_path)
    (tmp_path / "whop-referrals.env").unlink()
    with pytest.raises(ValueError, match="credential"):
        ReferralRuntime(STATE, path, provider_factory=Provider)


def test_public_config_cannot_choose_an_arbitrary_credential_file(tmp_path):
    path = configuration(tmp_path, {**CONFIG, "key_path": "../../private.env"})
    with pytest.raises(ValueError):
        ReferralRuntime(STATE, path, provider_factory=Provider)


def test_future_price_uses_another_stable_plan_id_without_a_tier_display_name(tmp_path):
    config = {**CONFIG, "plan_prices": {**CONFIG["plan_prices"], "plan_futuretier": 9900}}
    runtime = ReferralRuntime(STATE, configuration(tmp_path, config), provider_factory=Provider)
    assert runtime.service.plan_prices["plan_futuretier"] == 9900


def test_preview_never_constructs_referral_billing_runtime(tmp_path):
    from discord_bridge.core import JobStore

    from notifiers.discord import DiscordNotifier

    notifier = DiscordNotifier(JobStore(tmp_path / "q"), referrals_enabled=True)
    assert notifier.referrals is None
    assert notifier.health()["referrals"]["enabled"] is False


def test_bad_live_referral_configuration_keeps_existing_delivery_available(tmp_path):
    from discord_bridge.core import JobStore

    from notifiers.discord import DiscordNotifier

    notifier = DiscordNotifier(
        JobStore(tmp_path / "q"),
        live=True,
        state=STATE,
        referrals_enabled=True,
        referrals_config_path=tmp_path / "missing.json",
    )
    assert notifier.live is True
    assert notifier.referrals is None
    assert notifier.health()["referrals"]["last_error"] == "configuration"
