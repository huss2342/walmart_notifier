"""Expired controls, acknowledgement failures and real SDK command routing."""

import asyncio
from types import SimpleNamespace

from test_discord_request_commands import (
    CHANNEL,
    GUILD,
    Interaction,
    _gateway_scenario,
    invoke,
    make,
    real_commands,
)


def test_expired_list_disables_controls_and_replaces_private_message():
    async def scenario():
        commands, interaction, _, store = make()
        store.add(str(interaction.user.id), "chair")
        edits = []

        async def edit(**kwargs):
            edits.append(kwargs)

        message = SimpleNamespace(edit=edit)

        async def followup(content, **kwargs):
            await interaction.send(content, **kwargs)
            return message

        interaction.followup.send = followup
        await commands.list(interaction)
        view = interaction.messages[-1][1]["view"]
        assert view.message is message
        await view.on_timeout()
        assert all(child.disabled for child in view.children)
        assert "expired" in edits[0]["content"].lower()
        assert "/request list" in edits[0]["content"]
        assert len(store.list(str(interaction.user.id))) == 1

    asyncio.run(scenario())


def test_gateway_gives_private_guidance_for_expired_or_prerestart_controls(monkeypatch):
    async def scenario(sdk, tree, worker, client, store):
        for custom_id in ("old-random-sdk-button", "request-ui:" + "a" * 32 + ":delete"):
            interaction = Interaction()
            interaction.type = SimpleNamespace(value=3)
            interaction.data = {"custom_id": custom_id, "component_type": 2}
            await sdk.on_interaction(interaction)
            assert "expired" in interaction.messages[-1][0].lower()
            assert "/request list" in interaction.messages[-1][0]
        assert client.calls == [] and store.list(str(interaction.user.id)) == []
        elsewhere = Interaction(channel="1700000000000000006")
        elsewhere.type = SimpleNamespace(value=3)
        elsewhere.data = {"custom_id": "old-random-sdk-button"}
        await sdk.on_interaction(elsewhere)
        assert elsewhere.messages == []

    _gateway_scenario(monkeypatch, scenario)


def test_actual_sdk_active_delete_and_observer_acknowledge_and_mutate_once(monkeypatch):
    async def scenario(sdk, tree, worker, client, store):
        from discord_bridge.request_commands import build_request_view

        user = "1700000000000000005"
        row = store.add(user, "chair")
        view = build_request_view(sdk._request_handler, user, [row])
        button = next(child for child in view.children if getattr(child, "label", None) == "Delete")
        registry = sdk._connection._view_store
        registry.add_view(view, message_id=1234)
        interaction = Interaction()
        interaction.message = SimpleNamespace(id=1234)
        interaction.type = SimpleNamespace(value=3)
        interaction.data = {"custom_id": button.custom_id, "component_type": 2}
        client.interaction = interaction
        registry.dispatch_view(2, button.custom_id, interaction)
        await sdk.on_interaction(interaction)
        for _ in range(100):
            if interaction.messages:
                break
            await asyncio.sleep(0.01)
        assert len(interaction.messages) == 1 and "deleted" in interaction.messages[0][0].lower()
        assert not store.list(user)
        assert len(client.calls) == 1
        view.stop()

    _gateway_scenario(monkeypatch, scenario)


def test_active_component_is_left_to_its_sdk_callback(monkeypatch):
    async def scenario(sdk, tree, worker, client, store):
        from discord_bridge.request_commands import RequestCommands, build_request_view

        handler = sdk._request_handler
        assert isinstance(handler, RequestCommands)
        row = store.add("1700000000000000005", "chair")
        view = build_request_view(handler, "1700000000000000005", [row])
        interaction = Interaction()
        interaction.type = SimpleNamespace(value=3)
        interaction.data = {"custom_id": view.children[0].custom_id, "component_type": 3}
        await sdk.on_interaction(interaction)
        assert interaction.messages == [] and not interaction.acknowledged
        view.stop()

    _gateway_scenario(monkeypatch, scenario)


