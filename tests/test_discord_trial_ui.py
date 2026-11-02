"""Private batch controls exercise native UserSelect resolution and durable grants."""

import asyncio
import threading

import discord
import pytest
from discord_bridge.trial_ui import TrialBatchView

from test_discord_trials import BOT, CHANNEL, GUILD, OTHER, OWNER, USER, member, setup


class Response:
    def __init__(self):
        self.done = False

    def is_done(self):
        return self.done

    async def defer(self, **kwargs):
        assert kwargs == {"ephemeral": True, "thinking": False}
        self.done = True


class Interaction(discord.Interaction):
    async def edit_original_response(self, **kwargs):
        assert self.response.is_done()
        assert kwargs["allowed_mentions"].everyone is False
        assert kwargs["allowed_mentions"].users is False
        assert kwargs["allowed_mentions"].roles is False
        assert len(kwargs["content"]) <= 2000
        if getattr(self, "fail_edit", False):
            raise RuntimeError("private-edit-failure")
        self.edits.append(kwargs)


class Service:
    def __init__(self):
        self.now = 1000
        self.calls = []
        self.pending = set()
        self.errors = set()
        self.invalid = set()
        self.entered = self.release = None

    def clock(self):
        return self.now

    def _change(self, operation, user, kwargs):
        self.calls.append((operation, user, kwargs))
        if self.entered is not None:
            self.entered.set()
            assert self.release.wait(3)
        if user in self.errors:
            raise RuntimeError("private-token-and-provider-body")
        if user in self.invalid:
            raise ValueError("This member already has a trial. Use extend to add time.")
        self.now += 5
        return {
            "user_id": user,
            "synced": user not in self.pending,
            "expires_at": kwargs.get("until", self.now + kwargs.get("seconds", 0)),
        }

    def grant(self, user, **kwargs):
        return self._change("grant", user, kwargs)

    def extend(self, user, **kwargs):
        return self._change("extend", user, kwargs)

    def revoke(self, user, **kwargs):
        return self._change("revoke", user, kwargs)


class Handler:
    def __init__(self, service=None):
        self.service = service or Service()
        self.owner = OWNER
        self.authorizations = []
        self.replies = []
        self.failures = []
        self._views = {}

    async def authorize(self, target, *, expected_actor=None, thinking=True):
        self.authorizations.append((str(target.user.id), expected_actor, thinking))
        if (
            str(target.guild_id) != GUILD
            or str(target.channel_id) != CHANNEL
            or str(target.user.id) != self.owner
            or str(target.user.id) != expected_actor
        ):
            await self._reply(target, "These private controls are not available to you.")
            return None
        await target.response.defer(ephemeral=True, thinking=thinking)
        return str(target.user.id)

    async def _reply(self, target, content, **kwargs):
        assert len(content) <= 2000
        self.replies.append(content)

    def _failure(self, error):
        self.failures.append(type(error).__name__)


def sdk_client():
    sdk = discord.Client(intents=discord.Intents.none())
    sdk._connection.user = discord.ClientUser(
        state=sdk._connection,
        data={"id": BOT, "username": "bot", "discriminator": "0", "avatar": None},
    )
    return sdk


def component(sdk, view, kind, *, users=(), actor=OWNER, guild=GUILD, channel=CHANNEL):
    control = {"select": view.select, "confirm": view.confirm_button, "cancel": view.cancel_button}[
        kind
    ]
    data = {"custom_id": control.custom_id, "component_type": 5 if kind == "select" else 2}
    if kind == "select":
        data.update(
            values=list(users),
            resolved={
                "users": {
                    user: {
                        "id": user,
                        "username": "member",
                        "discriminator": "0",
                        "avatar": None,
                        "bot": False,
                    }
                    for user in users
                },
                "members": {
                    user: {
                        "roles": [],
                        "joined_at": "2026-11-02T01:00:00+00:00",
                        "deaf": False,
                        "mute": False,
                        "flags": 0,
                    }
                    for user in users
                },
            },
        )
    payload = {
        "id": "1700000000000000099",
        "type": 3,
        "application_id": BOT,
        "token": "offline-ui-fixture",
        "version": 1,
        "guild_id": guild,
        "attachment_size_limit": 10000000,
        "channel": {
            "id": channel,
            "type": 0,
            "name": "trial-management",
            "position": 0,
            "permission_overwrites": [],
        },
        "member": {
            "user": {"id": actor, "username": "owner", "discriminator": "0", "avatar": None},
            "roles": [],
            "joined_at": None,
            "deaf": False,
            "mute": False,
            "flags": 0,
        },
        "data": data,
    }
    target = Interaction(data=payload, state=sdk._connection)
    target._cs_response = Response()
    target.edits = []
    return target, control


