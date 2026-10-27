"""Offline Discord provisioning and permission-boundary tests."""

from __future__ import annotations

import copy
import json

import pytest
from discord_bridge.api import DiscordError
from discord_bridge.layout import (
    BOT_RUNTIME_PERMISSIONS,
    CREATE_INVITE,
    PROVISION_PERMISSIONS,
    SETUP_PERMISSIONS,
    server_manifest,
)
from discord_bridge.provision import ProvisionError, audit, bind_membership, provision

GUILD = "100000000000000001"
BOT = "100000000000000002"
OWNER = "100000000000000003"
BOT_ROLE = "100000000000000004"
VIEW = 1 << 10
SEND = 1 << 11
HISTORY = 1 << 16
EMBED = 1 << 14
ADMIN = 1 << 3
MEMBERSHIP_BOT = "100000000000000010"
MEMBERSHIP_ROLE = "100000000000000011"
MANAGE_ROLES = 1 << 28


class FakeDiscord:
    """A stateful resource fake, with actual overwrite aggregation below."""

    def __init__(self):
        self.guild = {
            "id": GUILD,
            "owner_id": OWNER,
            "features": [],
            "verification_level": 0,
            "default_message_notifications": 0,
            "explicit_content_filter": 0,
            "widget_enabled": False,
        }
        self.roles = [
            {"id": GUILD, "name": "@everyone", "permissions": "0", "managed": False},
            {
                "id": BOT_ROLE,
                "name": "Alert bot",
                "permissions": str(PROVISION_PERMISSIONS),
                "managed": True,
            },
        ]
        self.channels = []
        self.widget = {"enabled": False, "channel_id": None}
        self.member = {"user": {"id": BOT, "bot": True}, "roles": [BOT_ROLE]}
        self.membership_member = None
        self.writes = []
        self.next_id = 200000000000000000
        self.fail_after_writes = None

    def request(self, method, path, payload=None):
        if method != "GET":
            if self.fail_after_writes == len(self.writes):
                raise RuntimeError("Simulated interrupted request before remote commit")
            self.writes.append((method, path, copy.deepcopy(payload)))
        if method == "GET":
            if path == f"/guilds/{GUILD}/members/{MEMBERSHIP_BOT}":
                if self.membership_member is None:
                    raise DiscordError("Discord returned HTTP 404", status=404)
                return copy.deepcopy(self.membership_member)
            values = {
                f"/guilds/{GUILD}": self.guild,
                f"/guilds/{GUILD}/roles": self.roles,
                f"/guilds/{GUILD}/channels": self.channels,
                f"/guilds/{GUILD}/members/{BOT}": self.member,
                f"/guilds/{GUILD}/widget": self.widget,
            }
            return copy.deepcopy(values[path])
        if method == "PATCH" and path == f"/guilds/{GUILD}":
            self.guild.update(payload)
            return copy.deepcopy(self.guild)
        if method == "PATCH" and path == f"/guilds/{GUILD}/widget":
            self.widget.update(payload)
            self.guild["widget_enabled"] = self.widget["enabled"]
            return copy.deepcopy(self.widget)
        if method == "PATCH" and "/roles/" in path:
            role = next(r for r in self.roles if r["id"] == path.rsplit("/", 1)[1])
            role.update(payload)
            return copy.deepcopy(role)
        if method == "POST":
            self.next_id += 1
            resource = {"id": str(self.next_id), **copy.deepcopy(payload)}
            if path.endswith("/roles"):
                resource["managed"] = False
                self.roles.append(resource)
            elif path.endswith("/channels"):
                resource["guild_id"] = GUILD
                self.channels.append(resource)
            else:
                raise AssertionError((method, path))
            return copy.deepcopy(resource)
        raise AssertionError((method, path, payload))

    def permissions(self, channel_id, role_ids=(), user_id="300000000000000001"):
        channel = next(c for c in self.channels if c["id"] == channel_id)
        if user_id == OWNER:
            return (1 << 60) - 1
        permissions = 0
        for role in self.roles:
            if role["id"] in {GUILD, *role_ids}:
                permissions |= int(role["permissions"])
        if permissions & ADMIN:
            return (1 << 60) - 1
        overwrites = channel["permission_overwrites"]
        everyone = next(o for o in overwrites if o["id"] == GUILD and o["type"] == 0)
        permissions = (permissions & ~int(everyone["deny"])) | int(everyone["allow"])
        allow = deny = 0
        for overwrite in overwrites:
            if overwrite["type"] == 0 and overwrite["id"] in role_ids:
                allow |= int(overwrite["allow"])
                deny |= int(overwrite["deny"])
        permissions = (permissions & ~deny) | allow
        for overwrite in overwrites:
            if overwrite["type"] == 1 and overwrite["id"] == user_id:
                permissions = (permissions & ~int(overwrite["deny"])) | int(overwrite["allow"])
        return permissions


