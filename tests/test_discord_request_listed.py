"""/request listed narrows a long backlog: one request, a minimum value, any page."""

import time

import pytest
from discord_bridge.core import JobStore
from discord_bridge.payload_limits import payload_problems

from test_discord_request_catchup import item
from test_discord_request_commands import USER as MEMBER
from test_discord_request_commands import (
    Interaction,
    invoke,
    make,
    real_commands,
)


def backlog(tmp_path, recliners=40, lamps=5):
    """Older matches for two requests; recliners worth $1..$40, lamps $100..$500."""
    commands, interaction, client, store = real_commands(tmp_path)
    store.set_dm_status(MEMBER, "ready")
    jobs = JobStore(store.path)
    now = time.time()
    products = [item(index, value=100 * (index + 1), title=f"Recliner model {index}")
                for index in range(recliners)]
    products += [item(500 + index, value=10000 * (index + 1), title=f"Desk lamp {index}")
                 for index in range(lamps)]
    products.append(item(900, title="Desk lamp no price"))
    jobs.record(products, now=now - 900)
    recliner = store.add(MEMBER, "recliner", now=now - 800)
    lamp = store.add(MEMBER, "desk lamp", now=now - 800)
    jobs.record(products, now=now - 700)
    return commands, interaction, client, store, recliner, lamp


def reply(commands, client, **options):
    interaction = Interaction()
    client.interaction = interaction
    invoke(commands.listed, interaction, **options)
    content, kwargs = interaction.messages[-1]
    embed = kwargs.get("embed")
    if embed is not None:
        assert payload_problems({"content": content, "embeds": [embed.to_dict()]}) == []
    assert kwargs["ephemeral"] is True
    return content, embed


def test_store_filters_by_request_and_minimum_value(tmp_path):
    _, _, _, store, recliner, lamp = backlog(tmp_path)
    everything = store.listed(MEMBER)
    assert len(everything) == 46
    values = [row["item"]["value_cents"] for row in everything]
    assert values[:2] == [50000, 40000] and values[-1] is None, "highest value first"
    lamps = store.listed(MEMBER, request_id=lamp["id"])
    assert len(lamps) == 6 and {tuple(row["queries"]) for row in lamps} == {("desk lamp",)}
    assert len(store.listed(MEMBER, request_id=recliner["id"], min_value=35)) == 6
    assert len(store.listed(MEMBER, min_value="200")) == 4, "no-value listings drop out"
    assert store.listed(MEMBER, min_value=0) == everything
    with pytest.raises(ValueError, match="whole cents"):
        store.listed(MEMBER, min_value=1.001)
    assert store.listed("444444444444444444", request_id=lamp["id"]) == []


def test_command_filters_by_one_request_with_autocomplete_ids(tmp_path):
    commands, interaction, client, _, recliner, lamp = backlog(tmp_path)
    client.interaction = None  # Autocomplete cannot defer or send a message.
    labels = invoke(commands.autocomplete, interaction, "lamp")
    assert labels == [("desk lamp", str(lamp["id"]))]
    content, embed = reply(commands, client, request=str(lamp["id"]))
    assert content.startswith("6 items matching `desk lamp` were already listed when you added it")
    assert "Page" not in content and "Narrow" not in content
    assert embed.description.count("Matched: desk lamp") == 6
    assert "Recliner" not in embed.description
    # Typing the exact request text works too.
    content, _ = reply(commands, client, request="  Desk   LAMP ")
    assert content.startswith("6 items matching `desk lamp`")


def test_command_minimum_value_and_combined_filters(tmp_path):
    commands, _, client, _, recliner, _ = backlog(tmp_path)
    content, embed = reply(commands, client, min_value=250)
    assert content.startswith("3 items matching your requests worth $250.00+ were")
    assert embed.description.index("$500.00") < embed.description.index("$300.00")
    content, embed = reply(commands, client, request=str(recliner["id"]), min_value=38.5)
    assert content.startswith("2 items matching `recliner` worth $38.50+ were")
    content, embed = reply(commands, client, request=str(recliner["id"]), min_value=5000)
    assert content.startswith("No already-listed matches for `recliner` worth $5,000.00+")
    assert embed is None


def test_long_lists_page_gracefully_and_suggest_filters(tmp_path):
    commands, _, client, *_ = backlog(tmp_path, recliners=60)
    content, embed = reply(commands, client)
    pages = int(content.split("Page 1 of ")[1].split(".")[0])
    assert pages > 1 and "Next: `/request listed page:2`." in content
    assert "Narrow it with `request` or `min_value`." in content
    assert embed.description.startswith("**1.** [Desk lamp 4]")
    content, embed = reply(commands, client, page=500)
    assert f"Page {pages} of {pages}. There is no page 500." in content
    assert "Next:" not in content and embed.description
    content, _ = reply(commands, client, min_value=1, page=2)
    assert "Next: `/request listed page:3` with the same filters." in content
    assert "Narrow" not in content


@pytest.mark.parametrize("request_value", ["999999", "sofa", ""])
def test_unknown_request_is_a_private_hint(tmp_path, request_value):
    commands, _, client, *_ = backlog(tmp_path, recliners=2, lamps=1)
    interaction = Interaction()
    client.interaction = interaction
    invoke(commands.listed, interaction, request=request_value)
    content, kwargs = interaction.messages[-1]
    assert kwargs["ephemeral"] is True
    if request_value:
        assert content.startswith("Request not found.") and "embed" not in kwargs
    else:
        assert content.startswith("4 items matching your requests")


def test_listed_filters_are_pro_only_and_never_read_other_members():
    commands, interaction, client, store = make(roles=())
    invoke(commands.listed, interaction, request="1", min_value=10)
    assert "Pro members only" in interaction.messages[-1][0]
    assert all("/members/" in path for _, path, _ in client.calls)


def test_listed_options_autocomplete_and_match_registration():
    from discord_bridge.request_commands import (
        build_request_command_group,
        request_command_definition,
    )

    commands, *_ = make()
    group = build_request_command_group(commands)
    listed = group.get_command("listed")
    assert listed.get_parameter("request").autocomplete
    assert not listed.get_parameter("request").required
    (definition,) = [option for option in request_command_definition()["options"]
                     if option["name"] == "listed"]
    assert [option["name"] for option in definition["options"]] == [
        "request", "min_value", "page"]
    assert definition["options"][2]["max_value"] == 1000
