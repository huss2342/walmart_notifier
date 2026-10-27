"""Guild-scoped Pro request commands and an optional outbound-only Gateway.

Discord.py handles interactions and reconnects. Membership checks, DM creation,
and message sends share the bridge's rate-aware REST transport. No member/message
intent or publicly reachable interaction server is needed.
"""

import asyncio
import logging
import math
import re
import threading
import time
import uuid
from contextlib import suppress

from .api import DiscordError
from .monitoring import emit_event

_SNOWFLAKE = re.compile(r"[0-9]{17,20}\Z")
_DM_HELP = (
    "Enable direct messages from this server in Discord's Privacy Settings, "
    "unblock the bot if needed, then run /request test-dm. "
    "Item alerts are DM only; there is no channel fallback."
)
_RETRY = "Discord is temporarily busy. Please retry in a moment."
_RECONNECTING = (
    "The bot is reconnecting to Discord. Please wait a moment and retry your command here."
)


def _id(value):
    return isinstance(value, str) and bool(_SNOWFLAKE.fullmatch(value))


def _configuration(state):
    guild = state.get("guild_id")
    role = state.get("roles", {}).get("pro")
    channel = state.get("channels", {}).get("item-requests")
    if not all(_id(value) for value in (guild, role, channel)):
        raise ValueError(
            "Personal requests require a guild, Pro role, and bound item-requests channel"
        )
    return guild, role, channel


def _display(value, limit=120):
    # Inline code makes request input readable without user-supplied formatting.
    value = str(value).replace("`", "ʼ").replace("\n", " ").replace("\r", " ")
    return value[:limit] + ("…" if len(value) > limit else "")


class _SDKLogFilter(logging.Filter):
    def filter(self, record):
        # SDK DEBUG payloads contain member queries and interaction tokens.
        # HTTP warnings/tracebacks can also contain token-bearing webhook URLs.
        if record.levelno < logging.WARNING:
            return False
        if (
            record.name in {"discord.http", "discord.gateway"}
            or record.exc_info
            or record.levelno >= logging.ERROR
        ):
            record.msg, record.args = "Discord SDK operation failed (%s)", (record.name,)
            record.exc_info = record.exc_text = record.stack_info = None
        return True


_SDK_LOG_FILTER = _SDKLogFilter()


def _sdk():
    import discord

    for name in tuple(logging.Logger.manager.loggerDict):
        if name == "discord" or name.startswith("discord."):
            logging.getLogger(name).addFilter(_SDK_LOG_FILTER)
    return discord


def _request(client, method, path, payload=None):
    """Allow short shared-transport contention, never wait out a long 429."""
    deadline = time.monotonic() + 1.0
    for _ in range(4):
        try:
            return client.request(method, path, payload)
        except DiscordError as exc:
            delay = exc.retry_after
            if exc.status != 429 or not isinstance(delay, (float, int)):
                raise
            if not math.isfinite(delay) or delay < 0 or delay > deadline - time.monotonic():
                raise
            time.sleep(max(0.01, delay))
    raise DiscordError(status=429, retry_after=1.0)


class _PrivateError(Exception):
    pass


