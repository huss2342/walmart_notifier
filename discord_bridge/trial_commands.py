"""Private, owner-only trial controls; command registration is always explicit."""

import asyncio
import math
import re
import weakref
from datetime import datetime

from .api import DiscordError
from .monitoring import emit_event, error_fields
from .request_commands import _id, _request, _sdk

_DESCRIPTION = "Manage owner-issued Pro trials and the welcome offer"
_DESCRIPTIONS = {
    "grant": "Give selected members temporary Pro access",
    "extend": "Add time to selected members' trials",
    "revoke": "End selected members' complimentary access",
    "list": "List current trials and pending access changes",
    "welcome": "View or change automatic trials for new members",
}
_MEMBER = "Optional single member; omit to select up to 25 members privately"
_DURATION = "Trial duration, such as 8h, 24h, 7d or 30d"
_UNTIL = "Exact expiry with timezone, such as 2026-11-10T18:00:00-06:00"
_PAGE = "Page of trials to display"
_ENABLED = "Enable or disable trials for future new members; omit to view status"
_HOURS = "Length of future welcome trials in hours (recommended: 24)"
_SCOPE = "Use /trial in this server's owner-only #trial-management channel."
_BUSY = "Trial controls are temporarily unavailable. Please retry in a moment."


class _InputError(ValueError):
    pass


def _member_id(member):
    value = getattr(member, "id", member)
    if isinstance(value, bool) or not _id(str(value)):
        raise _InputError("Choose a valid server member.")
    return str(value)


def _seconds(duration):
    match = re.fullmatch(r"([1-9][0-9]{0,4})([hd])", str(duration).strip().lower())
    if match is None:
        raise _InputError("Use a duration such as 8h, 24h, 7d or 30d.")
    seconds = int(match[1]) * (3600 if match[2] == "h" else 86400)
    if seconds > 366 * 86400:
        raise _InputError("Trial duration cannot exceed 366 days.")
    return seconds


