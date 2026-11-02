"""Trial access shares Pro features while preserving paid memberships."""

import copy
from types import SimpleNamespace

import pytest
from discord_bridge.layout import CATEGORIES, MANAGE_ROLES, permission_overwrites
from discord_bridge.provision import _validate_state, audit
from discord_bridge.request_commands import RequestCommands, _PrivateError
from discord_bridge.request_delivery import RequestWorker
from discord_bridge.request_store import RequestStore

from test_discord_bridge_provision import BOT_ROLE, GUILD, apply_server
from test_discord_request_commands import PRO, STATE, USER, Client, Interaction, Store

TRIAL = "1700000000000000090"
TRIAL_CHANNEL = "1700000000000000091"


def trial_server(tmp_path):
    client, path, state = apply_server(tmp_path)
    state["roles"]["trial"] = TRIAL
    client.roles.append({"id": TRIAL, "name": "Pro Trial", "permissions": "0",
                         "mentionable": False, "managed": False, "position": 1})
    for role in client.roles:
        if role["id"] == BOT_ROLE:
            role.update(position=2, permissions=str(MANAGE_ROLES))
        elif role["id"] == state["roles"]["pro"]:
            role["position"] = 3
    categories = {c.key: c for c in CATEGORIES}
    by_id = {value: categories[key] for key, value in state["categories"].items()}
    for channel in client.channels:
        category = by_id.get(channel["id"]) or by_id[channel["parent_id"]]
        channel["permission_overwrites"] = permission_overwrites(
            GUILD, state["bot_user_id"], state["roles"], category,
            public_commands=channel["id"] == state["channels"]["general"],
        )
    return client, path, state


def test_optional_trial_role_is_accepted_and_restricted_to_pro_feeds(tmp_path):
    client, _, state = trial_server(tmp_path)
    assert _validate_state(state, GUILD)["roles"]["trial"] == TRIAL
    assert audit(client, GUILD, state)["ok"]
    assert client.permissions(state["channels"]["pro-all"], [TRIAL]) & (1 << 10)
    assert not client.permissions(state["channels"]["audit"], [TRIAL]) & (1 << 10)


@pytest.mark.parametrize("positions", [(2, 1, 3), (1, 3, 2)])
def test_trial_bot_must_manage_trial_but_cannot_manage_paid_pro(tmp_path, positions):
    client, _, state = trial_server(tmp_path)
    trial, bot, pro = positions
    for role in client.roles:
        role["position"] = {TRIAL: trial, BOT_ROLE: bot,
                            state["roles"]["pro"]: pro}.get(role["id"], 0)
    assert not audit(client, GUILD, state)["ok"]


def test_unconfigured_delivery_bot_cannot_gain_role_management(tmp_path):
    client, _, state = apply_server(tmp_path)
    client.roles[1]["permissions"] = str(MANAGE_ROLES)
    assert not audit(client, GUILD, state)["ok"]


def test_access_loss_deletes_watches_and_preserves_other_members(tmp_path):
    store = RequestStore(tmp_path / "requests.sqlite3", guild_id=GUILD)
    store.add(USER, "solar")
    other = "1700000000000000088"
    store.add(other, "chair")
    assert store.owners(limit=1) == [USER]
    assert store.owners(after=USER) == [other]
    store.revoke_user(USER)
    assert store.list(USER) == []
    assert len(store.list(other)) == 1
    assert store.owners() == [other]


class Eligibility:
    def __init__(self, active):
        self.current = active

    def active(self, user_id, **_):
        assert user_id == USER
        return self.current


def test_request_commands_require_both_trial_role_and_unexpired_grant():
    state = copy.deepcopy(STATE)
    state["roles"]["trial"] = TRIAL
    eligibility = Eligibility(True)
    handler = RequestCommands(Client(roles=[TRIAL]), state, Store(), trial_store=eligibility)
    assert handler.authorize(Interaction()) == USER
    eligibility.current = False
    with pytest.raises(_PrivateError):
        handler.authorize(Interaction())
    # Expiring complimentary access cannot revoke paid access.
    handler.client = Client(roles=[PRO, TRIAL])
    assert handler.authorize(Interaction()) == USER
    handler.client = Client(roles=[])
    eligibility.current = True
    with pytest.raises(_PrivateError):
        handler.authorize(Interaction())


def test_private_sender_rechecks_trial_expiration(tmp_path):
    class MemberClient:
        def request(self, method, path):
            return {"user": {"id": USER}, "roles": [TRIAL]}

    state = copy.deepcopy(STATE)
    state["roles"]["trial"] = TRIAL
    eligibility = Eligibility(True)
    store = RequestStore(tmp_path / "requests.sqlite3", guild_id=state["guild_id"])
    worker = RequestWorker(MemberClient(), state, store, trial_store=eligibility)
    assert worker._member(USER)
    eligibility.current = False
    assert not worker._member(USER)