class RequestCommands:
    """Framework-independent handlers with an authenticated owner on every path."""

    def __init__(self, client, state, store, *, event_sink=None):
        self.client, self.state, self.store = client, state, store
        self.guild, self.pro, self.channel = _configuration(state)
        self._events = event_sink

    def in_channel(self, interaction):
        return (
            str(getattr(interaction, "guild_id", None)) == self.guild
            and str(getattr(interaction, "channel_id", None)) == self.channel
        )

    @staticmethod
    def is_autocomplete(interaction):
        kind = getattr(interaction, "type", None)
        return getattr(kind, "value", kind) == 4

    def _failure(self, label):
        # Call sites supply fixed labels only, never exception or request data.
        emit_event(self._events, "worker_error", phase="worker", error_type=label)

    async def channel_guard(self, interaction):
        if self.in_channel(interaction):
            return True
        # An administrator can bypass command UI permissions. Acknowledge and
        # erase the ephemeral thinking response without leaving a status reply
        # or request data in another channel (including other guilds and DMs).
        try:
            if self.is_autocomplete(interaction):
                if not interaction.response.is_done():
                    await interaction.response.autocomplete([])
            else:
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True, thinking=True)
                await interaction.delete_original_response()
        except Exception:
            self._failure("request_scope_ack")
        return False

    def owner(self, interaction, expected=None):
        if not self.in_channel(interaction):
            raise _PrivateError("Use /request in this server's Pro-only #item-requests channel.")
        user = str(interaction.user.id)
        if not _id(user) or (expected is not None and user != expected):
            raise _PrivateError(
                "These controls belong to another member. Run /request list for your own requests."
            )
        return user

    def authorize(self, interaction, expected=None):
        user = self.owner(interaction, expected)
        try:
            member = _request(self.client, "GET", f"/guilds/{self.guild}/members/{user}")
        except DiscordError as exc:
            if exc.status == 404:
                raise _PrivateError(
                    "Personal item requests are available to current Pro members only."
                ) from None
            raise
        if (
            not isinstance(member, dict)
            or not isinstance(member.get("user"), dict)
            or member["user"].get("id") != user
            or not isinstance(member.get("roles"), list)
        ):
            raise DiscordError()
        if self.pro not in member["roles"] or member["user"].get("bot", False):
            raise _PrivateError("Personal item requests are available to current Pro members only.")
        return user

    async def _send(self, interaction, content, **kwargs):
        if not await self.channel_guard(interaction):
            return False
        # Expired interaction tokens and provider failures must not bubble to
        # SDK logging, where traceback exception text can contain webhook URLs.
        try:
            kwargs.update(ephemeral=True, allowed_mentions=_sdk().AllowedMentions.none())
            if interaction.response.is_done():
                await interaction.followup.send(content, **kwargs)
            else:
                await interaction.response.send_message(content, **kwargs)
        except Exception:
            self._failure("request_reply")
            return False
        return True

    async def error(self, interaction, _error=None):
        if not await self.channel_guard(interaction):
            return
        self._failure("request_command")
        if self.is_autocomplete(interaction):
            if not interaction.response.is_done():
                with suppress(Exception):
                    await interaction.response.autocomplete([])
            return
        await self._send(
            interaction, "Your request could not be completed. Please retry with /request list."
        )

    async def reconnecting(self, interaction):
        if not await self.channel_guard(interaction):
            return
        if self.is_autocomplete(interaction):
            if not interaction.response.is_done():
                with suppress(Exception):
                    await interaction.response.autocomplete([])
            return
        await self._send(interaction, _RECONNECTING)

    async def _execute(self, interaction, action, *, expected=None):
        if not await self.channel_guard(interaction):
            return
        # Acknowledge before any provider or SQLite work, including role checks.
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            user = await asyncio.to_thread(self.authorize, interaction, expected)
            await action(user)
        except _PrivateError as exc:
            await self._send(interaction, str(exc))
        except DiscordError:
            self._failure("request_provider")
            await self._send(interaction, _RETRY)
        except Exception:
            await self.error(interaction)

    async def _change(self, operation, *args):
        try:
            return await asyncio.to_thread(operation, *args)
        except ValueError as exc:
            # Only RequestStore validation messages are safe member-facing copy.
            # Never echo arbitrary provider/runtime exception text.
            raise _PrivateError(str(exc)) from None

    def _preflight_add(self, user, item):
        from .request_store import MAX_REQUESTS, parse_query

        query, _, _ = parse_query(item)
        if len(self.store.list(user)) >= MAX_REQUESTS:
            raise ValueError(
                "You can have up to 10 requests. Delete one with /request delete first."
            )
        return query

    def _test_delivery(self, user):
        try:
            channel = _request(self.client, "POST", "/users/@me/channels", {"recipient_id": user})
            if not isinstance(channel, dict) or not _id(channel.get("id")):
                raise DiscordError()
            channel_id = channel["id"]
            if channel.get("type") != 1:
                raise DiscordError()
            recipients = channel.get("recipients")
            if recipients is not None and (
                not isinstance(recipients, list)
                or len(recipients) != 1
                or not isinstance(recipients[0], dict)
                or recipients[0].get("id") != user
            ):
                raise DiscordError()
            payload = {
                "content": (
                    "Your personal item alert DMs are working. "
                    "Manage your requests in the server with /request list. "
                    "Requested item alerts will arrive here privately."
                ),
                "allowed_mentions": {"parse": [], "users": [], "roles": [], "replied_user": False},
                "nonce": uuid.uuid4().hex[:24],
                "enforce_nonce": True,
            }
            response = _request(self.client, "POST", f"/channels/{channel_id}/messages", payload)
            if not isinstance(response, dict) or not _id(response.get("id")):
                raise DiscordError()
            self.store.set_dm_status(user, "ready", channel_id=channel_id)
            return "ready"
        except DiscordError as exc:
            if exc.status == 403 or exc.provider_code == 50007:
                self.store.set_dm_status(user, "blocked")
                return "blocked"
            raise

    async def add(self, interaction, item):
        async def action(user):
            query = await self._change(self._preflight_add, user, item)
            status = await asyncio.to_thread(self.store.dm_status, user)
            if status == "needs_test":
                status = await asyncio.to_thread(self._test_delivery, user)
            row = await self._change(self.store.add, user, query)
            if status != "ready":
                await self._send(
                    interaction,
                    f"Request saved: `{_display(row['query'])}`. Alerts are paused. {_DM_HELP}",
                )
            else:
                await self._send(
                    interaction,
                    f"Request accepted: `{_display(row['query'])}`. "
                    "I'll DM you when a matching item is detected. "
                    "Requests stay active until deleted; each product alerts once. "
                    "Keywords may also match accessories. "
                    "Use /request list to manage your requests.",
                )

        await self._execute(interaction, action)

    async def list(self, interaction, *, expected=None):
        async def action(user):
            rows = await asyncio.to_thread(self.store.list, user)
            status = await asyncio.to_thread(self.store.dm_status, user)
            header = "**Your personal item requests** (up to 10)\n"
            if status != "ready":
                header += "**Alerts paused: DMs need attention.** " + _DM_HELP + "\n"
            if not rows:
                await self._send(
                    interaction,
                    header + "No requests yet. Use /request add with an item name, "
                    "Walmart link, or item ID.",
                )
                return
            text = header + "\n".join(
                f"{index}. `{_display(row['query'])}`"
                + {
                    "needs_review": " — delivery needs review",
                    "failed": " — delivery failed; a future fresh match can retry",
                }.get(row.get("delivery_status"), "")
                for index, row in enumerate(rows, 1)
            )
            await self._send(interaction, text, view=build_request_view(self, user, rows))

        await self._execute(interaction, action, expected=expected)

    async def edit(self, interaction, request, item, *, expected=None):
        async def action(user):
            row = await self._change(self.store.edit, user, request, item)
            await self._send(
                interaction,
                f"Request updated: `{_display(row['query'])}`. "
                "Use /request list to view your requests.",
            )

        await self._execute(interaction, action, expected=expected)

    async def delete(self, interaction, request, *, expected=None):
        async def action(user):
            deleted = await asyncio.to_thread(self.store.delete, user, request)
            await self._send(
                interaction,
                "Request deleted. Unsent alerts for it are canceled."
                if deleted
                else "Request not found. Use /request list to choose your own request.",
            )

        await self._execute(interaction, action, expected=expected)

    async def test_dm(self, interaction):
        async def action(user):
            status = await asyncio.to_thread(self._test_delivery, user)
            await self._send(
                interaction,
                "Test DM sent. Your personal item alerts are ready."
                if status == "ready"
                else "I couldn't send you a DM. Alerts are paused. " + _DM_HELP,
            )

        await self._execute(interaction, action)

    async def autocomplete(self, interaction, current):
        """Autocomplete choices are private by protocol and cannot be deferred."""

        def choices():
            user = self.authorize(interaction)
            needle = current.casefold()
            return [
                row
                for row in self.store.list(user)
                if needle in row["query"].casefold() or needle in str(row["id"])
            ]

        try:
            self.owner(interaction)
            rows = await asyncio.wait_for(asyncio.to_thread(choices), timeout=1.8)
            return [(str(row["query"])[:100], str(row["id"])) for row in rows[:10]]
        except Exception:
            return []