def _expiry(until):
    if not isinstance(until, str) or len(until) > 64:
        raise _InputError("Enter an ISO date and time with an explicit timezone.")
    try:
        value = datetime.fromisoformat(until.strip().replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError
        result = value.timestamp()
        if not math.isfinite(result):
            raise ValueError
    except (ValueError, OverflowError, OSError):
        raise _InputError("Include the timezone, for example 2026-11-10T18:00:00-06:00.") from None
    return result


def _configuration(state):
    values = (
        state.get("guild_id"),
        state.get("bot_user_id"),
        state.get("roles", {}).get("trial"),
        state.get("channels", {}).get("trial-management"),
    )
    if not all(_id(value) for value in values) or len(set(values)) != len(values):
        raise ValueError("Trial controls require their exact guild, bot, role and private channel.")
    return values[0], values[3]


class TrialCommands:
    """Scope and freshly read server ownership authorize every operation."""

    def __init__(self, client, state, service, *, event_sink=None):
        self.client, self.state, self.service = client, state, service
        self._events = event_sink
        self.guild, self.channel = _configuration(state)
        self._views = weakref.WeakValueDictionary()

    def _failure(self, error):
        original = getattr(error, "original", error)
        fields = error_fields(original)
        if isinstance(original, DiscordError) and original.status is not None:
            fields["status"] = original.status
        emit_event(
            self._events, "worker_error", component="trials", phase="worker",
            **fields,
        )

    def in_channel(self, interaction):
        return (
            str(getattr(interaction, "guild_id", None)) == self.guild
            and str(getattr(interaction, "channel_id", None)) == self.channel
        )

    async def _reply(self, interaction, content, **kwargs):
        try:
            send = (
                interaction.followup.send
                if interaction.response.is_done()
                else interaction.response.send_message
            )
            if kwargs.get("view") is not None and interaction.response.is_done():
                kwargs["wait"] = True
            message = await send(
                content[:2000], ephemeral=True, allowed_mentions=_sdk().AllowedMentions.none(),
                **kwargs,
            )
        except Exception as exc:
            # Never leak interaction tokens through SDK exception tracebacks.
            self._failure(exc)
            return False
        return message if message is not None else True

    async def guard(self, interaction):
        if self.in_channel(interaction):
            return True
        await self._reply(interaction, _SCOPE)
        return False

    def _owner(self, interaction):
        user = _member_id(getattr(interaction, "user", None))
        guild = _request(self.client, "GET", f"/guilds/{self.guild}")
        if (
            not isinstance(guild, dict)
            or guild.get("id") != self.guild
            or not _id(guild.get("owner_id"))
        ):
            raise DiscordError("Invalid guild response")
        if guild["owner_id"] != user:
            raise _InputError("Only the current server owner can manage trials.")
        return user

    @staticmethod
    def _intent(interaction, actor):
        interaction_id = str(getattr(interaction, "id", None))
        if not _id(interaction_id):
            raise _InputError("This command has expired. Run /trial again.")
        return {"actor_id": actor, "interaction_id": interaction_id}

    async def authorize(self, interaction, *, expected_actor=None, thinking=True):
        """Acknowledge privately before reading fresh ownership for every action."""
        if not await self.guard(interaction):
            return None
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=thinking)
        except Exception as exc:
            # An expired or unacknowledged command must not mutate access.
            self._failure(exc)
            return None
        try:
            actor = await asyncio.to_thread(self._owner, interaction)
            if expected_actor is not None and actor != expected_actor:
                raise _InputError("Only the owner who opened these controls can use them.")
            return actor
        except _InputError as exc:
            await self._reply(interaction, str(exc))
        except Exception as exc:
            self._failure(exc)
            await self._reply(interaction, _BUSY)
        return None

    async def _execute(self, interaction, action):
        actor = await self.authorize(interaction)
        if actor is None:
            return
        try:
            content = await asyncio.to_thread(action, actor)
        except _InputError as exc:
            content = str(exc)
        except ValueError:
            content = (
                "Trial settings could not be applied. Check the member, expiry and current trial "
                "status, then retry."
            )
        except Exception as exc:
            self._failure(exc)
            content = _BUSY
        await self._reply(interaction, content)

    async def open_picker(self, interaction, operation, *, duration=None, until=None):
        actor = await self.authorize(interaction)
        if actor is None:
            return
        try:
            if operation == "grant" and (duration is None) == (until is None):
                raise _InputError("Choose exactly one of duration or until for the trial expiry.")
            seconds = _seconds(duration) if duration is not None else None
            expiry = _expiry(until) if until is not None else None
            now = self.service.clock() if expiry is not None else None
            if expiry is not None and not now < expiry <= now + 366 * 86400:
                raise _InputError("Choose an expiry in the future, within 366 days.")
            if operation == "extend" and seconds is None:
                raise _InputError("Choose the amount of time to add, such as 7d.")
            from .trial_ui import TrialBatchView

            view = TrialBatchView(
                self, actor=actor, intent=self._intent(interaction, actor)["interaction_id"],
                operation=operation, seconds=seconds, until=expiry,
            )
            self._views[view.nonce] = view
            message = await self._reply(interaction, view.content(), view=view)
            if message is False:
                view.stop()
            elif message is not True:
                view.message = message
        except _InputError as exc:
            await self._reply(interaction, str(exc))
        except Exception as exc:
            self._failure(exc)
            await self._reply(interaction, _BUSY)

    async def expired_controls(self, interaction):
        if await self.authorize(interaction, thinking=False) is not None:
            await self._reply(interaction, "These trial controls expired. Run /trial again.")

    async def error(self, interaction, _error=None):
        if _error is not None:
            self._failure(_error)
        if await self.guard(interaction):
            await self._reply(interaction, _BUSY)

    @staticmethod
    def _result(row, verb):
        user = _member_id(row["user_id"])
        if row.get("synced") is not True:
            return (
                f"Trial {verb} recorded for <@{user}>; access synchronization is pending. "
                "Use /trial list to check its status. Paid Pro access is preserved."
            )
        if verb == "revocation":
            return f"Trial access ended for <@{user}>. Paid Pro access is preserved."
        expiry = int(row["expires_at"])
        return (
            f"Trial {verb} confirmed for <@{user}>. Expires <t:{expiry}:F> (<t:{expiry}:R>). "
            "Paid Pro access is preserved.\n\n"
            "**Optional message to copy:**\n"
            f"Your Pro trial is active until <t:{expiry}:F>. "
            "Enjoy all Pro feeds and personal requests. No signup or charge."
        )

    async def grant(self, interaction, member=None, duration=None, until=None):
        if member is None:
            await self.open_picker(interaction, "grant", duration=duration, until=until)
            return
        def action(actor):
            if (duration is None) == (until is None):
                raise _InputError("Choose exactly one of duration or until for the trial expiry.")
            row = self.service.grant(
                _member_id(member),
                seconds=_seconds(duration) if duration is not None else None,
                until=_expiry(until) if until is not None else None,
                **self._intent(interaction, actor),
            )
            return self._result(row, "grant")

        await self._execute(interaction, action)

    async def extend(self, interaction, member=None, duration=None):
        if member is None:
            await self.open_picker(interaction, "extend", duration=duration)
            return
        def action(actor):
            row = self.service.extend(
                _member_id(member), seconds=_seconds(duration), **self._intent(interaction, actor)
            )
            return self._result(row, "extension")

        await self._execute(interaction, action)

    async def revoke(self, interaction, member=None):
        if member is None:
            await self.open_picker(interaction, "revoke")
            return
        def action(actor):
            row = self.service.revoke(_member_id(member), **self._intent(interaction, actor))
            return self._result(row, "revocation")

        await self._execute(interaction, action)

    async def list(self, interaction, page=1):
        def action(_actor):
            if type(page) is not int or not 1 <= page <= 10000:
                raise _InputError("Choose a page number between 1 and 10000.")
            rows = self.service.list(offset=(page - 1) * 15, limit=15)
            lines = [f"**Pro trials — page {page}**"]
            for row in rows:
                user = _member_id(row["user_id"])
                state = "active" if row.get("active") else "ended"
                if row.get("synced") is not True:
                    state += "; sync pending"
                lines.append(f"<@{user}> · <t:{int(row['expires_at'])}:F> · {state}")
            if not rows:
                lines.append("No trials on this page.")
            lines.append("Use /trial list page: followed by a number for another page.")
            return "\n".join(lines)

        await self._execute(interaction, action)

    async def welcome(self, interaction, enabled=None, hours=24):
        def action(actor):
            if enabled is None:
                settings = self.service.settings()
            else:
                if type(enabled) is not bool or type(hours) is not int or not 1 <= hours <= 168:
                    raise _InputError("Choose enabled true or false and a duration of 1–168 hours.")
                settings = self.service.configure_welcome(
                    enabled, hours=hours, **self._intent(interaction, actor)
                )
            state = "on" if settings["enabled"] else "off"
            return (
                f"Automatic welcome trials are **{state}**; "
                f"duration: **{settings['hours']} hours**. "
                "Each new member can receive one welcome trial. "
                "Existing trials keep their expiration when the offer is turned off. "
                "Saved item requests are deleted when both paid and trial access end."
            )

        await self._execute(interaction, action)


