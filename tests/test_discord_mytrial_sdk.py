"""Member trial status uses real SDK dispatch and never discloses another user."""

import time

import discord
import pytest
from discord_bridge.layout import USE_APPLICATION_COMMANDS

from test_discord_bridge_provision import apply_server
from test_discord_trial_sdk_dispatch import (
    GUILD,
    OTHER,
    OWNER,
    PRO,
    STATE,
    TRIAL,
    dispatch,
    interaction,
    run_gateway,
)

GENERAL = "1700000000000000092"


def mytrial_interaction(sdk, *, user=OTHER, sequence=0):
    target = interaction(sdk, command="mytrial", sequence=sequence)
    target.data["options"] = []
    target.channel = discord.PartialMessageable(
        state=sdk._connection,
        id=int(GENERAL),
        type=discord.ChannelType.text,
    )
    target.user = discord.User(
        state=sdk._connection,
        data={"id": user, "username": "member", "discriminator": "0", "avatar": None},
    )
    return target


def own_member_response(rest, *, user=OTHER, roles=()):
    original = rest.request

    def request(method, path, payload=None):
        if path != f"/guilds/{GUILD}/members/{user}":
            return original(method, path, payload)
        assert rest.interaction.response.is_done()
        assert method == "GET", "A member status query must never write to Discord"
        rest.calls.append((method, path, payload))
        return {"user": {"id": user, "bot": False}, "roles": list(roles)}

    rest.request = request


def grant_record(service, user, *, seconds=3600, now=None):
    return service.store.change(
        user,
        operation="grant",
        seconds=seconds,
        actor_id=OWNER,
        interaction_id="sdk-status:" + user,
        now=time.time() if now is None else now,
    )


def test_general_allows_member_commands_without_opening_pro_or_owner_channels(tmp_path):
    client, _, state = apply_server(tmp_path)
    assert client.permissions(state["channels"]["general"]) & USE_APPLICATION_COMMANDS
    assert not client.permissions(state["channels"]["plans-and-help"]) & (1 << 10)
    assert not client.permissions(state["channels"]["pro-all"]) & (1 << 10)


def test_real_sdk_regular_member_sees_only_own_active_trial(monkeypatch, tmp_path):
    monkeypatch.setitem(STATE["channels"], "general", GENERAL)

    async def scenario(sdk, tree, rest, service, requests):
        own = grant_record(service, OTHER)
        hidden = grant_record(service, OWNER, seconds=7200)
        own_member_response(rest, roles=[TRIAL])
        rows_before = service.list()
        reply = await dispatch(tree, rest, mytrial_interaction(sdk))
        assert "your trial ends" in reply.lower()
        assert f"<t:{int(own['expires_at'])}:" in reply
        assert str(int(hidden["expires_at"])) not in reply
        assert OWNER not in reply
        assert rest.calls == [("GET", f"/guilds/{GUILD}/members/{OTHER}", None)]
        assert service.list() == rows_before and requests.list(OTHER) == []

    run_gateway(monkeypatch, tmp_path, scenario)


@pytest.mark.parametrize("status", ["expired", "revoked"])
def test_real_sdk_ended_trial_keeps_paid_pro_separate(monkeypatch, tmp_path, status):
    monkeypatch.setitem(STATE["channels"], "general", GENERAL)

    async def scenario(sdk, tree, rest, service, requests):
        grant_record(service, OTHER, now=1 if status == "expired" else None)
        if status == "revoked":
            service.store.change(
                OTHER,
                operation="revoke",
                actor_id=OWNER,
                interaction_id="sdk-status:revoke",
            )
        own_member_response(rest, roles=[PRO, TRIAL])
        before = service.store.get(OTHER)
        reply = await dispatch(tree, rest, mytrial_interaction(sdk))
        assert ("expired" if status == "expired" else "ended early") in reply.lower()
        assert "pro" in reply.lower()
        assert rest.calls == [("GET", f"/guilds/{GUILD}/members/{OTHER}", None)]
        assert service.store.get(OTHER) == before and requests.list(OTHER) == []

    run_gateway(monkeypatch, tmp_path, scenario)


