"""Deleting the retired Starter role must not stop active Free/Pro delivery."""

import copy

import pytest
from discord_bridge.layout import PROVISION_PERMISSIONS
from discord_bridge.provision import audit, provision

from test_discord_bridge_provision import (
    BOT,
    GUILD,
    apply_server,
    install_membership_bot,
)


@pytest.mark.parametrize("membership_bot", [False, True])
@pytest.mark.parametrize("delete_feeds", [False, True])
def test_deleted_starter_role_passes_audit_without_changing_history(
    tmp_path, membership_bot, delete_feeds
):
    client, _, state = apply_server(tmp_path)
    if membership_bot:
        install_membership_bot(client, state)
    original_state = copy.deepcopy(state)
    client.roles = [role for role in client.roles if role["id"] != state["roles"]["starter"]]
    if delete_feeds:
        removed = {state["categories"]["starter"], state["channels"]["starter-items"]}
        client.channels = [channel for channel in client.channels if channel["id"] not in removed]
    writes = len(client.writes)

    result = audit(client, GUILD, state)

    assert result["ok"], result["findings"]
    assert state == original_state
    assert len(client.writes) == writes
    assert client.permissions(state["channels"]["pro-all"], [state["roles"]["pro"]]) & (1 << 10)
    assert not client.permissions(state["channels"]["pro-all"]) & (1 << 10)


def test_setup_does_not_recreate_deleted_tracked_starter_role(tmp_path):
    client, path, state = apply_server(tmp_path)
    client.roles = [role for role in client.roles if role["id"] != state["roles"]["starter"]]
    client.roles[1]["permissions"] = str(PROVISION_PERMISSIONS)
    writes = len(client.writes)

    result = provision(client, GUILD, BOT, path)

    assert not any(action.get("key") == "starter" for action in result["actions"])
    assert len(client.writes) == writes


@pytest.mark.parametrize("membership_bot", [False, True])
def test_deleted_pro_role_still_fails_audit(tmp_path, membership_bot):
    client, _, state = apply_server(tmp_path)
    if membership_bot:
        install_membership_bot(client, state)
    client.roles = [role for role in client.roles if role["id"] not in state["roles"].values()]

    result = audit(client, GUILD, state)

    assert not result["ok"]
    assert any("Tracked role pro is missing" in finding for finding in result["findings"])


def test_deleted_starter_does_not_hide_unsafe_pro_role_hierarchy(tmp_path):
    client, _, state = apply_server(tmp_path)
    manager = install_membership_bot(client, state)
    client.roles = [role for role in client.roles if role["id"] != state["roles"]["starter"]]
    next(role for role in client.roles if role["id"] == state["roles"]["pro"])["position"] = (
        manager["position"]
    )

    result = audit(client, GUILD, state)

    assert not result["ok"]
    assert any("strictly above" in finding for finding in result["findings"])
