"""Run real SDK parsing and invocation; only Discord I/O is substituted."""

import asyncio

import discord
from discord_bridge.request_commands import RequestGateway
from discord_bridge.request_store import RequestStore
from discord_bridge.trials import TrialService, TrialStore

GUILD = "1700000000000000001"
OWNER = "1700000000000000005"
OTHER = "1700000000000000006"
BOT = "1700000000000000008"
PRO = "1700000000000000003"
TRIAL = "1700000000000000090"
CHANNEL = "1700000000000000091"
REQUEST_CHANNEL = "1700000000000000002"
DM = "1700000000000000007"
STATE = {
    "guild_id": GUILD,
    "bot_user_id": BOT,
    "roles": {"pro": PRO, "starter": "1700000000000000004", "trial": TRIAL},
    "channels": {"trial-management": CHANNEL, "item-requests": REQUEST_CHANNEL},
}


class Response:
    def __init__(self):
        self.done = False
        self.acknowledgements = []

    def is_done(self):
        return self.done

    async def defer(self, **kwargs):
        assert kwargs["ephemeral"] is True
        self.done = True
        self.acknowledgements.append(kwargs)


class Followup:
    def __init__(self):
        self.messages = []
        self.options = []

    async def send(self, content, **kwargs):
        assert kwargs["ephemeral"] is True
        mentions = kwargs["allowed_mentions"]
        assert mentions.everyone is False
        assert mentions.users is False
        assert mentions.roles is False
        assert len(content) <= 2000
        self.messages.append(content)
        self.options.append(kwargs)


class Rest:
    def __init__(self):
        self.owner = OWNER
        self.interaction = None
        self.calls = []

    def request(self, method, path, payload=None):
        assert self.interaction.response.is_done(), "Provider work preceded private acknowledgement"
        self.calls.append((method, path, payload))
        assert method == "GET", "The SDK-dispatch fixture must not send messages or change roles"
        if path == f"/guilds/{GUILD}":
            return {"id": GUILD, "owner_id": self.owner}
        if path == f"/guilds/{GUILD}/members/{OWNER}":
            return {"user": {"id": OWNER, "bot": False}, "roles": [PRO]}
        raise AssertionError((method, path))


def interaction(sdk, *, command="trial", subcommand="welcome", options=(), sequence=0):
    """A real Interaction exercises Namespace and SDK command argument transforms."""
    payload = {
        "id": str(1700000000000000100 + sequence),
        "type": 2,
        "application_id": BOT,
        "token": "offline-dispatch-fixture",
        "version": 1,
        "guild_id": GUILD,
        "attachment_size_limit": 10000000,
        "channel": {
            "id": CHANNEL if command == "trial" else REQUEST_CHANNEL,
            "type": 0,
            "name": "trial-management" if command == "trial" else "item-requests",
            "position": 0,
            "permission_overwrites": [],
        },
        "member": {
            "user": {"id": OWNER, "username": "owner", "discriminator": "0", "avatar": None},
            "roles": [],
            "joined_at": "2026-11-02T01:00:00+00:00",
            "deaf": False,
            "mute": False,
            "flags": 0,
        },
        "data": {
            "id": "1700000000000000097",
            "name": command,
            "type": 1,
            "guild_id": GUILD,
            "options": [{"type": 1, "name": subcommand, "options": list(options)}],
        },
    }
    result = discord.Interaction(data=payload, state=sdk._connection)
    result._cs_response = Response()
    result._cs_followup = Followup()
    return result


async def dispatch(tree, rest, target):
    rest.interaction = target
    await tree._call(target)
    assert target.command_failed is False
    assert len(target.response.acknowledgements) == 1
    assert len(target.followup.messages) == 1
    return target.followup.messages[0]


def run_gateway(monkeypatch, tmp_path, scenario):
    """Create the application's real tree without starting a network connection."""
    rest = Rest()
    requests = RequestStore(tmp_path / "requests.sqlite3", guild_id=GUILD)
    service = TrialService(rest, STATE, tmp_path / "trials.sqlite3", request_store=requests)
    original = discord.Client

    class Gateway(original):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._connection.user = discord.ClientUser(
                state=self._connection,
                data={"id": BOT, "username": "bot", "discriminator": "0", "avatar": None},
            )
            self._connection.application_id = int(BOT)

        async def start(self, token, *, reconnect):
            assert token == "offline"
            assert reconnect is True
            await self.on_ready()
            await scenario(self, self._connection._command_tree, rest, service, requests)

    monkeypatch.setattr(discord, "Client", Gateway)
    gateway = RequestGateway(
        rest, STATE, requests, token="offline", enabled=True, trial_service=service
    )
    try:
        asyncio.run(gateway._connect())
    finally:
        service.stop()


