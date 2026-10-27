"""Private request commands: offline interactions and shared REST boundary."""

import asyncio
import logging
import threading
from types import SimpleNamespace

import pytest
from discord_bridge.api import DiscordError
from discord_bridge.request_commands import (
    RequestCommands,
    RequestGateway,
    build_request_command_group,
    register_request_commands,
    request_command_definition,
)

GUILD = "1700000000000000001"
CHANNEL = "1700000000000000002"
PRO = "1700000000000000003"
STARTER = "1700000000000000004"
USER = "1700000000000000005"
OTHER = "1700000000000000006"
DM = "1700000000000000007"
STATE = {
    "guild_id": GUILD,
    "bot_user_id": "1700000000000000008",
    "roles": {"pro": PRO, "starter": STARTER},
    "channels": {"item-requests": CHANNEL},
}


class Interaction:
    def __init__(self, user=USER, guild=GUILD, channel=CHANNEL, autocomplete=False):
        self.user = SimpleNamespace(id=int(user))
        self.guild_id = int(guild) if guild else None
        self.channel_id = int(channel) if channel else None
        self.messages, self.acknowledged = [], False
        self.deleted = 0
        self.choices = None
        self.type = SimpleNamespace(value=4 if autocomplete else 2)
        self.response = SimpleNamespace(
            defer=self.defer,
            send_message=self.send,
            is_done=lambda: self.acknowledged,
            send_modal=self.modal,
            autocomplete=self.autocomplete,
        )
        self.followup = SimpleNamespace(send=self.send)

    async def defer(self, **kwargs):
        assert kwargs.get("ephemeral") is True
        self.acknowledged = True

    async def send(self, content, **kwargs):
        assert kwargs.get("ephemeral") is True
        self.acknowledged = True
        self.messages.append((content, kwargs))

    async def modal(self, modal):
        self.acknowledged = True
        self.opened_modal = modal

    async def delete_original_response(self):
        self.deleted += 1

    async def autocomplete(self, choices):
        self.acknowledged, self.choices = True, choices


class Store:
    def __init__(self):
        self.rows, self.statuses = {}, {}

    def add(self, user, query, **_):
        if not query.strip():
            raise ValueError("Enter an item name, Walmart URL, or item ID.")
        if len(self.list(user)) >= 10:
            raise ValueError("You can have up to 10 active requests.")
        row = {"id": str(len(self.rows) + 1), "query": query}
        self.rows[(user, row["id"])] = row
        return row

    def list(self, user):
        return [dict(row) for (owner, _), row in self.rows.items() if owner == user]

    def edit(self, user, request_id, query, **_):
        if (user, request_id) not in self.rows:
            raise ValueError("Request not found. Use /request list.")
        self.rows[(user, request_id)]["query"] = query
        return self.rows[(user, request_id)]

    def delete(self, user, request_id):
        return self.rows.pop((user, request_id), None) is not None

    def dm_status(self, user):
        return self.statuses.get(user, "needs_test")

    def set_dm_status(self, user, status, **_):
        self.statuses[user] = status


class Client:
    def __init__(self, roles=(PRO,), interaction=None):
        self.roles, self.interaction = roles, interaction
        self.calls, self.error = [], None

    def request(self, method, path, payload=None):
        if self.interaction:
            assert self.interaction.acknowledged, "REST started before private ack"
        self.calls.append((method, path, payload))
        if "/members/" in path:
            return {"user": {"id": path.rsplit("/", 1)[-1]}, "roles": list(self.roles)}
        if self.error:
            raise self.error
        if path == "/users/@me/channels":
            return {"id": DM, "type": 1, "recipients": [{"id": USER}]}
        if "/messages" in path:
            return {"id": "1700000000000000010", "channel_id": DM}
        if path == "/oauth2/applications/@me":
            return {"id": STATE["bot_user_id"]}
        return {"id": "1700000000000000011"}


def invoke(method, *args, **kwargs):
    return asyncio.run(method(*args, **kwargs))


def make(roles=(PRO,), **interaction_options):
    interaction = Interaction(**interaction_options)
    client, store = Client(roles, interaction), Store()
    return RequestCommands(client, STATE, store), interaction, client, store