def apply_server(tmp_path):
    client = FakeDiscord()
    path = tmp_path / "state.json"
    result = provision(client, GUILD, BOT, path, apply=True)
    finish_setup(client, result)
    return client, path, result["state"]


def finish_setup(client, result):
    assert result["audit"]["ok"] is False
    assert all("Bot role" in finding for finding in result["audit"]["findings"])
    client.roles[1]["permissions"] = "0"
    assert audit(client, GUILD, result["state"])["ok"]


def test_dry_run_plans_private_layout_without_any_writes(tmp_path):
    client = FakeDiscord()
    path = tmp_path / "state.json"
    result = provision(client, GUILD, BOT, path)
    assert result["applied"] is False
    assert len(result["actions"]) > 20
    assert client.writes == []
    assert not path.exists()


def test_onboarding_separates_pricing_greetings_and_public_instructions(tmp_path):
    client, _, state = apply_server(tmp_path)
    manifest = {channel["key"]: channel for channel in server_manifest()["channels"]}
    for key in ("welcome", "greeting", "how-it-works"):
        assert manifest[key]["category"] == "public-info"
        assert manifest[key]["audience"] == "everyone"
        assert manifest[key]["bot_only_posting"] is True
        for roles in ((), (state["roles"]["starter"],), (state["roles"]["pro"],)):
            access = client.permissions(state["channels"][key], roles)
            assert access & VIEW
            assert not access & SEND
            assert not access & EMBED
    assert manifest["how-it-works"]["name"] == "how-to-use"


def test_apply_is_idempotent_and_persists_only_ids(tmp_path):
    client, path, state = apply_server(tmp_path)
    first_count = len(client.writes)
    client.roles[1]["permissions"] = str(PROVISION_PERMISSIONS)
    result = provision(client, GUILD, BOT, path, apply=True)
    finish_setup(client, result)
    assert result["state"] == state == json.loads(path.read_text())
    assert len(client.writes) == first_count
    assert state["schema_version"] == 1
    assert state["roles"].keys() == {"starter", "pro"}
    assert state["channels"]["price-200-plus"]
    assert "token" not in path.read_text().lower()
    assert client.guild["verification_level"] >= 2
    assert client.guild["default_message_notifications"] == 1
    assert client.guild["explicit_content_filter"] == 2
    assert client.widget == {"enabled": False, "channel_id": None}


def test_interruption_resumes_resources_saved_after_each_creation(tmp_path):
    client = FakeDiscord()
    path = tmp_path / "state.json"
    client.fail_after_writes = 7
    with pytest.raises(RuntimeError, match="interrupted"):
        provision(client, GUILD, BOT, path, apply=True)
    partial = json.loads(path.read_text())
    assert partial["roles"]
    assert partial["categories"]
    ids_before = {c["id"] for c in client.channels}
    client.fail_after_writes = None
    result = provision(client, GUILD, BOT, path, apply=True)
    finish_setup(client, result)
    assert ids_before <= {c["id"] for c in client.channels}
    assert len({c["name"] for c in client.channels}) == len(client.channels)


def test_real_permission_aggregation_keeps_free_starter_and_interest_roles_private(tmp_path):
    client, _, state = apply_server(tmp_path)
    interest = "300000000000000002"
    client.roles.append({"id": interest, "name": "Furniture interest", "permissions": "0"})
    starter, pro = state["roles"]["starter"], state["roles"]["pro"]
    for memberships, starter_access, pro_access in [
        ([], False, False),
        ([interest], False, False),
        ([starter], True, False),
        ([starter, interest], True, False),
        ([pro], True, True),
        ([starter, pro, interest], True, True),
    ]:
        assert (
            bool(client.permissions(state["channels"]["starter-items"], memberships) & VIEW)
            is starter_access
        )
        assert (
            bool(client.permissions(state["channels"]["pro-all"], memberships) & VIEW) is pro_access
        )
        assert not client.permissions(state["channels"]["collector-health"], memberships) & VIEW
        assert not client.permissions(state["channels"]["pro-all"], memberships) & SEND
        assert client.permissions(state["channels"]["welcome"], memberships) & VIEW
        assert client.permissions(state["channels"]["general"], memberships) & SEND
    bot_permissions = client.permissions(state["channels"]["collector-health"], [BOT_ROLE], BOT)
    assert bot_permissions & (VIEW | SEND | EMBED | HISTORY) == VIEW | SEND | EMBED | HISTORY
    assert audit(client, GUILD, state)["ok"]