def build_request_view(handler, owner, rows):
    """Short-lived, owner-bound controls; all data changes reauthorize on submit."""
    discord = _sdk()

    class EditModal(discord.ui.Modal, title="Edit your item request"):
        item = discord.ui.TextInput(
            label="New item name, Walmart URL, or item ID", min_length=1, max_length=512
        )

        def __init__(self, request_id):
            super().__init__(timeout=300)
            self.request_id = request_id

        async def on_submit(self, interaction):
            await handler.edit(interaction, self.request_id, str(self.item.value), expected=owner)

        async def on_error(self, interaction, _error):
            await handler.error(interaction)

    class RequestView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=300)
            self.selected = str(rows[0]["id"])
            self.select = discord.ui.Select(
                placeholder="Choose a request to edit or delete",
                options=[
                    discord.SelectOption(label=str(row["query"])[:100], value=str(row["id"]))
                    for row in rows[:10]
                ],
            )
            self.select.callback = self.choose
            self.add_item(self.select)

        async def interaction_check(self, interaction):
            if not await handler.channel_guard(interaction):
                return False
            try:
                handler.owner(interaction, owner)
                return True
            except _PrivateError as exc:
                await handler._send(interaction, str(exc))
                return False

        async def choose(self, interaction):
            async def action(user):
                own = await asyncio.to_thread(handler.store.list, user)
                selected = self.select.values[0]
                row = next((row for row in own if str(row["id"]) == selected), None)
                if row is None:
                    raise _PrivateError(
                        "Request not found. Run /request list to refresh your controls."
                    )
                self.selected = selected
                await handler._send(
                    interaction,
                    f"Selected `{_display(row['query'])}`. "
                    "Use the Edit or Delete button on your request list.",
                )

            await handler._execute(interaction, action, expected=owner)

        @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, row=1)
        async def edit_button(self, interaction, _button):
            # Opening a blank modal is the immediate acknowledgement. Saved request
            # data is not fetched or shown; the submission checks fresh Pro access.
            if not await handler.channel_guard(interaction):
                return
            try:
                handler.owner(interaction, owner)
            except _PrivateError as exc:
                await handler._send(interaction, str(exc))
                return
            await interaction.response.send_modal(EditModal(self.selected))

        @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, row=1)
        async def delete_button(self, interaction, _button):
            await handler.delete(interaction, self.selected, expected=owner)

        @discord.ui.button(label="Refresh list", style=discord.ButtonStyle.secondary, row=1)
        async def refresh_button(self, interaction, _button):
            await handler.list(interaction, expected=owner)

        async def on_error(self, interaction, _error, _item):
            await handler.error(interaction)

    return RequestView()