def test_real_sdk_dispatches_welcome_status_without_options(monkeypatch, tmp_path):
    async def scenario(sdk, tree, rest, service, _requests):
        target = interaction(sdk)
        reply = await dispatch(tree, rest, target)
        assert "**off**" in reply and "**24 hours**" in reply
        assert rest.calls == [("GET", f"/guilds/{GUILD}", None)]
        assert service.settings()["enabled"] is False

    run_gateway(monkeypatch, tmp_path, scenario)


def test_real_sdk_dispatches_welcome_options_and_rechecks_current_owner(monkeypatch, tmp_path):
    async def scenario(sdk, tree, rest, service, _requests):
        options = [
            {"name": "enabled", "type": 5, "value": True},
            {"name": "hours", "type": 4, "value": 24},
        ]
        reply = await dispatch(tree, rest, interaction(sdk, options=options))
        assert "**on**" in reply and "**24 hours**" in reply
        reopened = TrialStore(tmp_path / "trials.sqlite3", guild_id=GUILD, role_id=TRIAL)
        assert reopened.settings()["enabled"] is True
        assert reopened.settings()["hours"] == 24
        # A cached SDK Member object must not retain authorization after ownership changes.
        rest.owner = OTHER
        disable = [{"name": "enabled", "type": 5, "value": False}]
        reply = await dispatch(tree, rest, interaction(sdk, options=disable, sequence=1))
        assert "Only the current server owner" in reply
        assert service.settings()["enabled"] is True
        assert [path for _, path, _ in rest.calls] == [f"/guilds/{GUILD}"] * 2

    run_gateway(monkeypatch, tmp_path, scenario)


def test_real_sdk_trial_dispatch_preserves_request_command_group(monkeypatch, tmp_path):
    async def scenario(sdk, tree, rest, _service, requests):
        await dispatch(tree, rest, interaction(sdk))
        requests.set_dm_status(OWNER, "ready", channel_id=DM)
        target = interaction(
            sdk,
            command="request",
            subcommand="add",
            sequence=1,
            options=[{"name": "item", "type": 3, "value": "solar"}],
        )
        reply = await dispatch(tree, rest, target)
        assert "Request accepted" in reply
        assert [row["query"] for row in requests.list(OWNER)] == ["solar"]
        assert rest.calls[-1][1] == f"/guilds/{GUILD}/members/{OWNER}"

    run_gateway(monkeypatch, tmp_path, scenario)


def test_real_sdk_opens_multi_user_picker_without_member_option(monkeypatch, tmp_path):
    async def scenario(sdk, tree, rest, service, _requests):
        for seq, operation in enumerate(("grant", "extend", "revoke")):
            options = [] if operation == "revoke" else [
                {"name": "duration", "type": 3, "value": "7d"}
            ]
            target = interaction(sdk, subcommand=operation, options=options, sequence=seq)
            await dispatch(tree, rest, target)
            view = target.followup.options[0]["view"]
            picker = next(item for item in view.children if isinstance(item, discord.ui.UserSelect))
            assert picker.max_values == 25 and picker.min_values == 1
            assert view.operation == operation
            assert view.actor == OWNER
            assert target.followup.options[0]["wait"] is True
            assert service.list() == []
            view.stop()

    run_gateway(monkeypatch, tmp_path, scenario)


def test_stale_trial_components_are_private_and_never_replay(monkeypatch, tmp_path):
    async def scenario(sdk, _tree, rest, service, _requests):
        target = interaction(sdk)
        target.type = discord.InteractionType.component
        target.data = {"custom_id": "trial-ui:old-session:confirm", "component_type": 2}
        rest.interaction = target
        await sdk.on_interaction(target)
        assert "expired" in target.followup.messages[0]
        assert service.list() == []
        assert rest.calls == [("GET", f"/guilds/{GUILD}", None)]
        rest.owner = OTHER
        target = interaction(sdk, sequence=1)
        target.type = discord.InteractionType.component
        target.data = {"custom_id": "trial-ui:old-session:confirm", "component_type": 2}
        rest.interaction = target
        await sdk.on_interaction(target)
        assert "Only the current server owner" in target.followup.messages[0]
        assert service.list() == []

    run_gateway(monkeypatch, tmp_path, scenario)