@pytest.mark.parametrize("roles", [(), (STARTER,)])
def test_free_and_starter_cannot_add_or_read_requests(roles):
    commands, interaction, client, store = make(roles)
    invoke(commands.add, interaction, "air fryer")
    invoke(commands.list, interaction)
    assert store.list(USER) == []
    assert all("Pro" in text for text, _ in interaction.messages)
    assert all("/members/" in call[1] for call in client.calls)


@pytest.mark.parametrize("options", [{"guild": None}, {"guild": OTHER}, {"channel": OTHER}])
def test_wrong_guild_channel_and_dm_fail_closed(options):
    commands, interaction, client, store = make(**options)
    invoke(commands.add, interaction, "air fryer")
    assert store.list(USER) == [] and client.calls == []
    assert interaction.messages == []
    assert interaction.acknowledged and interaction.deleted == 1


def test_first_add_tests_dm_then_accepts_private_request_and_list_controls():
    commands, interaction, client, store = make((STARTER, PRO))
    invoke(commands.add, interaction, "air fryer")
    assert store.list(USER)[0]["query"] == "air fryer"
    assert store.dm_status(USER) == "ready"
    sent = [c for c in client.calls if c[1].endswith("/messages")]
    assert len(sent) == 1 and sent[0][1] == f"/channels/{DM}/messages"
    assert sent[0][2]["allowed_mentions"]["parse"] == []
    assert "air fryer" not in sent[0][2]["content"]
    assert "accepted" in interaction.messages[-1][0].lower()
    invoke(commands.list, interaction)
    assert "air fryer" in interaction.messages[-1][0]
    assert interaction.messages[-1][1]["view"] is not None


def test_blocked_dm_keeps_watch_paused_and_never_uses_public_fallback():
    commands, interaction, client, store = make()
    client.error = DiscordError(status=403, provider_code=50007)
    invoke(commands.add, interaction, "air fryer")
    assert store.dm_status(USER) == "blocked"
    assert store.list(USER)[0]["query"] == "air fryer"
    assert "paused" in interaction.messages[-1][0].lower()
    assert "enable" in interaction.messages[-1][0].lower()
    assert not any(path == f"/channels/{CHANNEL}/messages" for _, path, _ in client.calls)
    client.error = None
    invoke(commands.test_dm, interaction)
    assert store.dm_status(USER) == "ready"


def test_rate_limit_does_not_mark_dms_blocked_or_expose_error_text():
    commands, interaction, client, store = make()
    client.error = DiscordError("secret-token", status=429, retry_after=200)
    invoke(commands.add, interaction, "air fryer")
    assert store.dm_status(USER) == "needs_test"
    assert store.list(USER) == []
    assert "retry" in interaction.messages[-1][0].lower()
    assert "secret-token" not in str(interaction.messages)


def test_users_can_only_edit_delete_or_autocomplete_their_own_requests():
    commands, interaction, client, store = make()
    row = store.add(OTHER, "secret other request")
    invoke(commands.edit, interaction, row["id"], "my change")
    invoke(commands.delete, interaction, row["id"])
    assert store.list(OTHER)[0]["query"] == "secret other request"
    assert invoke(commands.autocomplete, interaction, "") == []
    assert "secret other request" not in str(interaction.messages)


def test_authorization_is_fresh_after_role_removal_for_mutations_and_autocomplete():
    commands, interaction, client, store = make()
    row = store.add(USER, "air fryer")
    client.roles = ()
    invoke(commands.edit, interaction, row["id"], "TV")
    invoke(commands.delete, interaction, row["id"])
    assert invoke(commands.autocomplete, interaction, "") == []
    assert store.list(USER)[0]["query"] == "air fryer"


def test_successful_edit_and_delete_are_private():
    commands, interaction, _, store = make()
    row = store.add(USER, "air fryer")
    invoke(commands.edit, interaction, row["id"], "coffee maker")
    assert store.list(USER)[0]["query"] == "coffee maker"
    invoke(commands.delete, interaction, row["id"])
    assert store.list(USER) == []