def test_sender_cannot_delete_watches_after_a_concurrent_trial_extension(tmp_path):
    from test_discord_request_delivery import setup

    class ExtendingEligibility:
        current = False

        def active(self, *_args, **_kwargs):
            captured = self.current
            # The sender's stale no-access decision overlaps an owner extension.
            self.current = True
            return captured

    eligibility = ExtendingEligibility()
    store, _, provider, worker = setup(tmp_path, trial_store=eligibility)
    owner = store.owners()[0]
    worker.state = {**worker.state, "roles": {**worker.state["roles"], "trial": TRIAL}}
    provider.roles = [TRIAL]
    assert worker.tick(now=12)["blocked"] == 1
    assert eligibility.current
    assert len(store.list(owner)) == 1
    assert not provider.messages


def test_trial_setup_binds_exact_audited_resources_without_remote_mutation(tmp_path):
    import json

    from discord_bridge.trial_setup import bind_trial_access, trial_access_plan

    client, path, state = trial_server(tmp_path)
    plan = trial_access_plan(state, TRIAL)
    assert plan["welcome_default"] == {"enabled": False, "hours": 24}
    assert all(update["payload"]["permission_overwrites"] for update in plan["updates"])
    channel = {"id": TRIAL_CHANNEL, "guild_id": GUILD, **plan["channel"]}
    client.channels.append(channel)
    baseline = len(client.writes)
    result = bind_trial_access(client, path, TRIAL, TRIAL_CHANNEL)
    assert not result["applied"]
    assert "trial" not in json.loads(path.read_text())["roles"]
    result = bind_trial_access(client, path, TRIAL, TRIAL_CHANNEL, apply=True)
    assert result["audit"]["ok"]
    assert len(client.writes) == baseline
    assert client.permissions(TRIAL_CHANNEL, [TRIAL]) & (1 << 10) == 0
    assert client.permissions(TRIAL_CHANNEL, [state["roles"]["pro"]]) & (1 << 10) == 0
    assert json.loads(path.read_text())["roles"]["trial"] == TRIAL


def test_trial_runtime_shares_fresh_eligibility_and_lifecycle(tmp_path, monkeypatch):
    from discord_bridge.request_runtime import RequestRuntime

    state = copy.deepcopy(STATE)
    state["roles"]["trial"] = TRIAL
    state["channels"]["trial-management"] = TRIAL_CHANNEL
    instances = []

    class Service:
        def __init__(self, *args, request_store, **kwargs):
            self.store, self.requests = Eligibility(True), request_store
            self.started = self.stopped = False
            instances.append(self)

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def health(self):
            return {"running": self.started and not self.stopped}

    class Child:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        def start(self):
            pass

        def stop(self):
            pass

        def health(self):
            return {"running": True, "connected": True}

    monkeypatch.setattr("discord_bridge.trials.TrialService", Service)
    runtime = RequestRuntime(object(), state, tmp_path / "collector.sqlite3", token="offline",
                             auditor=lambda *_: {"ok": True}, worker_factory=Child,
                             gateway_factory=Child)
    assert runtime.prepare()
    assert runtime.worker.kwargs["trial_store"] is instances[0].store
    assert runtime.gateway.kwargs["trial_service"] is instances[0]
    assert instances[0].started
    runtime.stop()
    assert instances[0].stopped


def test_real_sdk_gateway_dispatches_owner_trials_and_preserves_requests(monkeypatch):
    import asyncio

    import discord
    from discord_bridge.request_commands import RequestGateway

    state = copy.deepcopy(STATE)
    state["roles"]["trial"] = TRIAL
    state["channels"]["trial-management"] = TRIAL_CHANNEL
    service = SimpleNamespace(store=Eligibility(True), settings=lambda: {"enabled": False,
                                                                         "hours": 24})
    original = discord.Client

    class GuildClient(Client):
        def request(self, method, path, payload=None):
            if path == f"/guilds/{STATE['guild_id']}":
                return {"id": STATE["guild_id"], "owner_id": USER}
            return super().request(method, path, payload)

    class Gateway(original):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._connection.user = SimpleNamespace(id=int(STATE["bot_user_id"]))
            self._connection.application_id = int(STATE["bot_user_id"])

        async def start(self, token, *, reconnect):
            await self.on_ready()
            tree = self._connection._command_tree
            group = tree.get_command("trial", guild=SimpleNamespace(id=int(STATE["guild_id"])))
            assert group is not None
            interaction = Interaction(channel=TRIAL_CHANNEL)
            interaction.data = {"name": "trial"}
            assert await tree.interaction_check(interaction)
            await group.get_command("welcome").callback(interaction)
            assert "24 hours" in interaction.messages[-1][0]
            request = Interaction()
            request.data = {"name": "request"}
            assert await tree.interaction_check(request)
            command = tree.get_command("request", guild=SimpleNamespace(id=int(STATE["guild_id"])))
            await command.get_command("add").callback(request, "solar")
            assert store.list(USER)
            await self.on_disconnect()
            fresh = Interaction(channel=TRIAL_CHANNEL)
            fresh.data = {"name": "trial"}
            assert not await tree.interaction_check(fresh)

    monkeypatch.setattr(discord, "Client", Gateway)
    store = Store()
    gateway = RequestGateway(GuildClient(), state, store, token="offline", enabled=True,
                             trial_service=service)
    asyncio.run(gateway._connect())
