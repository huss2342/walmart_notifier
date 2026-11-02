"""Public entry point for private, read-only information about one's own trial."""

import asyncio

from .request_commands import _id, _request, _sdk
from .trial_commands import TrialCommands, _member_id
from .trials import _member

_DESCRIPTION = "Privately check your own Pro trial status and expiration"
_SCOPE = "Use /mytrial in this server's #general channel. Only you will see the reply."
_BUSY = "Your trial information is temporarily unavailable. Please try again shortly."


class MyTrialCommands:
    def __init__(self, client, state, service, *, event_sink=None):
        self.client, self.state, self.service = client, state, service
        self.guild = state["guild_id"]
        self.channel = state.get("channels", {}).get("general")
        if not _id(self.channel):
            raise ValueError("The general channel must be configured for /mytrial.")
        self.responses = TrialCommands(client, state, service, event_sink=event_sink)

    async def guard(self, interaction):
        if (
            str(getattr(interaction, "guild_id", None)) == self.guild
            and str(getattr(interaction, "channel_id", None)) == self.channel
        ):
            return True
        await self.responses._reply(interaction, _SCOPE)
        return False

    async def error(self, interaction, error=None):
        if error is not None:
            self.responses._failure(error)
        if await self.guard(interaction):
            await self.responses._reply(interaction, _BUSY)

    def _status(self, interaction):
        user = _member_id(getattr(interaction, "user", None))
        member = _request(self.client, "GET", f"/guilds/{self.guild}/members/{user}")
        _, roles, bot = _member(member, user)
        if bot:
            raise ValueError("Human members only")
        row = self.service.store.get(user, now=self.service.clock())
        separate = self.state["roles"]["pro"] in roles
        lines = ["**Your Pro trial**"]
        if row is None:
            lines.append("You have no recorded Pro trial.")
        elif row["revoked"]:
            lines.append("Your Pro trial was ended early.")
        elif row["active"]:
            expiry = int(row["expires_at"])
            lines.append(f"Your trial ends <t:{expiry}:F> (<t:{expiry}:R>).")
            if self.state["roles"]["trial"] not in roles:
                lines.append("Your trial is recorded; channel access is still syncing.")
            lines.append("The trial ends automatically and does not charge you.")
            if not separate:
                lines.append(
                    "When it ends, Pro access and saved item requests are removed "
                    "unless you have other Pro access."
                )
        else:
            lines.append(f"Your Pro trial expired <t:{int(row['expires_at'])}:F>.")
        if separate:
            lines.append(
                "You currently have separate Pro access. Trial expiry does not remove it. "
                "This command does not track its expiration."
            )
        return "\n".join(lines)

    async def show(self, interaction):
        if not await self.guard(interaction):
            return
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception as exc:
            self.responses._failure(exc)
            return
        try:
            content = await asyncio.to_thread(self._status, interaction)
        except Exception as exc:
            self.responses._failure(exc)
            content = _BUSY
        await self.responses._reply(interaction, content)


def build_mytrial_command(handler):
    discord = _sdk()

    async def show(interaction):
        await handler.show(interaction)

    command = discord.app_commands.Command(name="mytrial", description=_DESCRIPTION, callback=show)
    command.guild_only = True
    command.on_error = handler.error
    return command


def mytrial_command_definition():
    return {
        "name": "mytrial",
        "description": _DESCRIPTION,
        "type": 1,
        "options": [],
        "default_member_permissions": None,
        "dm_permission": False,
        "nsfw": False,
    }


def register_mytrial_command(client, state):
    application = _request(client, "GET", "/oauth2/applications/@me")
    if application.get("id") != state["bot_user_id"]:
        raise ValueError("Wrong trial application")
    return _request(
        client,
        "POST",
        f"/applications/{application['id']}/guilds/{state['guild_id']}/commands",
        mytrial_command_definition(),
    )