def test_only_two_community_channels_allow_text_but_no_media_threads_apps_or_privileges(tmp_path):
    from discord_bridge.layout import (
        ADD_REACTIONS,
        ATTACH_FILES,
        BYPASS_SLOWMODE,
        CREATE_INVITE,
        CREATE_PRIVATE_THREADS,
        CREATE_PUBLIC_THREADS,
        MANAGE_CHANNELS,
        MANAGE_GUILD,
        MANAGE_MESSAGES,
        MANAGE_THREADS,
        MANAGE_WEBHOOKS,
        MENTION_EVERYONE,
        PIN_MESSAGES,
        SEND_MESSAGES_IN_THREADS,
        SEND_POLLS,
        SEND_TTS_MESSAGES,
        SEND_VOICE_MESSAGES,
        USE_APPLICATION_COMMANDS,
        USE_EXTERNAL_APPS,
        server_manifest,
    )

    client, _, state = apply_server(tmp_path)
    starter, pro = state["roles"]["starter"], state["roles"]["pro"]
    prohibited = (
        EMBED
        | ATTACH_FILES
        | CREATE_PUBLIC_THREADS
        | CREATE_PRIVATE_THREADS
        | SEND_MESSAGES_IN_THREADS
        | SEND_VOICE_MESSAGES
        | SEND_POLLS
        | USE_APPLICATION_COMMANDS
        | USE_EXTERNAL_APPS
        | ADD_REACTIONS
        | SEND_TTS_MESSAGES
        | MENTION_EVERYONE
        | BYPASS_SLOWMODE
        | CREATE_INVITE
        | MANAGE_CHANNELS
        | MANAGE_GUILD
        | MANAGE_ROLES
        | MANAGE_MESSAGES
        | MANAGE_THREADS
        | MANAGE_WEBHOOKS
        | PIN_MESSAGES
    )
    chat = {
        state["categories"]["community"],
        *(state["channels"][key] for key in ("general", "feature-requests")),
    }
    for memberships in ([], [starter], [pro], [starter, pro]):
        for channel_id in (*state["categories"].values(), *state["channels"].values()):
            permissions = client.permissions(channel_id, memberships)
            assert not permissions & prohibited
            assert bool(permissions & SEND) is (channel_id in chat)
    for channel_id in state["channels"].values():
        bot = client.permissions(channel_id, [BOT_ROLE], BOT)
        assert bot & (VIEW | SEND | EMBED | HISTORY) == VIEW | SEND | EMBED | HISTORY
        assert not bot & ATTACH_FILES  # Product thumbnails use embeds, not uploads.
    manifest = server_manifest()
    assert {entry["key"] for entry in manifest["channels"] if not entry["bot_only_posting"]} == {
        "general",
        "feature-requests",
    }
    assert {entry["key"] for entry in manifest["categories"] if not entry["bot_only_posting"]} == {
        "community"
    }
    for key in ("general", "feature-requests"):
        channel = next(c for c in client.channels if c["id"] == state["channels"][key])
        assert channel["rate_limit_per_user"] == 10


@pytest.mark.parametrize("key", ["general", "feature-requests"])
@pytest.mark.parametrize("slowmode", [None, 0, 9, False, "10"])
def test_audit_refuses_missing_or_disabled_community_slowmode(tmp_path, key, slowmode):
    client, _, state = apply_server(tmp_path)
    channel = next(c for c in client.channels if c["id"] == state["channels"][key])
    if slowmode is None:
        channel.pop("rate_limit_per_user")
    else:
        channel["rate_limit_per_user"] = slowmode
    result = audit(client, GUILD, state)
    assert not result["ok"]
    assert any("slowmode" in finding.lower() for finding in result["findings"])


def test_audit_does_not_claim_to_verify_automod_without_manage_guild(tmp_path):
    client, _, state = apply_server(tmp_path)
    result = audit(client, GUILD, state)
    assert result["ok"]
    assert any("AutoMod" in item and "owner" in item for item in result["limitations"])


def test_audit_accepts_stronger_community_slowmode(tmp_path):
    client, _, state = apply_server(tmp_path)
    for key in ("general", "feature-requests"):
        channel = next(c for c in client.channels if c["id"] == state["channels"][key])
        channel["rate_limit_per_user"] = 30
    assert audit(client, GUILD, state)["ok"]


def test_audit_accepts_current_tier_category_headings_without_price_text(tmp_path):
    client, _, state = apply_server(tmp_path)
    current_names = {"starter": "STARTER - 5 MIN DELAY", "pro-price": "PRO - BY VALUE"}
    for key, name in current_names.items():
        channel = next(c for c in client.channels if c["id"] == state["categories"][key])
        channel["name"] = name
    writes_before = copy.deepcopy(client.writes)
    result = audit(client, GUILD, state)
    assert result["ok"], result["findings"]
    assert client.writes == writes_before, "Auditing existing names must not modify Discord"