def test_real_sdk_no_trial_cannot_query_another_member(monkeypatch, tmp_path):
    monkeypatch.setitem(STATE["channels"], "general", GENERAL)

    async def scenario(sdk, tree, rest, service, _requests):
        hidden = grant_record(service, OWNER)
        own_member_response(rest, roles=[PRO])
        target = mytrial_interaction(sdk)
        # The registered command has no member option; even an injected option
        # must never replace the authenticated interaction user's identity.
        target.data["options"] = [{"name": "member", "type": 6, "value": OWNER}]
        reply = await dispatch(tree, rest, target)
        assert "trial" in reply.lower() and "no" in reply.lower()
        assert str(int(hidden["expires_at"])) not in reply and OWNER not in reply
        assert rest.calls == [("GET", f"/guilds/{GUILD}/members/{OTHER}", None)]
        assert service.store.get(OTHER) is None

    run_gateway(monkeypatch, tmp_path, scenario)


def test_real_sdk_mytrial_preserves_request_and_owner_commands(monkeypatch, tmp_path):
    monkeypatch.setitem(STATE["channels"], "general", GENERAL)

    async def scenario(sdk, tree, rest, service, _requests):
        own_member_response(rest)
        await dispatch(tree, rest, mytrial_interaction(sdk))
        await dispatch(tree, rest, interaction(sdk, sequence=1))
        await dispatch(
            tree,
            rest,
            interaction(
                sdk,
                command="request",
                subcommand="list",
                sequence=2,
            ),
        )
        guild = discord.Object(id=int(GUILD))
        assert tree.get_command("mytrial", guild=guild) is not None
        assert tree.get_command("trial", guild=guild) is not None
        assert tree.get_command("request", guild=guild) is not None
        assert service.settings()["enabled"] is False

    run_gateway(monkeypatch, tmp_path, scenario)


def test_real_sdk_rechecks_expiration_after_member_provider_latency(monkeypatch, tmp_path):
    monkeypatch.setitem(STATE["channels"], "general", GENERAL)

    async def scenario(sdk, tree, rest, service, _requests):
        clock = [1000.0]
        service.clock = service.store.clock = lambda: clock[0]
        grant_record(service, OTHER, seconds=100, now=clock[0])
        own_member_response(rest, roles=[TRIAL])
        original = rest.request

        def delayed(method, path, payload=None):
            result = original(method, path, payload)
            clock[0] = 1200.0
            return result

        rest.request = delayed
        reply = await dispatch(tree, rest, mytrial_interaction(sdk))
        assert "expired" in reply.lower()
        assert rest.calls == [("GET", f"/guilds/{GUILD}/members/{OTHER}", None)]

    run_gateway(monkeypatch, tmp_path, scenario)


def test_real_sdk_mismatched_provider_identity_cannot_expose_another_trial(monkeypatch, tmp_path):
    monkeypatch.setitem(STATE["channels"], "general", GENERAL)

    async def scenario(sdk, tree, rest, service, _requests):
        hidden = grant_record(service, OWNER)
        own_member_response(rest)
        original = rest.request

        def wrong_member(method, path, payload=None):
            result = original(method, path, payload)
            result["user"]["id"] = OWNER
            return result

        rest.request = wrong_member
        reply = await dispatch(tree, rest, mytrial_interaction(sdk))
        assert "unavailable" in reply.lower()
        assert OWNER not in reply and str(int(hidden["expires_at"])) not in reply
        assert rest.calls == [("GET", f"/guilds/{GUILD}/members/{OTHER}", None)]
        assert service.store.get(OTHER) is None

    run_gateway(monkeypatch, tmp_path, scenario)
