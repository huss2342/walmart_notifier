"""Real SDK routing keeps referral entry separate from private Pro requests."""

from types import SimpleNamespace

from discord_bridge.referral_ui import ReferralHandler

from test_discord_referral_ui import GUIDE, Interaction, Service
from test_discord_referral_ui import STATE as REFERRAL_STATE
from test_discord_request_commands import _gateway_scenario


def test_referral_component_works_in_public_guide_without_request_channel_guard(monkeypatch):
    async def scenario(sdk, tree, worker, client, store):
        await sdk.on_ready()
        interaction = Interaction()
        interaction.channel_id = int(GUIDE)
        service = Service(interaction=interaction)
        worker._referral_handler = ReferralHandler(REFERRAL_STATE, service)
        await sdk.on_interaction(interaction)
        assert service.calls and interaction.acknowledged
        assert "Your referral link" in interaction.messages[-1][0]
        assert interaction.messages[-1][1]["ephemeral"] is True
        assert client.calls == []

    _gateway_scenario(monkeypatch, scenario)


def test_referral_component_during_gateway_reconnect_does_not_call_billing(monkeypatch):
    async def scenario(sdk, tree, worker, client, store):
        interaction = Interaction()
        interaction.channel_id = int(GUIDE)
        service = Service(interaction=interaction)
        worker._referral_handler = ReferralHandler(REFERRAL_STATE, service)
        worker._connected = False
        await sdk.on_interaction(interaction)
        assert not service.calls
        assert "temporarily unavailable" in interaction.messages[-1][0]
        assert interaction.messages[-1][1]["ephemeral"] is True

    _gateway_scenario(monkeypatch, scenario)


def test_unknown_referral_prefix_does_not_intercept_existing_request_controls(monkeypatch):
    async def scenario(sdk, tree, worker, client, store):
        await sdk.on_ready()
        from test_discord_request_commands import Interaction as RequestInteraction

        service = Service()
        worker._referral_handler = ReferralHandler(REFERRAL_STATE, service)
        interaction = RequestInteraction()
        interaction.type = SimpleNamespace(value=3)
        interaction.data = {"custom_id": "request-ui:" + "a" * 32 + ":delete"}
        await sdk.on_interaction(interaction)
        assert "/request list" in interaction.messages[-1][0]
        assert not service.calls

    _gateway_scenario(monkeypatch, scenario)


def test_future_pro_activation_uses_the_same_referral_button(tmp_path):
    from test_discord_membership import PRO, USER, advance, make, member, posts

    worker, client, clock = make(tmp_path)
    worker.referrals_enabled = True
    worker.tick()
    client.members = [member(USER, PRO)]
    assert advance(worker, clock)["sent"] == 1
    payload = posts(client)[0][2]
    assert "30%" in payload["embeds"][0]["description"]
    assert any(
        button.get("custom_id") == "referral:link:v1"
        for row in payload["components"]
        for button in row["components"]
    )