def test_command_definition_and_registration_only_upsert_request_command():
    definition = request_command_definition()
    assert definition["name"] == "request" and definition["dm_permission"] is False
    assert {o["name"] for o in definition["options"]} == {
        "add",
        "list",
        "edit",
        "delete",
        "test-dm",
    }
    client = Client()
    register_request_commands(client, STATE)
    assert client.calls[-1] == (
        "POST",
        f"/applications/{STATE['bot_user_id']}/guilds/{GUILD}/commands",
        definition,
    )
    assert not any(method == "PUT" for method, _, _ in client.calls)


def test_disabled_gateway_is_inert_and_health_does_not_reveal_data():
    gateway = RequestGateway(Client(), STATE, Store(), token="secret")
    gateway.start()
    gateway.stop()
    health = gateway.health()
    assert health["enabled"] is False
    assert "secret" not in str(health) and USER not in str(health)


def test_enabled_gateway_requires_exact_bound_request_channel():
    with pytest.raises(ValueError):
        RequestGateway(Client(), {**STATE, "channels": {}}, Store(), token="secret", enabled=True)


def test_autocomplete_returns_only_own_choices_and_never_acks_as_a_message():
    commands, interaction, client, store = make()
    client.interaction = None  # Autocomplete cannot defer or send a message.
    mine = store.add(USER, "air fryer")
    store.add(OTHER, "air fryer for another user")
    assert invoke(commands.autocomplete, interaction, "fry") == [("air fryer", mine["id"])]
    assert interaction.messages == [] and not interaction.acknowledged
    assert invoke(commands.autocomplete, Interaction(channel=OTHER), "") == []


def test_ui_controls_reject_another_user_and_modal_submissions_recheck_ownership():
    async def scenario():
        commands, interaction, client, store = make()
        store.add(USER, "air fryer")
        await commands.list(interaction)
        view = interaction.messages[-1][1]["view"]
        other = Interaction(user=OTHER)
        assert await view.interaction_check(other) is False
        assert "another member" in other.messages[0][0]
        own = Interaction()
        assert await view.interaction_check(own) is True
        edit = next(child for child in view.children if getattr(child, "label", None) == "Edit")
        await edit.callback(own)
        assert own.opened_modal.item.default is None
        own.opened_modal.item._value = "coffee maker"
        await own.opened_modal.on_submit(other)
        assert store.list(USER)[0]["query"] == "air fryer"
        await own.opened_modal.on_submit(own)
        assert store.list(USER)[0]["query"] == "coffee maker"
        remove = next(child for child in view.children if getattr(child, "label", None) == "Delete")
        await remove.callback(other)
        assert store.list(USER)
        await remove.callback(own)
        assert store.list(USER) == []

    asyncio.run(scenario())


def test_edit_modal_submission_rechecks_pro_access_after_it_is_opened():
    async def scenario():
        commands, interaction, client, store = make()
        store.add(USER, "air fryer")
        await commands.list(interaction)
        view = interaction.messages[-1][1]["view"]
        edit = next(child for child in view.children if getattr(child, "label", None) == "Edit")
        modal_interaction = Interaction()
        await edit.callback(modal_interaction)
        modal_interaction.opened_modal.item._value = "coffee maker"
        client.roles = (STARTER,)
        await modal_interaction.opened_modal.on_submit(modal_interaction)
        assert store.list(USER)[0]["query"] == "air fryer"
        assert "Pro" in modal_interaction.messages[-1][0]

    asyncio.run(scenario())


def test_runtime_and_provider_exception_details_are_private_and_redacted():
    commands, interaction, client, store = make()
    client.request = lambda *_: (_ for _ in ()).throw(ValueError("secret-token"))
    invoke(commands.list, interaction)
    assert "secret-token" not in str(interaction.messages)
    assert "retry" in interaction.messages[-1][0].lower()


def test_blocked_dm_status_is_clear_in_private_management():
    commands, interaction, _, store = make()
    store.add(USER, "air fryer")
    store.set_dm_status(USER, "blocked")
    invoke(commands.list, interaction)
    assert "paused" in interaction.messages[-1][0].lower()
    assert "/request test-dm" in interaction.messages[-1][0]