async def click(sdk, view, kind, **kwargs):
    target, control = component(sdk, view, kind, **kwargs)
    await view._scheduled_task(control, target)
    return target


def test_native_picker_selects_many_without_granting_then_confirms_common_expiry():
    async def scenario():
        sdk, handler = sdk_client(), Handler()
        view = TrialBatchView(
            handler, actor=OWNER, intent="1234567890123456789", operation="grant", seconds=3600
        )
        try:
            assert isinstance(view.select, discord.ui.UserSelect)
            assert view.select.min_values == 1 and view.select.max_values == 25
            assert view.confirm_button.disabled and view.timeout == 180
            selected = await click(sdk, view, "select", users=[USER, OTHER, USER])
            assert view.selected == (USER, OTHER)
            assert not handler.service.calls and not view.confirm_button.disabled
            assert selected.edits and "2" in selected.edits[-1]["content"]
            confirmed = await click(sdk, view, "confirm")
            assert len(handler.service.calls) == 2
            assert {call[2]["until"] for call in handler.service.calls} == {4600}
            assert [call[2]["interaction_id"] for call in handler.service.calls] == [
                f"1234567890123456789:grant:{USER}",
                f"1234567890123456789:grant:{OTHER}",
            ]
            assert all(call[2]["actor_id"] == OWNER for call in handler.service.calls)
            assert all(child.disabled for child in view.children) and view.is_finished()
            assert "confirmed" in confirmed.edits[-1]["content"].lower()
            assert len(handler.authorizations) == 2
        finally:
            view.stop()
            await sdk.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["select", "confirm", "cancel"])
@pytest.mark.parametrize("mismatch", ["owner", "channel", "guild", "creator"])
def test_every_callback_rechecks_owner_scope_and_creator(kind, mismatch):
    async def scenario():
        sdk, handler = sdk_client(), Handler()
        view = TrialBatchView(handler, actor=OWNER, intent="batch", operation="grant", seconds=3600)
        options = {"users": [USER]} if kind == "select" else {}
        if mismatch == "owner":
            handler.owner = OTHER
        elif mismatch == "creator":
            options["actor"] = OTHER
        else:
            options[mismatch] = OTHER
        try:
            await click(sdk, view, kind, **options)
            assert not handler.service.calls and not view.selected
            assert handler.replies
            assert handler.authorizations == [
                (OTHER if mismatch == "creator" else OWNER, OWNER, False)
            ]
        finally:
            view.stop()
            await sdk.close()

    asyncio.run(scenario())


def test_cancel_and_timeout_disable_controls_without_role_changes():
    async def scenario():
        sdk, handler = sdk_client(), Handler()
        view = TrialBatchView(handler, actor=OWNER, intent="cancel", operation="revoke")
        expired = TrialBatchView(handler, actor=OWNER, intent="expire", operation="revoke")

        class Message:
            edits = []

            async def edit(self, **kwargs):
                assert kwargs["allowed_mentions"].everyone is False
                self.edits.append(kwargs)

        expired.message = Message()
        try:
            await click(sdk, view, "select", users=[USER])
            cancelled = await click(sdk, view, "cancel")
            assert "cancelled" in cancelled.edits[-1]["content"].lower()
            await expired.on_timeout()
            assert "expired" in expired.message.edits[-1]["content"].lower()
            assert all(item.disabled for item in view.children + expired.children)
            assert view.is_finished() and expired.is_finished()
            assert not handler.service.calls
        finally:
            view.stop()
            expired.stop()
            await sdk.close()

    asyncio.run(scenario())


