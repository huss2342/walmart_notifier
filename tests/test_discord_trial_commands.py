"""Owner trial controls authenticate fresh ownership and keep replies private."""

import asyncio
import copy
from types import SimpleNamespace

import pytest
from discord_bridge.api import DiscordError
from discord_bridge.trial_commands import (
    TrialCommands,
    build_trial_command_group,
    register_trial_commands,
    trial_command_definition,
)

from test_discord_request_commands import GUILD, OTHER, STATE, USER, Interaction

CHANNEL = "1700000000000000091"
TRIAL = "1700000000000000090"
TRIAL_STATE = copy.deepcopy(STATE)
TRIAL_STATE["roles"]["trial"] = TRIAL
TRIAL_STATE["channels"]["trial-management"] = CHANNEL


class Service:
    def __init__(self):
        self.calls = []
        self.rows = []
        self.synced = True
        self.config = {"enabled": False, "hours": 24}

    def grant(self, user, **kwargs):
        self.calls.append(("grant", user, kwargs))
        return {"user_id": user, "expires_at": 2000000000, "synced": self.synced}

    def extend(self, user, **kwargs):
        self.calls.append(("extend", user, kwargs))
        return {"user_id": user, "expires_at": 2000000000, "synced": self.synced}

    def revoke(self, user, **kwargs):
        self.calls.append(("revoke", user, kwargs))
        return {"user_id": user, "expires_at": 2000000000, "synced": self.synced}

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return self.rows

    def settings(self):
        self.calls.append(("settings",))
        return self.config

    def configure_welcome(self, enabled, **kwargs):
        self.calls.append(("welcome", enabled, kwargs))
        self.config = {"enabled": enabled, "hours": kwargs["hours"]}
        return self.config


class Client:
    def __init__(self):
        self.owner = USER
        self.calls = []
        self.error = None

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if self.error:
            raise self.error
        if path == f"/guilds/{GUILD}":
            return {"id": GUILD, "owner_id": self.owner}
        if path == "/oauth2/applications/@me":
            return {"id": STATE["bot_user_id"]}
        assert method == "POST" and path.endswith("/commands")
        return {"id": "1700000000000000099"}


def make(**interaction_kwargs):
    client, service = Client(), Service()
    interaction = Interaction(channel=CHANNEL, **interaction_kwargs)
    interaction.id = 1700000000000000099
    return TrialCommands(client, TRIAL_STATE, service), client, service, interaction


def run(awaitable):
    return asyncio.run(awaitable)


def test_owner_grant_uses_idempotent_interaction_and_no_mentions():
    handler, client, service, interaction = make()
    run(handler.grant(interaction, SimpleNamespace(id=int(OTHER)), duration="7d"))
    call = service.calls[0]
    assert call[0:2] == ("grant", OTHER)
    assert call[2] == {
        "seconds": 7 * 86400,
        "until": None,
        "actor_id": USER,
        "interaction_id": str(interaction.id),
    }
    assert client.calls[0][1] == f"/guilds/{GUILD}"
    assert "<t:2000000000:F>" in interaction.messages[-1][0]
    assert interaction.messages[-1][1]["allowed_mentions"].everyone is False


def test_sdk_failure_records_only_safe_classification_and_http_status():
    handler, client, service, interaction = make()
    events = []
    handler = TrialCommands(client, TRIAL_STATE, service,
                            event_sink=lambda event, **fields: events.append((event, fields)))
    error = SimpleNamespace(original=DiscordError("private-token-and-member-query", status=429))
    run(handler.error(interaction, error))
    assert events == [("worker_error", {"component": "trials", "phase": "worker",
                                       "error_type": "DiscordError", "outcome": "rejected",
                                       "status": 429})]
    assert "private-token" not in str(events) + str(interaction.messages)


@pytest.mark.parametrize("scope", [{"guild": None}, {"guild": OTHER}, {"channel": OTHER}])
def test_scope_failure_does_not_call_discord_or_service(scope):
    handler, client, service, interaction = make()
    for key, value in scope.items():
        setattr(interaction, key + "_id", int(value) if value else None)
    run(handler.grant(interaction, OTHER, duration="7d"))
    assert not client.calls and not service.calls
    assert interaction.messages and "trial-management" in interaction.messages[-1][0]


def test_owner_check_is_fresh_for_reads_and_writes():
    handler, client, service, interaction = make()
    client.owner = OTHER
    run(handler.list(interaction))
    assert not service.calls
    assert "owner" in interaction.messages[-1][0].lower()
    client.owner = USER
    run(handler.welcome(interaction))
    assert service.calls == [("settings",)]
    assert len(client.calls) == 2


def test_failed_acknowledgement_prevents_all_work():
    handler, client, service, interaction = make()

    async def fail(**kwargs):
        raise RuntimeError("token=private")

    interaction.response.defer = fail
    run(handler.grant(interaction, OTHER, duration="7d"))
    assert not client.calls and not service.calls and not interaction.messages


@pytest.mark.parametrize("duration", ["0h", "367d", "24", "nonsense", "-1h"])
def test_invalid_duration_never_reaches_service(duration):
    handler, _, service, interaction = make()
    run(handler.grant(interaction, OTHER, duration=duration))
    assert not service.calls
    assert interaction.messages


def test_existing_expiration_requires_explicit_timezone():
    handler, _, service, interaction = make()
    run(handler.grant(interaction, OTHER, until="2026-11-10T18:00:00"))
    assert not service.calls
    run(handler.grant(interaction, OTHER, until="2026-11-10T18:00:00-06:00"))
    assert service.calls[0][2]["until"] == 1794355200