@pytest.mark.parametrize("change", ["type", "parent", "permissions", "missing"])
def test_runtime_category_renames_preserve_all_resource_security_checks(tmp_path, change):
    client, _, state = apply_server(tmp_path)
    category = next(c for c in client.channels if c["id"] == state["categories"]["pro-price"])
    category["name"] = "Owner's custom Pro value heading"
    before = copy.deepcopy(client.writes)
    assert audit(client, GUILD, state)["ok"], "A heading rename does not change category access"
    if change == "type":
        category["type"] = 0
    elif change == "parent":
        category["parent_id"] = state["categories"]["starter"]
    elif change == "permissions":
        category["permission_overwrites"].append(
            {"id": "300000000000000004", "type": 0, "allow": str(VIEW), "deny": "0"}
        )
    else:
        client.channels.remove(category)
    assert not audit(client, GUILD, state)["ok"], change
    assert client.writes == before, "Runtime auditing must remain read-only"


@pytest.mark.parametrize("kind", [0, 1])
def test_audit_refuses_unsynced_extra_chat_member_or_role_allows(tmp_path, kind):
    client, _, state = apply_server(tmp_path)
    channel = next(c for c in client.channels if c["id"] == state["channels"]["feature-requests"])
    channel["permission_overwrites"].append(
        {"id": "300000000000000004", "type": kind, "allow": str(SEND), "deny": "0"}
    )
    assert not audit(client, GUILD, state)["ok"]


@pytest.mark.parametrize("boundary", ["provisioning", "channel-name"])
def test_category_heading_exception_is_limited_to_runtime_categories(tmp_path, boundary):
    client, path, state = apply_server(tmp_path)
    category = next(c for c in client.channels if c["id"] == state["categories"]["pro-price"])
    category["name"] = "Owner's custom heading"
    before = copy.deepcopy(client.writes)
    state_before = path.read_bytes()
    if boundary == "provisioning":
        with pytest.raises(ProvisionError, match="unexpected name or channel type"):
            provision(client, GUILD, BOT, path, apply=True)
    else:
        channel = next(c for c in client.channels if c["id"] == state["channels"]["pro-all"])
        channel["name"] = "unexpected-channel-name"
        assert not audit(client, GUILD, state)["ok"]
    assert client.writes == before
    assert path.read_bytes() == state_before


@pytest.mark.parametrize("channel_key", ["welcome", "pro-all"])
@pytest.mark.parametrize("kind", [0, 1])
@pytest.mark.parametrize("numeric", [False, True])
def test_neutral_member_or_role_overwrite_preserves_access_and_passes_audit(
    tmp_path, channel_key, kind, numeric
):
    client, _, state = apply_server(tmp_path)
    neutral_id = "300000000000000004"
    if kind == 0:
        client.roles.append(
            {"id": neutral_id, "name": "Neutral role", "permissions": "0", "managed": False}
        )
    channel_id = state["channels"][channel_key]
    channel = next(c for c in client.channels if c["id"] == channel_id)
    audiences = ([], [state["roles"]["starter"]], [state["roles"]["pro"]], [neutral_id])
    before = [client.permissions(channel_id, roles, neutral_id) for roles in audiences]
    channel["permission_overwrites"].append(
        {
            "id": neutral_id,
            "type": kind,
            "allow": 0 if numeric else "0",
            "deny": 0 if numeric else "0",
        }
    )
    assert [client.permissions(channel_id, roles, neutral_id) for roles in audiences] == before
    assert audit(client, GUILD, state)["ok"]


@pytest.mark.parametrize("field", ["allow", "deny"])
def test_neutral_overwrite_does_not_hide_a_nonzero_permission_change(tmp_path, field):
    client, _, state = apply_server(tmp_path)
    channel = next(c for c in client.channels if c["id"] == state["channels"]["welcome"])
    extra = {"id": "300000000000000004", "type": 1, "allow": "0", "deny": "0"}
    channel["permission_overwrites"].append(extra)
    extra[field] = str(SEND)
    assert not audit(client, GUILD, state)["ok"]


@pytest.mark.parametrize("problem", ["duplicate", "id", "type"])
def test_neutral_overwrite_still_requires_a_valid_unique_target(tmp_path, problem):
    client, _, state = apply_server(tmp_path)
    channel = next(c for c in client.channels if c["id"] == state["channels"]["welcome"])
    extra = {"id": "300000000000000004", "type": 1, "allow": "0", "deny": "0"}
    if problem == "duplicate":
        extra["id"] = BOT
    elif problem == "id":
        extra["id"] = "not-a-snowflake"
    else:
        extra["type"] = 2
    channel["permission_overwrites"].append(extra)
    assert not audit(client, GUILD, state)["ok"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allow", 0.5),
        ("allow", False),
        ("allow", "\u0660"),
        ("allow", "+0"),
        ("allow", ""),
        ("deny", None),
        ("type", False),
        ("type", True),
    ],
)
def test_malformed_neutral_overwrite_is_not_treated_as_inheritance(tmp_path, field, value):
    client, _, state = apply_server(tmp_path)
    channel = next(c for c in client.channels if c["id"] == state["channels"]["welcome"])
    extra = {"id": "300000000000000004", "type": 1, "allow": "0", "deny": "0"}
    extra[field] = value
    channel["permission_overwrites"].append(extra)
    assert not audit(client, GUILD, state)["ok"]