def test_sdk_group_has_expected_autocomplete_and_errors_bound_privately():
    commands, *_ = make()
    group = build_request_command_group(commands)
    assert group.guild_only
    assert {command.name for command in group.commands} == {
        "add",
        "list",
        "edit",
        "delete",
        "test-dm",
    }
    assert group.get_command("edit").get_parameter("request").autocomplete
    assert group.get_command("delete").get_parameter("request").autocomplete
    assert group.on_error == commands.error


def test_sdk_wire_definition_exactly_matches_explicit_registration_payload():
    import discord

    commands, *_ = make()
    gateway = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(gateway)
    assert build_request_command_group(commands).to_dict(tree) == request_command_definition()


@pytest.mark.parametrize(
    "bot,application",
    [
        (STATE["bot_user_id"], STATE["bot_user_id"]),
        (OTHER, STATE["bot_user_id"]),
        (STATE["bot_user_id"], OTHER),
    ],
)
def test_gateway_starts_stops_without_sync_or_privileged_intents(monkeypatch, bot, application):
    import discord
    import discord_bridge.request_commands as module

    entered, stopped = threading.Event(), threading.Event()
    events, instances = [], []

    def sink(event, **fields):
        events.append((event, fields))

    class Gateway(discord.Client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._connection.user = SimpleNamespace(id=int(bot))
            self._connection.application_id = int(application)
            self.done = asyncio.Event()
            instances.append(self)

        async def start(self, token, *, reconnect):
            assert token == "secret" and reconnect is True
            assert self.intents.value == 0
            await self.on_ready()
            entered.set()
            await self.done.wait()

        async def close(self):
            self.done.set()
            stopped.set()

    monkeypatch.setattr(
        module,
        "_sdk",
        lambda: SimpleNamespace(
            Client=Gateway,
            Intents=discord.Intents,
            AllowedMentions=discord.AllowedMentions,
            app_commands=discord.app_commands,
            Object=discord.Object,
            ui=discord.ui,
            ButtonStyle=discord.ButtonStyle,
            SelectOption=discord.SelectOption,
            InteractionType=discord.InteractionType,
        ),
    )
    client = Client()
    gateway = RequestGateway(client, STATE, Store(), token="secret", enabled=True, event_sink=sink)
    gateway.start()
    assert entered.wait(3)
    try:
        assert instances and client.calls == []
        if bot == application == STATE["bot_user_id"]:
            assert gateway.health()["connected"] is True
        else:
            assert gateway.health()["connected"] is False
            assert gateway.health()["last_error"] == "gateway_identity"
    finally:
        gateway.stop()
    assert stopped.is_set() and not gateway.health()["running"]
    assert "secret" not in str(events) and USER not in str(events)


def test_sdk_logging_redacts_raw_gateway_payloads_and_token_bearing_http_errors(caplog):
    import discord_bridge.request_commands as module

    module._sdk()
    with caplog.at_level(logging.DEBUG, logger="discord.http"):
        logging.getLogger("discord.http").debug(
            "request /webhooks/id/secret-token product secret item"
        )
        logging.getLogger("discord.http").warning(
            "request /webhooks/id/secret-token product secret item"
        )
    with caplog.at_level(logging.DEBUG, logger="discord.gateway"):
        logging.getLogger("discord.gateway").debug("interaction secret-token item secret item")
    assert "secret-token" not in caplog.text and "secret item" not in caplog.text


def test_private_list_shows_ambiguous_delivery_needs_review():
    commands, interaction, _, store = make()
    row = store.add(USER, "air fryer")
    store.rows[(USER, row["id"])]["delivery_status"] = "needs_review"
    invoke(commands.list, interaction)
    assert "needs review" in interaction.messages[-1][0].lower()


def test_private_list_shows_definitely_failed_delivery():
    commands, interaction, _, store = make()
    row = store.add(USER, "air fryer")
    store.rows[(USER, row["id"])]["delivery_status"] = "failed"
    invoke(commands.list, interaction)
    assert "delivery failed" in interaction.messages[-1][0].lower()


def test_invalid_first_request_does_not_send_a_dm(tmp_path):
    from discord_bridge.request_store import RequestStore

    interaction, client = Interaction(), Client()
    client.interaction = interaction
    store = RequestStore(tmp_path / "requests.sqlite3", guild_id=GUILD)
    commands = RequestCommands(client, STATE, store)
    invoke(commands.add, interaction, " ")
    assert not any(path.endswith("/messages") for _, path, _ in client.calls)
    assert store.list(USER) == []


def test_command_lifecycle_uses_real_durable_store_and_survives_restart(tmp_path):
    from discord_bridge.request_store import RequestStore

    interaction, client = Interaction(), Client()
    client.interaction = interaction
    path = tmp_path / "requests.sqlite3"
    commands = RequestCommands(client, STATE, RequestStore(path, guild_id=GUILD))
    invoke(commands.add, interaction, "air fryer")
    restarted = RequestCommands(client, STATE, RequestStore(path, guild_id=GUILD))
    rows = restarted.store.list(USER)
    assert len(rows) == 1 and restarted.store.dm_status(USER) == "ready"
    invoke(restarted.edit, interaction, str(rows[0]["id"]), "coffee maker")
    invoke(restarted.list, interaction)
    assert "coffee maker" in interaction.messages[-1][0]
    invoke(restarted.delete, interaction, str(rows[0]["id"]))
    assert restarted.store.list(USER) == []


@pytest.mark.parametrize("action", ["add", "list", "edit", "delete", "test_dm", "error"])
@pytest.mark.parametrize("scope", [{"channel": OTHER}, {"guild": OTHER}, {"guild": None}])
def test_all_command_and_error_paths_are_silent_outside_requests_channel(action, scope):
    commands, interaction, client, store = make(**scope)
    arguments = {"add": ("air fryer",), "edit": ("1", "coffee maker"), "delete": ("1",)}
    invoke(getattr(commands, action), interaction, *arguments.get(action, ()))
    assert interaction.messages == []
    assert interaction.acknowledged and interaction.deleted == 1
    assert client.calls == [] and store.list(USER) == []


def test_wrong_channel_already_deferred_response_is_erased_without_followup():
    commands, interaction, client, _ = make(channel=OTHER)
    invoke(interaction.defer, ephemeral=True, thinking=True)
    invoke(commands.error, interaction, RuntimeError("secret-token"))
    assert interaction.messages == [] and interaction.deleted == 1
    assert client.calls == []


def test_wrong_channel_autocomplete_error_only_returns_empty_choices():
    commands, interaction, client, _ = make(channel=OTHER, autocomplete=True)
    invoke(commands.error, interaction, RuntimeError("secret item"))
    assert interaction.choices == [] and interaction.messages == []
    assert interaction.deleted == 0 and client.calls == []


def _gateway_scenario(monkeypatch, scenario):
    """Real SDK event dispatch and command tree, without login or sockets."""
    import discord
    import discord_bridge.request_commands as module

    original_client = discord.Client
    events = []

    class Gateway(original_client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._connection.user = SimpleNamespace(id=int(STATE["bot_user_id"]))
            self._connection.application_id = int(STATE["bot_user_id"])

        async def start(self, token, *, reconnect):
            await scenario(self, self._connection._command_tree, worker, client, store)

    def sink(event, **fields):
        events.append((event, fields))

    monkeypatch.setattr(discord, "Client", Gateway)
    client, store = Client(), Store()
    worker = module.RequestGateway(
        client, STATE, store, token="secret-token", enabled=True, event_sink=sink
    )
    asyncio.run(worker._connect())
    return events


def test_gateway_actual_sdk_dispatch_recovers_after_resumed(monkeypatch):
    async def scenario(sdk, tree, worker, client, store):
        sdk.dispatch("ready")
        await asyncio.sleep(0)
        assert worker.health()["connected"]
        sdk.dispatch("disconnect")
        await asyncio.sleep(0)
        assert not worker.health()["connected"]
        sdk.dispatch("resumed")
        await asyncio.sleep(0)
        assert worker.health()["connected"]
        interaction = Interaction()
        assert await tree.interaction_check(interaction)
        command = tree.get_command("request", guild=SimpleNamespace(id=int(GUILD)))
        await command.get_command("add").callback(interaction, "air fryer")
        assert store.list(USER)[0]["query"] == "air fryer"

    _gateway_scenario(monkeypatch, scenario)


@pytest.mark.parametrize("identity", ["bot", "application", "shutdown", "closed"])
def test_resumed_rechecks_identity_and_does_not_revive_shutdown(monkeypatch, identity):
    async def scenario(sdk, tree, worker, client, store):
        sdk.dispatch("ready")
        await asyncio.sleep(0)
        sdk.dispatch("disconnect")
        await asyncio.sleep(0)
        if identity == "bot":
            sdk._connection.user = SimpleNamespace(id=int(OTHER))
        elif identity == "application":
            sdk._connection.application_id = int(OTHER)
        elif identity == "shutdown":
            worker._stop.set()
        else:
            # Exercise an event queued just before SDK close. A fully closed
            # SDK intentionally clears its loop and cannot dispatch new events.
            sdk.dispatch("resumed")
            await sdk.close()
        if identity != "closed":
            sdk.dispatch("resumed")
        await asyncio.sleep(0)
        assert not worker.health()["connected"]
        if identity in {"bot", "application"}:
            assert worker.health()["last_error"] == "gateway_identity"
        assert client.calls == [] and store.list(USER) == []

    _gateway_scenario(monkeypatch, scenario)


@pytest.mark.parametrize("connected", [False, True])
def test_tree_guards_and_errors_never_reply_outside_channel(monkeypatch, connected):
    async def scenario(sdk, tree, worker, client, store):
        worker._connected = connected
        for options in ({"channel": OTHER}, {"guild": OTHER}, {"guild": None}):
            interaction = Interaction(**options)
            assert await tree.interaction_check(interaction) is False
            assert interaction.messages == [] and interaction.deleted == 1
            await tree.on_error(interaction, RuntimeError("secret-token"))
            assert interaction.messages == []
        automatic = Interaction(channel=OTHER, autocomplete=True)
        assert await tree.interaction_check(automatic) is False
        assert automatic.choices == [] and automatic.messages == []
        assert client.calls == [] and store.list(USER) == []

    _gateway_scenario(monkeypatch, scenario)


def test_temporary_disconnect_has_private_recovery_guidance_in_bound_channel(monkeypatch):
    async def scenario(sdk, tree, worker, client, store):
        interaction = Interaction()
        assert await tree.interaction_check(interaction) is False
        text = interaction.messages[-1][0].lower()
        assert "reconnect" in text and "retry" in text
        assert "/request list" not in text
        assert client.calls == [] and store.list(USER) == []

    events = _gateway_scenario(monkeypatch, scenario)
    assert all("secret-token" not in str(event) for event in events)


def test_ui_modal_and_view_errors_are_silent_outside_requests_channel():
    async def scenario():
        commands, interaction, client, store = make()
        store.add(USER, "air fryer")
        await commands.list(interaction)
        view = interaction.messages[-1][1]["view"]
        client.calls.clear()
        elsewhere = Interaction(channel=OTHER)
        assert not await view.interaction_check(elsewhere)
        edit = next(child for child in view.children if getattr(child, "label", None) == "Edit")
        await edit.callback(elsewhere)
        assert not hasattr(elsewhere, "opened_modal")
        await view.on_error(elsewhere, RuntimeError("secret-token"), edit)
        own = Interaction()
        await edit.callback(own)
        own.opened_modal.item._value = "coffee maker"
        await own.opened_modal.on_submit(elsewhere)
        await own.opened_modal.on_error(elsewhere, RuntimeError("secret item"))
        assert elsewhere.messages == []
        assert client.calls == [] and store.list(USER)[0]["query"] == "air fryer"

    asyncio.run(scenario())


def test_command_failures_emit_fixed_operational_labels_without_request_data():
    events = []
    commands, interaction, client, store = make()
    commands._events = lambda event, **fields: events.append((event, fields))
    client.request = lambda *_: (_ for _ in ()).throw(RuntimeError("secret-token secret item"))
    invoke(commands.add, interaction, "secret item")
    assert events == [("worker_error", {"phase": "worker", "error_type": "request_command"})]
    assert "secret-token" not in str(events) and "secret item" not in str(events)