def test_ambiguous_grant_options_are_rejected():
    handler, _, service, interaction = make()
    run(handler.grant(interaction, OTHER, duration="7d", until="2026-11-10T18:00:00Z"))
    assert not service.calls


def test_unsynced_grant_and_revoke_do_not_claim_access_confirmed():
    handler, _, service, interaction = make()
    service.synced = False
    run(handler.grant(interaction, OTHER, duration="8h"))
    assert "pending" in interaction.messages[-1][0].lower()
    run(handler.revoke(interaction, OTHER))
    assert "pending" in interaction.messages[-1][0].lower()


def test_provider_errors_are_private_and_redacted():
    handler, client, service, interaction = make()
    client.error = DiscordError("token=private", status=403)
    run(handler.list(interaction))
    assert not service.calls
    assert "private" not in interaction.messages[-1][0]


def test_list_is_bounded_and_welcome_can_switch_off_without_touching_grants():
    handler, _, service, interaction = make()
    service.rows = [
        {
            "user_id": OTHER,
            "expires_at": 2000000000,
            "revoked": False,
            "active": True,
            "synced": True,
        }
    ] * 15
    run(handler.list(interaction, page=2))
    assert service.calls[0] == ("list", {"offset": 15, "limit": 15})
    assert len(interaction.messages[-1][0]) <= 2000
    run(handler.welcome(interaction, enabled=True, hours=8))
    run(handler.welcome(interaction, enabled=False, hours=8))
    assert service.config == {"enabled": False, "hours": 8}
    assert not any(call[0] == "revoke" for call in service.calls)


def test_registration_matches_sdk_and_preserves_other_guild_commands():
    import discord

    handler, client, _, _ = make()
    group = build_trial_command_group(handler)
    tree = discord.app_commands.CommandTree(discord.Client(intents=discord.Intents.none()))
    actual = group.to_dict(tree)
    expected = trial_command_definition()
    assert str(actual["default_member_permissions"]) == "0"
    assert expected["default_member_permissions"] == "0"
    assert actual["options"] == expected["options"]
    register_trial_commands(client, TRIAL_STATE)
    assert [call[0] for call in client.calls] == ["GET", "POST"]
    assert client.calls[-1][1] == (f"/applications/{STATE['bot_user_id']}/guilds/{GUILD}/commands")
    assert client.calls[-1][2] == expected


@pytest.mark.parametrize("operation", ["grant", "extend", "revoke", "list", "welcome"])
def test_every_operation_rejects_non_owner(operation):
    handler, client, service, interaction = make()
    client.owner = OTHER
    operations = {
        "grant": lambda: handler.grant(interaction, OTHER, duration="8h"),
        "extend": lambda: handler.extend(interaction, OTHER, duration="8h"),
        "revoke": lambda: handler.revoke(interaction, OTHER),
        "list": lambda: handler.list(interaction),
        "welcome": lambda: handler.welcome(interaction, enabled=True),
    }
    run(operations[operation]())
    assert not service.calls
    assert "owner" in interaction.messages[-1][0].lower()


def test_acknowledgement_precedes_provider_work():
    handler, client, _, interaction = make()
    original = client.request

    def checked(method, path, payload=None):
        assert interaction.acknowledged
        return original(method, path, payload)

    client.request = checked
    run(handler.list(interaction))
    assert client.calls


def test_malformed_owner_response_cannot_authorize_owner():
    handler, client, service, interaction = make()
    client.owner = None
    run(handler.list(interaction))
    assert not service.calls
    assert "unavailable" in interaction.messages[-1][0]


def test_registration_rejects_a_different_application():
    _, client, _, _ = make()
    original = client.request

    def wrong_bot(method, path, payload=None):
        if path == "/oauth2/applications/@me":
            return {"id": OTHER}
        return original(method, path, payload)

    client.request = wrong_bot
    with pytest.raises(ValueError):
        register_trial_commands(client, TRIAL_STATE)
    assert not client.calls


def test_member_options_are_optional_for_batch_selection():
    definitions = {item["name"]: item["options"] for item in trial_command_definition()["options"]}
    for name in ("grant", "extend", "revoke"):
        member = next(option for option in definitions[name] if option["name"] == "member")
        assert member["required"] is False
    assert definitions["extend"][0]["name"] == "duration"
    assert definitions["extend"][0]["required"] is True


@pytest.mark.parametrize("operation", ["grant", "extend", "revoke"])
def test_omitting_member_opens_private_picker_without_changing_access(monkeypatch, operation):
    handler, _, service, interaction = make()
    opened = []

    async def picker(target, operation, *, duration=None, until=None):
        opened.append((target, operation, duration, until))

    monkeypatch.setattr(handler, "open_picker", picker)
    kwargs = {} if operation == "revoke" else {"duration": "7d"}
    run(getattr(handler, operation)(interaction, **kwargs))
    assert opened == [(interaction, operation, kwargs.get("duration"), None)]
    assert not service.calls


def test_component_authorization_binds_current_owner_and_original_creator():
    handler, client, service, interaction = make()
    assert run(handler.authorize(interaction, expected_actor=OTHER, thinking=False)) is None
    assert not service.calls
    assert "owner" in interaction.messages[-1][0].lower()
    client.owner = OTHER
    assert run(handler.authorize(interaction, expected_actor=USER, thinking=False)) is None