def test_concurrent_confirm_and_timeout_cannot_repeat_or_interrupt_submission():
    async def scenario():
        sdk, handler = sdk_client(), Handler()
        handler.service.entered, handler.service.release = threading.Event(), threading.Event()
        view = TrialBatchView(handler, actor=OWNER, intent="once", operation="extend", seconds=3600)
        try:
            await click(sdk, view, "select", users=[USER])
            first = asyncio.create_task(click(sdk, view, "confirm"))
            assert await asyncio.to_thread(handler.service.entered.wait, 1)
            duplicate = asyncio.create_task(click(sdk, view, "confirm"))
            timeout = asyncio.create_task(view.on_timeout())
            handler.service.release.set()
            result, _, _ = await asyncio.gather(first, duplicate, timeout)
            assert len(handler.service.calls) == 1
            assert "confirmed" in result.edits[-1]["content"].lower()
            assert "expired" not in result.edits[-1]["content"].lower()
            assert view.is_finished() and all(item.disabled for item in view.children)
        finally:
            handler.service.release.set()
            view.stop()
            await sdk.close()

    asyncio.run(scenario())


def test_partial_batch_reports_confirmed_pending_and_unknown_without_exception_text():
    async def scenario():
        sdk, handler = sdk_client(), Handler()
        third = str(int(OTHER) + 20)
        handler.service.pending.add(OTHER)
        handler.service.errors.add(third)
        view = TrialBatchView(handler, actor=OWNER, intent="partial", operation="grant", until=5000)
        try:
            await click(sdk, view, "select", users=[USER, OTHER, third])
            target = await click(sdk, view, "confirm")
            content = target.edits[-1]["content"]
            assert len(handler.service.calls) == 3
            assert "pending" in content and "check /trial list" in content
            assert all(user in content for user in (USER, OTHER, third))
            assert "private-token" not in content + str(handler.failures)
            assert view.is_finished()
        finally:
            view.stop()
            await sdk.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["empty", "too_many", "unresolved"])
def test_invalid_or_injected_selection_cannot_enable_confirmation(invalid):
    async def scenario():
        sdk, handler = sdk_client(), Handler()
        view = TrialBatchView(handler, actor=OWNER, intent="invalid", operation="revoke")
        users = [] if invalid == "empty" else [str(int(USER) + i) for i in range(26)]
        if invalid == "unresolved":
            users = [USER]
        target, control = component(sdk, view, "select", users=users)
        if invalid == "unresolved":
            target.data["resolved"] = {}
        try:
            await view._scheduled_task(control, target)
            assert not view.selected and view.confirm_button.disabled
            assert not handler.service.calls
        finally:
            view.stop()
            await sdk.close()

    asyncio.run(scenario())


def test_maximum_extend_batch_keeps_each_result_and_expiration_under_message_limit():
    async def scenario():
        sdk, handler = sdk_client(), Handler()
        users = [str(int(USER) + i) for i in range(25)]
        view = TrialBatchView(handler, actor=OWNER, intent="full", operation="extend", seconds=3600)
        try:
            await click(sdk, view, "select", users=users)
            target = await click(sdk, view, "confirm")
            content = target.edits[-1]["content"]
            assert len(handler.service.calls) == 25
            assert all(user in content for user in users)
            assert content.count("<t:") == 25 and len(content) <= 2000
        finally:
            view.stop()
            await sdk.close()

    asyncio.run(scenario())


