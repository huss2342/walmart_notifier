"""Members can privately inspect only their own trial, without writes."""

import asyncio
import copy
from types import SimpleNamespace

import pytest
from discord_bridge.mytrial_commands import (
    MyTrialCommands,
    build_mytrial_command,
    mytrial_command_definition,
)
from discord_bridge.trials import TrialStore

from test_discord_request_commands import Interaction
from test_discord_trial_commands import GUILD, OTHER, TRIAL_STATE, USER

GENERAL = "1700000000000000098"


def setup(tmp_path):
    state = copy.deepcopy(TRIAL_STATE)
    state["channels"]["general"] = GENERAL
    clock = [1000.0]
    store = TrialStore(
        tmp_path / "trials.db",
        guild_id=GUILD,
        role_id=state["roles"]["trial"],
        clock=lambda: clock[0],
    )
    service = SimpleNamespace(store=store, clock=lambda: clock[0])
    target = Interaction(channel=GENERAL)
    roles, calls = [], []

    def request(method, path, payload=None):
        assert target.acknowledged
        assert method == "GET" and path == f"/guilds/{GUILD}/members/{USER}"
        calls.append(path)
        return {"user": {"id": USER}, "roles": list(roles)}

    handler = MyTrialCommands(SimpleNamespace(request=request), state, service)
    return handler, target, store, roles, calls, clock


@pytest.mark.parametrize("status", ["active", "expired", "revoked", "none"])
def test_own_status_is_private_and_read_only(tmp_path, status):
    handler, target, store, roles, calls, clock = setup(tmp_path)
    if status != "none":
        store.change(USER, operation="grant", actor_id=OTHER, interaction_id="grant", seconds=3600)
        roles.append(TRIAL_STATE["roles"]["trial"])
    if status == "expired":
        clock[0] += 4000
    if status == "revoked":
        store.change(USER, operation="revoke", actor_id=OTHER, interaction_id="revoke")
    before = store.get(USER)
    asyncio.run(handler.show(target))
    content, options = target.messages[-1]
    assert {
        "active": "ends",
        "expired": "expired",
        "revoked": "ended early",
        "none": "no recorded",
    }[status] in content.lower()
    assert options["ephemeral"] and not options["allowed_mentions"].users
    assert calls and store.get(USER) == before


@pytest.mark.parametrize(
    "scope", [{"guild_id": None}, {"guild_id": int(OTHER)}, {"channel_id": int(OTHER)}]
)
def test_wrong_scope_never_reads_member_or_ledger(tmp_path, scope):
    handler, target, _store, _roles, calls, _clock = setup(tmp_path)
    for key, value in scope.items():
        setattr(target, key, value)
    asyncio.run(handler.show(target))
    assert not calls and "#general" in target.messages[-1][0]


def test_separate_pro_does_not_invent_paid_expiry(tmp_path):
    handler, target, _store, roles, _calls, _clock = setup(tmp_path)
    roles.append(TRIAL_STATE["roles"]["pro"])
    asyncio.run(handler.show(target))
    content = target.messages[-1][0]
    assert "separate Pro access" in content and "no recorded" in content
    assert "subscription" not in content and "<t:" not in content


def test_public_schema_has_no_member_or_mutation_options(tmp_path):
    import discord

    handler, *_ = setup(tmp_path)
    tree = discord.app_commands.CommandTree(discord.Client(intents=discord.Intents.none()))
    actual = build_mytrial_command(handler).to_dict(tree)
    expected = mytrial_command_definition()
    assert actual["options"] == [] and actual["default_member_permissions"] is None
    assert expected["default_member_permissions"] is None and expected["options"] == []
