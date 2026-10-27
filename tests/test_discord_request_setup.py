"""Optional Pro request-channel rollout must preserve existing paid feeds."""

import copy
import json

import pytest
from discord_bridge.layout import (
    CATEGORIES,
    USE_APPLICATION_COMMANDS,
    permission_overwrites,
    server_manifest,
)
from discord_bridge.provision import ProvisionError, audit
from discord_bridge.request_setup import bind_request_channel, request_channel_plan

from test_discord_bridge_provision import BOT, GUILD, apply_server


def create_request_channel(client, state):
    plan = request_channel_plan(state)
    channel = {"id": "1800000000000000001", "guild_id": GUILD, **plan}
    client.channels.append(channel)
    return channel


def test_existing_server_audit_accepts_absent_optional_request_channel(tmp_path):
    client, _, state = apply_server(tmp_path)
    assert "item-requests" not in state["channels"]
    assert audit(client, GUILD, state)["ok"]
    assert server_manifest()["optional_channels"][0]["key"] == "item-requests"


def test_explicit_channel_binding_is_dry_run_then_local_only(tmp_path):
    client, path, state = apply_server(tmp_path)
    channel = create_request_channel(client, state)
    baseline = len(client.writes)
    planned = bind_request_channel(client, path, channel["id"])
    assert planned["applied"] is False
    assert "item-requests" not in json.loads(path.read_text())["channels"]
    result = bind_request_channel(client, path, channel["id"], apply=True)
    assert result["audit"]["ok"]
    assert len(client.writes) == baseline
    saved = json.loads(path.read_text())
    assert saved["channels"]["item-requests"] == channel["id"]
    assert audit(client, GUILD, saved)["ok"]
    pro = saved["roles"]["pro"]
    starter = saved["roles"]["starter"]
    assert client.permissions(channel["id"], [pro]) & USE_APPLICATION_COMMANDS
    assert not client.permissions(channel["id"], [starter]) & USE_APPLICATION_COMMANDS
    assert not client.permissions(channel["id"]) & USE_APPLICATION_COMMANDS
    assert not client.permissions(channel["id"], [pro]) & (1 << 11)


@pytest.mark.parametrize("drift", ["public", "parent", "name", "pro_commands"])
def test_request_binding_refuses_policy_drift(tmp_path, drift):
    client, path, state = apply_server(tmp_path)
    channel = create_request_channel(client, state)
    if drift == "public":
        channel["permission_overwrites"][0]["allow"] = str(1 << 10)
    elif drift == "parent":
        channel["parent_id"] = state["categories"]["community"]
    elif drift == "name":
        channel["name"] = "other"
    else:
        next(o for o in channel["permission_overwrites"] if o["id"] == state["roles"]["pro"])[
            "allow"
        ] = str((1 << 10) | (1 << 16))
    before = path.read_bytes()
    with pytest.raises(ProvisionError):
        bind_request_channel(client, path, channel["id"], apply=True)
    assert path.read_bytes() == before


def test_request_binding_does_not_reassign_a_tracked_channel(tmp_path):
    client, path, state = apply_server(tmp_path)
    create_request_channel(client, state)
    with pytest.raises(ProvisionError):
        bind_request_channel(client, path, state["channels"]["pro-all"], apply=True)


def test_request_command_permission_is_narrow_and_audited(tmp_path):
    client, path, state = apply_server(tmp_path)
    channel = create_request_channel(client, state)
    result = bind_request_channel(client, path, channel["id"], apply=True)
    saved = result["state"]
    leaked = copy.deepcopy(channel["permission_overwrites"])
    leaked[0]["allow"] = str(USE_APPLICATION_COMMANDS)
    channel["permission_overwrites"] = leaked
    assert not audit(client, GUILD, saved)["ok"]
    public = next(c for c in CATEGORIES if c.audience == "everyone")
    with pytest.raises(ValueError):
        permission_overwrites(GUILD, BOT, state["roles"], public, application_commands=True)


def test_channel_binding_state_save_survives_a_transient_windows_file_lock(tmp_path, monkeypatch):
    import discord_bridge.provision as module

    path = tmp_path / "state.json"
    path.write_text('{"version": 1}')
    original = module.os.replace
    attempts = []
    delays = []

    def locked(source, target):
        attempts.append(source)
        assert json.loads(path.read_text()) == {"version": 1}
        if len(attempts) < 3:
            error = PermissionError("temporary Windows lock")
            error.winerror = 5
            raise error
        original(source, target)

    monkeypatch.setattr(module.os, "replace", locked)
    monkeypatch.setattr("time.sleep", delays.append)
    module._save_state(path, {"version": 2})
    assert json.loads(path.read_text()) == {"version": 2}
    assert len(delays) == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_channel_binding_state_save_preserves_old_file_when_lock_persists(tmp_path, monkeypatch):
    import discord_bridge.provision as module

    path = tmp_path / "state.json"
    path.write_text('{"version": 1}')

    def locked(_source, _target):
        error = PermissionError("persistent Windows lock")
        error.winerror = 32
        raise error

    monkeypatch.setattr(module.os, "replace", locked)
    monkeypatch.setattr("time.sleep", lambda _delay: None)
    with pytest.raises(PermissionError):
        module._save_state(path, {"version": 2})
    assert json.loads(path.read_text()) == {"version": 1}
    assert not list(tmp_path.glob("*.tmp"))