@pytest.mark.parametrize("missing", ["allow", "deny"])
def test_neutral_overwrite_requires_both_explicit_bitfields(tmp_path, missing):
    client, _, state = apply_server(tmp_path)
    channel = next(c for c in client.channels if c["id"] == state["channels"]["welcome"])
    extra = {"id": "300000000000000004", "type": 1, "allow": "0", "deny": "0"}
    del extra[missing]
    channel["permission_overwrites"].append(extra)
    assert not audit(client, GUILD, state)["ok"]


def test_neutralizing_a_required_overwrite_still_fails_audit(tmp_path):
    client, _, state = apply_server(tmp_path)
    channel = next(c for c in client.channels if c["id"] == state["channels"]["pro-all"])
    everyone = next(o for o in channel["permission_overwrites"] if o["id"] == GUILD)
    everyone.update(allow="0", deny="0")
    assert not audit(client, GUILD, state)["ok"]


@pytest.mark.parametrize("target", ["everyone", "starter", "pro", "interest"])
def test_audit_refuses_slowmode_bypass_on_any_member_role(tmp_path, target):
    from discord_bridge.layout import BYPASS_SLOWMODE

    client, _, state = apply_server(tmp_path)
    if target == "interest":
        client.roles.append(
            {"id": "300000000000000002", "name": "Furniture", "permissions": str(BYPASS_SLOWMODE)}
        )
    else:
        role_id = GUILD if target == "everyone" else state["roles"][target]
        next(role for role in client.roles if role["id"] == role_id)["permissions"] = str(
            BYPASS_SLOWMODE
        )
    assert not audit(client, GUILD, state)["ok"]


def test_audit_refuses_mentionable_interest_role_even_with_zero_permissions(tmp_path):
    client, _, state = apply_server(tmp_path)
    client.roles.append(
        {
            "id": "300000000000000002",
            "name": "Furniture interest",
            "permissions": "0",
            "mentionable": True,
        }
    )
    result = audit(client, GUILD, state)
    assert not result["ok"]
    assert any("mentionable" in finding for finding in result["findings"])


def test_audit_refuses_member_posting_or_upload_exceptions_in_community(tmp_path):
    client, _, state = apply_server(tmp_path)
    channel = next(c for c in client.channels if c["id"] == state["channels"]["general"])
    channel["permission_overwrites"][0]["allow"] = str(VIEW | HISTORY | SEND | (1 << 15))
    assert not audit(client, GUILD, state)["ok"]


def test_simple_welcome_area_and_read_only_updates_do_not_expose_operator_guides(tmp_path):
    from discord_bridge.layout import server_manifest

    manifest = server_manifest()
    public_info = [entry for entry in manifest["channels"] if entry["category"] == "public-info"]
    assert [entry["name"] for entry in public_info] == [
        "💳-welcome",
        "greeting",
        "how-to-use",
        "updates",
        "🆓-free-24h-alerts",
    ]
    client, _, state = apply_server(tmp_path)
    for memberships in ([], [state["roles"]["starter"]], [state["roles"]["pro"]]):
        updates = client.permissions(state["channels"]["feed-status"], memberships)
        assert updates & VIEW and not updates & SEND
        for key in ("plans-and-help", "audit", "collector-health"):
            assert not client.permissions(state["channels"][key], memberships) & VIEW
    assert audit(client, GUILD, state)["ok"]


@pytest.mark.parametrize(
    "tamper",
    ["role_allow", "member_allow", "admin", "paid_privilege", "unsynced", "everyone_invite"],
)
def test_audit_detects_permission_bypasses_and_unsynced_channels(tmp_path, tamper):
    client, _, state = apply_server(tmp_path)
    channel = next(c for c in client.channels if c["id"] == state["channels"]["pro-all"])
    if tamper in {"role_allow", "member_allow"}:
        channel["permission_overwrites"].append(
            {
                "id": "300000000000000004",
                "type": 0 if tamper == "role_allow" else 1,
                "allow": str(VIEW),
                "deny": "0",
            }
        )
    elif tamper == "admin":
        client.roles.append(
            {"id": "300000000000000004", "name": "Unsafe", "permissions": str(ADMIN)}
        )
    elif tamper == "paid_privilege":
        next(r for r in client.roles if r["id"] == state["roles"]["starter"])["permissions"] = str(
            1 << 28
        )
    elif tamper == "unsynced":
        channel["permission_overwrites"][0]["deny"] = "0"
    elif tamper == "everyone_invite":
        client.roles[0]["permissions"] = "1"
    result = audit(client, GUILD, state)
    assert not result["ok"]
    assert result["findings"]