def build_trial_command_group(handler):
    discord = _sdk()
    app = discord.app_commands
    member_user = discord.User
    duration_text = app.Range[str, 1, 32]
    expiry_text = app.Range[str, 1, 64]
    page_number = app.Range[int, 1, 10000]
    hour_count = app.Range[int, 1, 168]
    group = app.Group(
        name="trial",
        description=_DESCRIPTION,
        guild_only=True,
        default_permissions=discord.Permissions.none(),
    )

    @group.command(name="grant", description=_DESCRIPTIONS["grant"])
    @app.describe(member=_MEMBER, duration=_DURATION, until=_UNTIL)
    async def grant(
        interaction, member: member_user = None, duration: duration_text = None,
        until: expiry_text = None,
    ):
        await handler.grant(interaction, member, duration, until)

    @group.command(name="extend", description=_DESCRIPTIONS["extend"])
    @app.describe(member=_MEMBER, duration=_DURATION)
    async def extend(interaction, duration: duration_text, member: member_user = None):
        await handler.extend(interaction, member, duration)

    @group.command(name="revoke", description=_DESCRIPTIONS["revoke"])
    @app.describe(member=_MEMBER)
    async def revoke(interaction, member: member_user = None):
        await handler.revoke(interaction, member)

    @group.command(name="list", description=_DESCRIPTIONS["list"])
    @app.describe(page=_PAGE)
    async def list_trials(interaction, page: page_number = 1):
        await handler.list(interaction, page)

    @group.command(name="welcome", description=_DESCRIPTIONS["welcome"])
    @app.describe(enabled=_ENABLED, hours=_HOURS)
    async def welcome(interaction, enabled: bool = None, hours: hour_count = 24):
        await handler.welcome(interaction, enabled, hours)

    group.on_error = handler.error
    return group


def trial_command_definition():
    def option(name, kind, description, required=False, **limits):
        return {
            "type": kind,
            "name": name,
            "description": description,
            "required": required,
            **limits,
        }

    member = option("member", 6, _MEMBER)
    duration = option("duration", 3, _DURATION, min_length=1, max_length=32)
    options = {
        "grant": [member, duration, option("until", 3, _UNTIL, min_length=1, max_length=64)],
        "extend": [dict(duration, required=True), member],
        "revoke": [member],
        "list": [option("page", 4, _PAGE, min_value=1, max_value=10000)],
        "welcome": [
            option("enabled", 5, _ENABLED),
            option("hours", 4, _HOURS, min_value=1, max_value=168),
        ],
    }
    return {
        "name": "trial",
        "description": _DESCRIPTION,
        "type": 1,
        "default_member_permissions": "0",
        "dm_permission": False,
        "nsfw": False,
        "options": [
            {"type": 1, "name": name, "description": description, "options": options[name]}
            for name, description in _DESCRIPTIONS.items()
        ],
    }


def register_trial_commands(client, state):
    guild, _ = _configuration(state)
    application = _request(client, "GET", "/oauth2/applications/@me")
    if not isinstance(application, dict) or application.get("id") != state["bot_user_id"]:
        raise ValueError("Trial command registration requires the configured bot application.")
    return _request(
        client,
        "POST",
        f"/applications/{application['id']}/guilds/{guild}/commands",
        trial_command_definition(),
    )