def build_request_command_group(handler):
    discord = _sdk()
    app = discord.app_commands
    item_query = app.Range[str, 1, 512]
    group = app.Group(
        name="request", description="Manage your private Pro item alerts", guild_only=True
    )

    @group.command(
        name="add", description="Request an item and receive a private DM when it is found"
    )
    @app.describe(item="Item keywords, Walmart product URL, or exact item ID")
    async def add(interaction, item: item_query):
        await handler.add(interaction, item)

    @group.command(name="list", description="View your requests and private Edit/Delete controls")
    async def list_requests(interaction):
        await handler.list(interaction)

    @group.command(name="edit", description="Change one of your own item requests")
    @app.describe(
        request="Choose one of your own requests",
        item="Item keywords, Walmart product URL, or exact item ID",
    )
    async def edit(interaction, request: str, item: item_query):
        await handler.edit(interaction, request, item)

    @group.command(
        name="delete", description="Delete one of your own requests and cancel its unsent alerts"
    )
    @app.describe(request="Choose one of your own requests")
    async def delete(interaction, request: str):
        await handler.delete(interaction, request)

    @group.command(name="test-dm", description="Check private DM delivery and resume your alerts")
    async def test_dm(interaction):
        await handler.test_dm(interaction)

    async def autocomplete(interaction, current: str):
        return [
            app.Choice(name=name, value=value)
            for name, value in await handler.autocomplete(interaction, current)
        ]

    edit.autocomplete("request")(autocomplete)
    delete.autocomplete("request")(autocomplete)
    group.on_error = handler.error
    return group


def request_command_definition():
    """Registration payload independent of the SDK and existing guild commands."""

    def item():
        return {
            "type": 3,
            "name": "item",
            "description": "Item keywords, Walmart product URL, or exact item ID",
            "required": True,
            "min_length": 1,
            "max_length": 512,
        }

    def request():
        return {
            "type": 3,
            "name": "request",
            "description": "Choose one of your own requests",
            "required": True,
            "autocomplete": True,
        }

    return {
        "name": "request",
        "description": "Manage your private Pro item alerts",
        "type": 1,
        "dm_permission": False,
        "nsfw": False,
        "default_member_permissions": None,
        "contexts": None,
        "integration_types": None,
        "options": [
            {
                "type": 1,
                "name": "add",
                "description": "Request an item and receive a private DM when it is found",
                "options": [item()],
            },
            {
                "type": 1,
                "name": "list",
                "description": "View your requests and private Edit/Delete controls",
                "options": [],
            },
            {
                "type": 1,
                "name": "edit",
                "description": "Change one of your own item requests",
                "options": [request(), item()],
            },
            {
                "type": 1,
                "name": "delete",
                "description": "Delete one of your own requests and cancel its unsent alerts",
                "options": [request()],
            },
            {
                "type": 1,
                "name": "test-dm",
                "description": "Check private DM delivery and resume your alerts",
                "options": [],
            },
        ],
    }