def test_same_name_channel_never_adopted_and_nonempty_server_refused(tmp_path):
    client = FakeDiscord()
    client.channels.append({"id": "400000000000000001", "name": "welcome", "type": 0})
    with pytest.raises(ProvisionError):
        provision(client, GUILD, BOT, tmp_path / "state.json", apply=True)
    assert client.writes == []


def test_mismatched_guild_state_rejected_before_writes(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "guild_id": "999999999999999999",
                "roles": {},
                "categories": {},
                "channels": {},
            }
        )
    )
    client = FakeDiscord()
    with pytest.raises(ProvisionError, match="guild"):
        provision(client, GUILD, BOT, path, apply=True)
    assert client.writes == []


def test_discoverable_server_requires_owner_change_without_administrator(tmp_path):
    client = FakeDiscord()
    client.guild["features"] = ["DISCOVERABLE"]
    with pytest.raises(ProvisionError, match="Discovery|discover"):
        provision(client, GUILD, BOT, tmp_path / "state.json", apply=True)
    assert client.writes == []


def test_manifest_defines_prices_and_routes_without_credentials():
    manifest = server_manifest()
    assert manifest["plans"]["starter"] == {
        "role": "Starter",
        "monthly_price_usd": 5,
        "monthly_price_cents": 500,
        "delay_seconds": 300,
        "max_price_cents_exclusive": 4000,
    }
    assert manifest["plans"]["pro"]["monthly_price_usd"] == 20
    assert manifest["plans"]["pro"]["delay_seconds"] == 0
    assert manifest["security"]["billing_enabled"] is False
    routes = {channel["key"]: channel for channel in manifest["channels"]}
    assert routes["pro-all"]["audience"] == "pro"
    assert routes["starter-items"]["bot_only_posting"] is True
    assert routes["collector-health"]["audience"] == "operations"
    assert "token" not in json.dumps(manifest).lower()


def test_temporary_setup_privileges_must_be_removed_before_delivery(tmp_path):
    client = FakeDiscord()
    client.roles[1]["permissions"] = str(PROVISION_PERMISSIONS)
    path = tmp_path / "state.json"
    result = provision(client, GUILD, BOT, path, apply=True)
    assert not result["audit"]["ok"]
    assert any("Bot role" in finding for finding in result["audit"]["findings"])
    client.roles[1]["permissions"] = "0"
    assert audit(client, GUILD, result["state"])["ok"]


def test_every_new_resource_is_private_at_creation_and_alerts_cannot_be_posted_by_members(tmp_path):
    client, _, state = apply_server(tmp_path)
    public_categories = {state["categories"]["public-info"], state["categories"]["community"]}
    for method, path, payload in client.writes:
        if method != "POST" or not path.endswith("/channels"):
            continue
        everyone = next(o for o in payload["permission_overwrites"] if o["id"] == GUILD)
        is_public = (
            payload.get("name") in {"START HERE", "COMMUNITY"}
            or payload.get("parent_id") in public_categories
        )
        if not is_public:
            assert int(everyone["deny"]) & VIEW
            assert not int(everyone["allow"]) & VIEW
        if payload.get("parent_id") != state["categories"]["community"]:
            assert int(everyone["deny"]) & SEND or payload.get("name") == "COMMUNITY"


def test_security_settings_never_lower_stronger_verification(tmp_path):
    client = FakeDiscord()
    client.guild["verification_level"] = 4
    client.widget = {"enabled": True, "channel_id": "400000000000000001"}
    result = provision(client, GUILD, BOT, tmp_path / "state.json", apply=True)
    finish_setup(client, result)
    assert client.guild["verification_level"] == 4
    assert client.widget == {"enabled": False, "channel_id": None}