def test_ack_failure_logs_safe_classification_and_never_changes_requests(tmp_path):
    commands, interaction, client, store = real_commands(tmp_path)
    events = []
    commands._events = lambda name, **fields: events.append({"event": name, **fields})

    class ExpiredInteraction(Exception):
        status, code = 404, 10062

    async def expired(**kwargs):
        raise ExpiredInteraction("PRIVATE interaction token and item")

    interaction.response.defer = expired
    invoke(commands.add, interaction, "chair")
    assert store.list(str(interaction.user.id)) == [] and client.calls == []
    failed = next(e for e in events if e.get("interaction_stage") == "failed")
    assert failed["provider_code"] == 10062 and failed["status"] == 404
    assert "PRIVATE" not in str(events)


def test_full_sdk_tree_dispatch_acknowledges_and_logs_without_input(monkeypatch):
    async def scenario(sdk, tree, worker, client, store):
        sdk.dispatch("ready")
        await asyncio.sleep(0)
        interaction = Interaction()
        interaction._state = sdk._connection
        interaction.guild = None
        interaction.command_failed = False
        interaction.data = {
            "name": "request",
            "type": 1,
            "guild_id": GUILD,
            "options": [{"name": "list", "type": 1}],
        }
        await tree._call(interaction)
        assert interaction.acknowledged
        assert "No requests" in interaction.messages[-1][0]

    events = _gateway_scenario(monkeypatch, scenario)
    stages = [
        fields["interaction_stage"] for name, fields in events if name == "request_interaction"
    ]
    assert "acknowledged" in stages and "replied" in stages
    assert CHANNEL not in str(events)


def test_overlapping_batches_get_clear_added_and_existing_feedback(tmp_path):
    commands, interaction, _, store = real_commands(tmp_path)
    store.set_dm_status(str(interaction.user.id), "ready")
    invoke(commands.add, interaction, "ice, ice maker, ice machine")
    invoke(commands.add, interaction, "ice maker, ice tray, ice cubes")
    text = interaction.messages[-1][0].lower()
    assert "2 new" in text and "1 already" in text
    assert len(store.list(str(interaction.user.id))) == 5
    invoke(commands.add, interaction, "ice maker, ice tray, ice cubes")
    assert "already" in interaction.messages[-1][0].lower()
    assert len(store.list(str(interaction.user.id))) == 5


def test_actual_sdk_view_timeout_then_dispatch_returns_refresh_guidance(monkeypatch):
    async def scenario(sdk, tree, worker, client, store):
        from discord_bridge.request_commands import build_request_view

        handler = sdk._request_handler
        user = "1700000000000000005"
        row = store.add(user, "chair")
        view = build_request_view(handler, user, [row])
        delete = next(child for child in view.children if getattr(child, "label", None) == "Delete")
        custom_id = delete.custom_id
        registry = sdk._connection._view_store
        registry.add_view(view, message_id=1234)
        view._dispatch_timeout()
        await asyncio.sleep(0)
        interaction = Interaction()
        interaction.message = SimpleNamespace(id=1234)
        interaction.type = SimpleNamespace(value=3)
        interaction.data = {"custom_id": custom_id, "component_type": 2}
        registry.dispatch_view(2, custom_id, interaction)
        await asyncio.sleep(0)
        assert not interaction.acknowledged  # The SDK has dropped its handler.
        await sdk.on_interaction(interaction)
        assert interaction.acknowledged and "expired" in interaction.messages[-1][0].lower()
        assert store.list(user) and client.calls == []

    _gateway_scenario(monkeypatch, scenario)


def test_expired_edit_modal_returns_guidance_without_applying_input(monkeypatch):
    async def scenario(sdk, tree, worker, client, store):
        from discord_bridge.request_commands import build_request_view

        handler = sdk._request_handler
        user = "1700000000000000005"
        row = store.add(user, "chair")
        view = build_request_view(handler, user, [row])
        edit = next(child for child in view.children if getattr(child, "label", None) == "Edit")
        opening = Interaction()
        await edit.callback(opening)
        modal = opening.opened_modal
        registry = sdk._connection._view_store
        registry.add_view(modal)
        modal._dispatch_timeout()
        await asyncio.sleep(0)
        interaction = Interaction()
        interaction.type = SimpleNamespace(value=5)
        interaction.data = {"custom_id": modal.custom_id}
        registry.dispatch_modal(modal.custom_id, interaction, [], {})
        await sdk.on_interaction(interaction)
        assert "expired" in interaction.messages[-1][0].lower()
        assert store.list(user)[0]["query"] == "chair" and client.calls == []
        view.stop()

    _gateway_scenario(monkeypatch, scenario)