def register_request_commands(client, state, *, application_id=None):
    guild, _, _ = _configuration(state)
    if application_id is None:
        application = _request(client, "GET", "/oauth2/applications/@me")
        application_id = application.get("id") if isinstance(application, dict) else None
    if not _id(application_id):
        raise ValueError("A valid Discord application ID is required")
    return _request(
        client,
        "POST",
        f"/applications/{application_id}/guilds/{guild}/commands",
        request_command_definition(),
    )


class RequestGateway:
    """A small daemon lifecycle around Discord.py; registration is always explicit."""

    def __init__(
        self, client, state, store, *, token, enabled=False, stop_event=None, event_sink=None
    ):
        self.enabled = bool(enabled)
        self.client, self.state, self.store = client, state, store
        self._token = token
        self._stop = stop_event if stop_event is not None else threading.Event()
        self._events = event_sink
        self._thread, self._loop, self._gateway = None, None, None
        self._connected, self._last_error = False, None
        if self.enabled:
            _configuration(state)
            if not _id(state.get("bot_user_id")):
                raise ValueError("Personal request Gateway requires a saved bot identity")
            if (
                not isinstance(token, str)
                or not token.strip()
                or any(char.isspace() for char in token)
            ):
                raise ValueError("Personal request Gateway requires a valid bot token")

    def _event(self, event):
        # Lifecycle readiness is reported in health; operational logs receive
        # only safe fixed error types through the bridge's existing sink.
        if event != "gateway_ready":
            emit_event(self._events, "worker_error", phase="worker", error_type=event)

    def start(self):
        if not self.enabled or self._stop.is_set() or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(
            target=self._run, name="discord-personal-requests", daemon=True
        )
        self._thread.start()

    def _run(self):
        try:
            asyncio.run(self._connect())
        except Exception:
            self._last_error = "gateway_connection"
            self._event("gateway_error")
        finally:
            self._connected = False
            self._loop = None

    async def _connect(self):
        discord = _sdk()
        self._loop = asyncio.get_running_loop()
        handler = RequestCommands(self.client, self.state, self.store, event_sink=self._events)
        gateway = discord.Client(
            intents=discord.Intents.none(), allowed_mentions=discord.AllowedMentions.none()
        )
        self._gateway = gateway
        tree = discord.app_commands.CommandTree(gateway)
        tree.add_command(
            build_request_command_group(handler), guild=discord.Object(id=int(handler.guild))
        )

        async def mark_ready():
            if self._stop.is_set() or gateway.is_closed():
                self._connected = False
                return
            expected = self.state["bot_user_id"]
            if (
                str(getattr(gateway.user, "id", None)) != expected
                or str(gateway.application_id) != expected
            ):
                self._connected, self._last_error = False, "gateway_identity"
                self._event("gateway_identity")
                await gateway.close()
                return
            self._connected, self._last_error = True, None
            self._event("gateway_ready")

        @gateway.event
        async def on_ready():
            await mark_ready()

        @gateway.event
        async def on_resumed():
            # A normal resumable reconnect emits RESUMED without another READY.
            # Revalidate the same identity boundary before restoring readiness.
            await mark_ready()

        @gateway.event
        async def on_disconnect():
            self._connected = False
            if not self._stop.is_set() and self._last_error != "gateway_identity":
                self._last_error = "gateway_reconnecting"

        @gateway.event
        async def on_error(_event, *_args, **_kwargs):
            self._last_error = "gateway_event"
            self._event("gateway_error")

        tree.on_error = handler.error

        async def interaction_check(interaction):
            if not await handler.channel_guard(interaction):
                return False
            if self._connected and not self._stop.is_set() and not gateway.is_closed():
                return True
            await handler.reconnecting(interaction)
            return False

        tree.interaction_check = interaction_check

        async def watch_stop():
            while not self._stop.is_set():
                await asyncio.sleep(0.25)
            await gateway.close()

        watcher = asyncio.create_task(watch_stop())
        try:
            async with gateway:
                if not self._stop.is_set():
                    await gateway.start(self._token, reconnect=True)
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher

    def stop(self):
        self._stop.set()
        self._connected = False
        if self._loop and self._gateway and self._loop.is_running():
            with suppress(RuntimeError):
                self._loop.call_soon_threadsafe(lambda: asyncio.create_task(self._gateway.close()))
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def health(self):
        return {
            "enabled": self.enabled,
            "connected": self._connected,
            "running": bool(self._thread and self._thread.is_alive()),
            "last_error": self._last_error,
        }
