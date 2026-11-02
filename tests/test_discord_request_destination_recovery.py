"""Stale DM destinations require an explicit test without replaying uncertain sends."""

import pytest
from discord_bridge.api import DeliveryOutcome, DiscordError
from discord_bridge.request_commands import RequestCommands

from test_discord_request_delivery import DM, MESSAGE, STATE, Provider, setup
from test_discord_requests import USER, observe, product

NEW_DM = "888888888888888888"
COMMAND_STATE = {**STATE, "channels": {"item-requests": "999999999999999999"}}


class DestinationProvider(Provider):
    def __init__(self):
        super().__init__()
        self.channel = DM

    def request(self, method, path, payload=None):
        if method == "POST" and path == "/users/@me/channels":
            self.calls.append((method, path, payload))
            return {"id": self.channel, "type": 1, "recipients": [{"id": USER}]}
        if method == "POST" and path == f"/channels/{self.channel}/messages":
            self.calls.append((method, path, payload))
            if self.send_error:
                raise self.send_error
            return {"id": MESSAGE}
        return super().request(method, path, payload)


@pytest.mark.parametrize("provider_code", [10003, None])
def test_rejected_cached_destination_pauses_reobservations_until_test_dm(tmp_path, provider_code):
    events = []
    store, _, provider, worker = setup(
        tmp_path, event_sink=lambda event, **fields: events.append({"event": event, **fields})
    )
    store.set_dm_status(USER, "ready", channel_id=DM)
    provider.send_error = DiscordError(status=404, provider_code=provider_code)

    result = worker.tick(now=12)

    assert result["blocked"] == 1 and result["failed"] == 0
    assert store.dm_status(USER) == "needs_test"
    assert store.stats()["reasons"]["failed"] == {"destination_missing": 1}
    assert worker.health()["last_error"] == "destination_missing"
    for at in (13, 14, 15):
        observe(store, [product()], now=at)
        assert worker.tick(now=at)["sent"] == 0
    assert len(provider.messages) == 1
    assert not any(path == "/users/@me/channels" for _, path, _ in provider.calls)
    assert len(store.list(USER)) == 1
    assert any(
        event["event"] == "dispatch_failed" and event.get("reason") == "destination_missing"
        for event in events
    )


def test_explicit_test_replaces_destination_and_restores_fresh_delivery(tmp_path):
    store, _, _, worker = setup(tmp_path)
    provider = DestinationProvider()
    worker.client = provider
    store.set_dm_status(USER, "ready", channel_id=DM)
    provider.send_error = DiscordError(status=404, provider_code=10003)
    assert worker.tick(now=12)["blocked"] == 1
    assert store.dm_status(USER) == "needs_test"

    provider.send_error = None
    provider.channel = NEW_DM
    commands = RequestCommands(provider, COMMAND_STATE, store)
    assert commands._test_delivery(USER) == "ready"
    assert "DMs are working" in provider.messages[-1]["content"]
    observe(store, [product(number="987654321")], now=13)

    assert worker.tick(now=14)["sent"] == 1
    assert provider.calls[-1][1] == f"/channels/{NEW_DM}/messages"
    assert provider.messages[-1]["embeds"][0]["footer"]["text"] == "ip-987654321"
    assert store.stats()["sent"] == 1
    assert store.stats()["failed"] == 1
    assert worker.health()["last_error"] is None


@pytest.mark.parametrize("outcome", [DeliveryOutcome.UNKNOWN, DeliveryOutcome.ACCEPTED])
def test_uncertain_destination_error_stays_held_after_explicit_test(tmp_path, outcome):
    store, _, _, worker = setup(tmp_path)
    provider = DestinationProvider()
    worker.client = provider
    store.set_dm_status(USER, "ready", channel_id=DM)
    provider.send_error = DiscordError(status=404, provider_code=10003, outcome=outcome)

    assert worker.tick(now=12)["review"] == 1
    assert store.dm_status(USER) == "ready"
    provider.send_error = None
    provider.channel = NEW_DM
    commands = RequestCommands(provider, COMMAND_STATE, store)
    assert commands._test_delivery(USER) == "ready"
    observe(store, [product(), product(number="987654321")], now=13)
    assert worker.tick(now=14)["sent"] == 1
    assert worker.tick(now=15)["sent"] == 0
    assert store.stats()["review"] == 1
    assert store.stats()["sent"] == 1
    assert len(provider.messages) == 3  # Original uncertain POST, explicit test, new product.
    assert provider.messages[-1]["embeds"][0]["footer"]["text"] == "ip-987654321"


@pytest.mark.parametrize("provider_code", [None, 10003, 10004])
def test_member_404_remains_unverified_without_changing_dm_readiness(tmp_path, provider_code):
    store, _, provider, worker = setup(tmp_path)
    provider.member_error = DiscordError(status=404, provider_code=provider_code)
    assert worker.tick(now=12)["retried"] == 1
    assert store.dm_status(USER) == "ready"
    assert store.stats()["reasons"]["pending"] == {"membership_unverified": 1}
    assert provider.messages == []


def test_audit_404_preserves_readiness_and_retries_membership_verification(tmp_path):
    def audit(*_args):
        raise DiscordError(status=404, provider_code=10003)

    store, _, provider, worker = setup(tmp_path, auditor=audit)
    assert worker.tick(now=12)["retried"] == 1
    assert store.dm_status(USER) == "ready"
    assert provider.messages == []