def test_confirmed_picker_applies_real_sqlite_grants_only_after_confirmation(tmp_path):
    async def scenario():
        service, provider, _, _ = setup(tmp_path)
        provider.members[OTHER] = member(OTHER)
        sdk, handler = sdk_client(), Handler(service)
        view = TrialBatchView(
            handler, actor=OWNER, intent="durable", operation="grant", seconds=3600
        )
        try:
            await click(sdk, view, "select", users=[USER, OTHER])
            assert service.list() == [] and not provider.writes
            await click(sdk, view, "confirm")
            assert {row["user_id"] for row in service.list()} == {USER, OTHER}
            assert {row["expires_at"] for row in service.list()} == {4600}
            assert len(provider.writes) == 2
        finally:
            view.stop()
            service.stop()
            await sdk.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("terminal", ["timeout", "cancel"])
def test_confirm_waiting_for_owner_check_cannot_apply_after_expiry_or_cancel(terminal):
    async def scenario():
        sdk, handler = sdk_client(), Handler()
        view = TrialBatchView(handler, actor=OWNER, intent="waiting", operation="revoke")
        entered, release = asyncio.Event(), asyncio.Event()
        original = handler.authorize

        async def delayed(target, **kwargs):
            if getattr(target, "wait_for_owner", False):
                entered.set()
                await release.wait()
            return await original(target, **kwargs)

        handler.authorize = delayed
        try:
            await click(sdk, view, "select", users=[USER])
            target, control = component(sdk, view, "confirm")
            target.wait_for_owner = True
            confirmation = asyncio.create_task(view._scheduled_task(control, target))
            await entered.wait()
            if terminal == "timeout":
                await view.on_timeout()
            else:
                await click(sdk, view, "cancel")
            release.set()
            await confirmation
            assert not handler.service.calls
            assert view.state == ("expired" if terminal == "timeout" else "cancelled")
        finally:
            release.set()
            view.stop()
            await sdk.close()

    asyncio.run(scenario())


def test_selection_changed_during_confirm_authorization_requires_another_review():
    async def scenario():
        sdk, handler = sdk_client(), Handler()
        view = TrialBatchView(handler, actor=OWNER, intent="review", operation="revoke")
        entered, release = asyncio.Event(), asyncio.Event()
        original = handler.authorize

        async def delayed(target, **kwargs):
            if getattr(target, "wait_for_owner", False):
                entered.set()
                await release.wait()
            return await original(target, **kwargs)

        handler.authorize = delayed
        try:
            await click(sdk, view, "select", users=[USER])
            target, control = component(sdk, view, "confirm")
            target.wait_for_owner = True
            confirmation = asyncio.create_task(view._scheduled_task(control, target))
            await entered.wait()
            await click(sdk, view, "select", users=[OTHER])
            release.set()
            await confirmation
            assert not handler.service.calls and view.state == "review"
            await click(sdk, view, "confirm")
            assert [call[1] for call in handler.service.calls] == [OTHER]
        finally:
            release.set()
            view.stop()
            await sdk.close()

    asyncio.run(scenario())


def test_unconfirmed_selection_edit_failure_closes_the_picker_without_mutation():
    async def scenario():
        sdk, handler = sdk_client(), Handler()
        view = TrialBatchView(handler, actor=OWNER, intent="lost-card", operation="revoke")
        target, control = component(sdk, view, "select", users=[USER])
        target.fail_edit = True
        try:
            await click(sdk, view, "select", users=[OTHER])
            await view._scheduled_task(control, target)
            await click(sdk, view, "confirm")
            assert not handler.service.calls
            assert view.is_finished() and all(item.disabled for item in view.children)
        finally:
            view.stop()
            await sdk.close()

    asyncio.run(scenario())


def test_expected_validation_failure_is_not_logged_as_worker_error():
    async def scenario():
        sdk, handler = sdk_client(), Handler()
        handler.service.invalid.add(USER)
        view = TrialBatchView(
            handler, actor=OWNER, intent="validation", operation="grant", seconds=3600
        )
        try:
            await click(sdk, view, "select", users=[USER, OTHER])
            target = await click(sdk, view, "confirm")
            content = target.edits[-1]["content"]
            assert "not applied" in content and "confirmed" in content
            assert not handler.failures
            assert "Optional copy for confirmed members" in content
        finally:
            view.stop()
            await sdk.close()

    asyncio.run(scenario())