def test_channel_creation_obeys_discord_nonadministrator_overwrite_restrictions(tmp_path):
    class DiscordOverwriteRules(FakeDiscord):
        def request(self, method, path, payload=None):
            if method == "POST" and path.endswith("/channels"):
                own = 0
                for role in self.roles:
                    if role["id"] in {GUILD, *self.member["roles"]}:
                        own |= int(role["permissions"])
                for overwrite in payload["permission_overwrites"]:
                    bits = int(overwrite["allow"]) | int(overwrite["deny"])
                    if bits & ~own or bits & MANAGE_ROLES:
                        raise DiscordError("Discord returned HTTP 403", status=403)
            return super().request(method, path, payload)

    client = DiscordOverwriteRules()
    client.roles[1]["permissions"] = str(SETUP_PERMISSIONS | BOT_RUNTIME_PERMISSIONS)
    result = provision(client, GUILD, BOT, tmp_path / "state.json", apply=True)
    assert len(result["state"]["channels"]) == 31
    assert len(result["state"]["categories"]) == 7
    client.roles[1]["permissions"] = "0"
    assert audit(client, GUILD, result["state"])["ok"]


@pytest.mark.parametrize("borrow_everyone", [False, True])
def test_missing_bot_runtime_grants_refused_before_any_mutation(tmp_path, borrow_everyone):
    client = FakeDiscord()
    client.roles[1]["permissions"] = str((1 << 4) | (1 << 5) | MANAGE_ROLES)
    if borrow_everyone:
        client.roles[0]["permissions"] = str(BOT_RUNTIME_PERMISSIONS)
    path = tmp_path / "state.json"
    with pytest.raises(ProvisionError, match="View Channels"):
        provision(client, GUILD, BOT, path, apply=True)
    assert client.writes == []
    assert not path.exists()


def test_definitive_rate_limit_during_creation_resumes_without_duplicates(tmp_path):
    class RateLimitedDiscord(FakeDiscord):
        limited = False

        def request(self, method, path, payload=None):
            if (
                method == "POST"
                and path.endswith("/channels")
                and len(self.channels) == 3
                and not self.limited
            ):
                self.limited = True
                raise DiscordError("Discord rate limit", status=429, retry_after=0.1)
            return super().request(method, path, payload)

    client = RateLimitedDiscord()
    path = tmp_path / "state.json"
    with pytest.raises(DiscordError) as caught:
        provision(client, GUILD, BOT, path, apply=True)
    assert caught.value.status == 429
    partial = json.loads(path.read_text())
    preserved = set(partial["categories"].values())
    assert preserved == {channel["id"] for channel in client.channels}
    result = provision(client, GUILD, BOT, path, apply=True)
    finish_setup(client, result)
    assert preserved <= set(result["state"]["categories"].values())
    assert len(client.channels) == 38
    assert len({channel["id"] for channel in client.channels}) == 38
    assert len({channel["name"] for channel in client.channels}) == 38


def install_membership_bot(client, state):
    state["membership_bot_user_id"] = MEMBERSHIP_BOT
    client.membership_member = {
        "user": {"id": MEMBERSHIP_BOT, "bot": True},
        "roles": [MEMBERSHIP_ROLE],
    }
    for role in client.roles:
        role["position"] = 1
    role = {
        "id": MEMBERSHIP_ROLE,
        "name": "Membership integration",
        "permissions": str(MANAGE_ROLES),
        "managed": True,
        "tags": {"bot_id": MEMBERSHIP_BOT},
        "position": 2,
    }
    client.roles.append(role)
    return role


@pytest.mark.parametrize("permissions", [MANAGE_ROLES, MANAGE_ROLES | CREATE_INVITE | VIEW])
def test_bound_real_membership_bot_can_sync_roles_without_direct_private_feed_access(
    tmp_path, permissions
):
    client, path, state = apply_server(tmp_path)
    role = install_membership_bot(client, state)
    role["permissions"] = str(permissions)
    result = audit(client, GUILD, state)
    assert result["ok"], result
    assert any("trusted access manager" in item for item in result["limitations"])
    for key in ("starter-items", "pro-all", "price-200-plus", "collector-health", "audit"):
        assert (
            not client.permissions(state["channels"][key], [MEMBERSHIP_ROLE], MEMBERSHIP_BOT) & VIEW
        )
    # Role management delegates access control: the trusted integration could
    # assign itself Pro, so a lack of direct ACL access is not isolation.
    assert (
        client.permissions(
            state["channels"]["pro-all"], [MEMBERSHIP_ROLE, state["roles"]["pro"]], MEMBERSHIP_BOT
        )
        & VIEW
    )
    # Binding survives an idempotent setup pass and cannot silently disappear.
    path.write_text(json.dumps(state))
    client.roles[1]["permissions"] = str(PROVISION_PERMISSIONS)
    applied = provision(client, GUILD, BOT, path, apply=True)
    finish_setup(client, applied)
    assert applied["state"]["membership_bot_user_id"] == MEMBERSHIP_BOT
    assert json.loads(path.read_text())["membership_bot_user_id"] == MEMBERSHIP_BOT
    assert audit(client, GUILD, applied["state"])["ok"]


@pytest.mark.parametrize("target", ["collector", "unbound_role"])
def test_invite_permission_remains_forbidden_outside_bound_membership_bot(tmp_path, target):
    client, _, state = apply_server(tmp_path)
    if target == "collector":
        role = next(role for role in client.roles if role["id"] == BOT_ROLE)
        role["permissions"] = str(int(role["permissions"]) | CREATE_INVITE)
    else:
        client.roles.append(
            {"id": MEMBERSHIP_ROLE, "name": "Whop Bot", "permissions": str(CREATE_INVITE)}
        )
    result = audit(client, GUILD, state)
    assert not result["ok"]
    assert result["findings"]


@pytest.mark.parametrize(
    "tamper",
    [
        "unbound",
        "nonmanaged",
        "no_tags",
        "wrong_tag",
        "name_spoof",
        "not_member",
        "human_member",
        "member_id_mismatch",
        "role_not_assigned",
        "below_paid",
        "equal_paid",
        "duplicate_tag",
        "administrator",
        "manage_guild",
        "manage_channels",
        "kick",
        "ban",
        "posting",
        "no_manage_roles",
    ],
)
def test_membership_bot_allowlist_refuses_spoofing_and_extra_privileges(tmp_path, tamper):
    client, _, state = apply_server(tmp_path)
    role = install_membership_bot(client, state)
    if tamper == "unbound":
        del state["membership_bot_user_id"]
    elif tamper == "nonmanaged":
        role["managed"] = False
    elif tamper == "no_tags":
        del role["tags"]
    elif tamper == "wrong_tag":
        role["tags"]["bot_id"] = BOT
    elif tamper == "name_spoof":
        role["name"] = "Whop"
        role["managed"] = False
        role["tags"] = {}
    elif tamper == "not_member":
        client.membership_member = None
    elif tamper == "human_member":
        client.membership_member["user"]["bot"] = False
    elif tamper == "member_id_mismatch":
        client.membership_member["user"]["id"] = BOT
    elif tamper == "role_not_assigned":
        client.membership_member["roles"] = []
    elif tamper == "below_paid":
        role["position"] = 0
    elif tamper == "equal_paid":
        role["position"] = 1
    elif tamper == "duplicate_tag":
        client.roles.append({**role, "id": "100000000000000012"})
    elif tamper == "no_manage_roles":
        role["permissions"] = "0"
    else:
        bit = {
            "administrator": ADMIN,
            "manage_guild": 1 << 5,
            "manage_channels": 1 << 4,
            "kick": 1 << 1,
            "ban": 1 << 2,
            "posting": SEND,
        }[tamper]
        role["permissions"] = str(MANAGE_ROLES | bit)
    result = audit(client, GUILD, state)
    assert not result["ok"], tamper
    assert result["findings"]


@pytest.mark.parametrize("bot_id", [GUILD, BOT, "１２３４", "abc", None])
def test_membership_binding_requires_distinct_ascii_numeric_id(tmp_path, bot_id):
    client, _, state = apply_server(tmp_path)
    state["membership_bot_user_id"] = bot_id
    assert not audit(client, GUILD, state)["ok"]


def test_bind_membership_saves_verified_id_atomically_without_discord_writes(tmp_path):
    client, path, state = apply_server(tmp_path)
    install_membership_bot(client, state)
    writes_before = list(client.writes)
    result = bind_membership(client, GUILD, MEMBERSHIP_BOT, path)
    assert result["ok"] and result["bound"] and result["assessment"]["ok"]
    saved = json.loads(path.read_text())
    assert saved["membership_bot_user_id"] == MEMBERSHIP_BOT
    assert saved["bot_user_id"] == BOT
    assert client.writes == writes_before


@pytest.mark.parametrize("failure", ["unsafe_role", "invalid_id", "wrong_guild", "missing_bot"])
def test_bind_membership_failure_preserves_exact_existing_state(tmp_path, failure):
    client, path, state = apply_server(tmp_path)
    role = install_membership_bot(client, state)
    # Preserve even nonstandard whitespace and unknown harmless metadata on
    # failed verification; no rewrite is allowed before the assessment passes.
    original = path.read_bytes() + b"\n\n"
    path.write_bytes(original)
    writes_before = list(client.writes)
    guild_id, bot_id = GUILD, MEMBERSHIP_BOT
    if failure == "unsafe_role":
        role["permissions"] = str(MANAGE_ROLES | ADMIN)
    elif failure == "invalid_id":
        bot_id = BOT
    elif failure == "wrong_guild":
        guild_id = "999999999999999999"
    elif failure == "missing_bot":
        client.membership_member = None
    result = bind_membership(client, guild_id, bot_id, path)
    assert result["ok"] is False and result["bound"] is False
    assert result["assessment"]["ok"] is False
    assert path.read_bytes() == original
    assert client.writes == writes_before
