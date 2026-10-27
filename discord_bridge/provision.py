"""Reviewable, resumable Discord setup; no remote mutations without apply=True.

Only an empty server or resources already recorded in the state file are
accepted. Same-name resources are never adopted. Creation and local state
cannot be one transaction: an unknown remote result deliberately requires an
owner to reconcile the orphan rather than guessing who owns it.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .api import DiscordError
from .layout import (
    ADMINISTRATOR,
    BOT_RUNTIME_PERMISSIONS,
    CATEGORIES,
    CHANNELS,
    CREATE_INVITE,
    MANAGE_ROLES,
    OPTIONAL_CHANNELS,
    PROVISION_PERMISSIONS,
    READER_PERMISSIONS,
    ROLE_NAMES,
    SETUP_PERMISSIONS,
    VIEW_CHANNEL,
    permission_overwrites,
)

if TYPE_CHECKING:
    from .api import DiscordClient


class ProvisionError(RuntimeError):
    """A safe setup refusal requiring review rather than resource adoption."""


def _snowflake(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdecimal()
        or not 0 < int(value) < 1 << 64
    ):
        raise ProvisionError(f"Invalid {label}; expected a Discord numeric ID.")
    return value


def _new_state(guild_id: str, bot_user_id: str) -> dict:
    return {
        "schema_version": 1,
        "guild_id": guild_id,
        "bot_user_id": bot_user_id,
        "roles": {},
        "categories": {},
        "channels": {},
    }


def _validate_state(state: dict, guild_id: str, bot_user_id: str | None = None) -> dict:
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ProvisionError("Unsupported or invalid provisioning state schema.")
    if state.get("guild_id") != guild_id:
        raise ProvisionError("State belongs to a different guild; no changes were made.")
    actual_bot = _snowflake(state.get("bot_user_id"), "state bot user ID")
    if bot_user_id is not None and actual_bot != bot_user_id:
        raise ProvisionError("State belongs to a different bot user; no changes were made.")
    allowed_keys = {
        "roles": set(ROLE_NAMES),
        "categories": {c.key for c in CATEGORIES},
        "channels": {c.key for c in (*CHANNELS, *OPTIONAL_CHANNELS)},
    }
    seen = {guild_id, actual_bot}
    membership_bot = None
    if "membership_bot_user_id" in state:
        membership_bot = _snowflake(state["membership_bot_user_id"], "state membership bot user ID")
        if membership_bot in seen:
            raise ProvisionError("Membership bot must differ from the guild and collector bot.")
        seen.add(membership_bot)
    for field, known_keys in allowed_keys.items():
        mapping = state.get(field)
        if not isinstance(mapping, dict) or set(mapping) - known_keys:
            raise ProvisionError(f"Invalid or unknown {field} in provisioning state.")
        for value in mapping.values():
            resource_id = _snowflake(value, f"{field} ID")
            if resource_id in seen:
                raise ProvisionError("Duplicate resource ID in provisioning state.")
            seen.add(resource_id)
    # Normalize to IDs only, dropping unrelated metadata from caller input.
    clean = _new_state(guild_id, actual_bot)
    if membership_bot is not None:
        clean["membership_bot_user_id"] = membership_bot
    for field in allowed_keys:
        clean[field] = dict(state[field])
    if clean["channels"] and not clean["categories"]:
        raise ProvisionError("Channel state is missing its parent categories.")
    if clean["categories"] and set(clean["roles"]) != set(ROLE_NAMES):
        raise ProvisionError("Category state is missing its audience roles.")
    return clean


def _load_state(path: Path, guild_id: str, bot_user_id: str | None = None) -> dict:
    if path.is_symlink():
        raise ProvisionError("Provisioning state must be a regular file, not a symlink.")
    if not path.exists():
        if bot_user_id is None:
            raise ProvisionError("Provision the server before binding a membership integration.")
        return _new_state(guild_id, bot_user_id)
    try:
        if path.stat().st_size > 65536:
            raise ProvisionError("Provisioning state is unexpectedly large.")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProvisionError("Unable to read a valid provisioning state file.") from exc
    return _validate_state(value, guild_id, bot_user_id)


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(5):
            try:
                os.replace(temp_name, path)
                break
            except PermissionError as exc:
                # Windows readers can briefly hold a file without delete sharing.
                # Keep the old state intact and the replacement atomic throughout.
                if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 4:
                    raise
                time.sleep(0.025 * 2**attempt)
    finally:
        if temp_name is not None and os.path.exists(temp_name):
            os.unlink(temp_name)


def _snapshot(
    client: DiscordClient,
    guild_id: str,
    bot_user_id: str,
    membership_bot_user_id: str | None = None,
) -> dict:
    guild = client.request("GET", f"/guilds/{guild_id}")
    roles = client.request("GET", f"/guilds/{guild_id}/roles")
    channels = client.request("GET", f"/guilds/{guild_id}/channels")
    member = client.request("GET", f"/guilds/{guild_id}/members/{bot_user_id}")
    if not isinstance(guild, dict) or guild.get("id") != guild_id:
        raise ProvisionError("Discord returned an invalid guild resource.")
    if (
        not isinstance(roles, list)
        or not isinstance(channels, list)
        or not isinstance(member, dict)
    ):
        raise ProvisionError("Discord returned an invalid resource inventory.")
    if member.get("user", {}).get("id") != bot_user_id or not member.get("user", {}).get("bot"):
        raise ProvisionError("The configured collector bot is not a bot member of this guild.")
    for objects, label in ((roles, "role"), (channels, "channel")):
        resource_ids = []
        for obj in objects:
            if not isinstance(obj, dict):
                raise ProvisionError(f"Discord returned an invalid {label} resource.")
            resource_ids.append(_snowflake(obj.get("id"), f"remote {label} ID"))
        if len(set(resource_ids)) != len(resource_ids):
            raise ProvisionError(f"Discord returned duplicate {label} IDs.")
    snapshot = {"guild": guild, "roles": roles, "channels": channels, "member": member}
    if membership_bot_user_id is not None:
        try:
            membership_member = client.request(
                "GET", f"/guilds/{guild_id}/members/{membership_bot_user_id}"
            )
        except DiscordError as exc:
            raise ProvisionError(
                "Cannot verify the bound membership bot's guild membership."
            ) from exc
        if not isinstance(membership_member, dict):
            raise ProvisionError("The bound membership bot is not a real bot member of this guild.")
        membership_user = membership_member.get("user")
        if (
            not isinstance(membership_user, dict)
            or membership_user.get("id") != membership_bot_user_id
            or membership_user.get("bot") is not True
            or not isinstance(membership_member.get("roles"), list)
        ):
            raise ProvisionError("The bound membership bot is not a real bot member of this guild.")
        snapshot["membership_member"] = membership_member
    return snapshot


def _acl(value: object) -> tuple:
    """Compare actual ACL semantics, ignoring array ordering and number encoding."""
    if not isinstance(value, list):
        raise ProvisionError("A channel is missing its permission overwrites.")
    result = []
    for overwrite in value:
        if not isinstance(overwrite, dict):
            raise ProvisionError("Invalid permission overwrite.")
        resource_id = _snowflake(overwrite.get("id"), "overwrite ID")
        kind = overwrite.get("type")
        if type(kind) is not int or kind not in (0, 1):
            raise ProvisionError("Invalid permission overwrite target type.")
        bitfields = (overwrite.get("allow"), overwrite.get("deny"))
        if any(
            type(bits) is not int
            and not (isinstance(bits, str) and bits.isascii() and bits.isdecimal())
            for bits in bitfields
        ):
            raise ProvisionError("Invalid permission overwrite bitfield encoding.")
        allow, deny = map(int, bitfields)
        if min(allow, deny) < 0 or allow & deny:
            raise ProvisionError("Invalid or contradictory permission overwrite bitfield.")
        result.append((kind, resource_id, allow, deny))
    if len({(kind, resource_id) for kind, resource_id, _, _ in result}) != len(result):
        raise ProvisionError("Duplicate permission overwrite target.")
    # A zero allow/deny pair changes no inherited permission bits. Validate all
    # entries and duplicates first, then omit only these semantic no-ops.
    return tuple(sorted(entry for entry in result if entry[2] or entry[3]))


def _role_findings(
    snapshot: dict, state: dict, *, allow_setup=False, ignore_everyone=False
) -> list[str]:
    findings = []
    bot_roles = set(snapshot["member"].get("roles", []))
    paid_roles = set(state["roles"].values())
    membership_role_id, membership_findings = _membership_role(snapshot, state)
    findings.extend(membership_findings)
    for role in snapshot["roles"]:
        role_id = role["id"]
        try:
            permissions = int(role.get("permissions", "0"))
        except (TypeError, ValueError):
            findings.append(f"Role {role_id}: invalid base permissions.")
            continue
        if permissions & ADMINISTRATOR:
            findings.append(f"Role {role_id}: Administrator bypasses channel privacy; remove it.")
        if ignore_everyone and role_id == state["guild_id"]:
            continue
        if role_id == state["guild_id"] or role_id in paid_roles:
            if permissions:
                findings.append(
                    f"Role {role_id}: expected zero guild permissions; "
                    "access belongs in channel overwrites."
                )
            if role.get("mentionable"):
                findings.append(f"Role {role_id}: role is mentionable.")
            continue
        if role_id == membership_role_id:
            if not permissions & MANAGE_ROLES:
                findings.append(
                    f"Membership bot role {role_id}: Manage Roles is required to sync paid roles."
                )
            if permissions & ~(MANAGE_ROLES | CREATE_INVITE | VIEW_CHANNEL):
                findings.append(
                    f"Membership bot role {role_id}: only Manage Roles and optional "
                    "Create Invite/View Channels are permitted."
                )
        elif role_id in bot_roles:
            allowed = BOT_RUNTIME_PERMISSIONS | (SETUP_PERMISSIONS if allow_setup else 0)
            if permissions & ~allowed:
                findings.append(
                    f"Bot role {role_id}: remove privileges beyond "
                    "View/Send/Embed/Read History before running delivery."
                )
        else:
            if role.get("mentionable"):
                findings.append(f"Role {role_id}: role is mentionable.")
            if permissions & ~READER_PERMISSIONS:
                findings.append(
                    f"Role {role_id}: unexpected privileged base permissions "
                    "can bypass the intended policy."
                )
    return findings


def _membership_role(snapshot: dict, state: dict) -> tuple[str | None, list[str]]:
    """Bind role-management trust to Discord's managed-role identity, never a name."""
    bot_id = state.get("membership_bot_user_id")
    if bot_id is None:
        return None, []
    matching = [
        role
        for role in snapshot["roles"]
        if role.get("managed") is True
        and isinstance(role.get("tags"), dict)
        and role["tags"].get("bot_id") == bot_id
    ]
    if len(matching) != 1:
        return None, [
            "Membership bot must have exactly one real managed role with matching bot_id tags."
        ]
    role = matching[0]
    role_id = role["id"]
    member = snapshot.get("membership_member", {})
    if role_id not in member.get("roles", []):
        return None, ["The verified membership bot does not hold its managed integration role."]
    positions = {resource["id"]: resource.get("position") for resource in snapshot["roles"]}
    position = role.get("position")
    if (
        type(position) is not int
        or set(state["roles"]) != set(ROLE_NAMES)
        or any(
            type(positions.get(role_id)) is not int or positions[role_id] >= position
            for role_id in state["roles"].values()
        )
    ):
        return None, ["Membership bot's managed role must be strictly above Starter and Pro."]
    return role_id, []


def _resource_findings(
    snapshot: dict, state: dict, *, complete=True, allow_category_renames=False
) -> list[str]:
    findings = []
    roles = {r["id"]: r for r in snapshot["roles"]}
    channels = {c["id"]: c for c in snapshot["channels"]}
    tracked = set(state["categories"].values()) | set(state["channels"].values())
    for resource_id in channels.keys() - tracked:
        findings.append(
            f"Untracked channel {resource_id}; existing channels are never adopted automatically."
        )
    for key, name in ROLE_NAMES.items():
        resource_id = state["roles"].get(key)
        if resource_id is None:
            if complete:
                findings.append(f"Missing managed role: {key}.")
            if any(role.get("name") == name for role in roles.values()):
                findings.append(f"Untracked same-name role for {key}; cannot safely adopt it.")
            continue
        role = roles.get(resource_id)
        if role is None or role.get("name") != name or role.get("managed"):
            findings.append(f"Tracked role {key} is missing, renamed, or externally managed.")
    category_by_key = {category.key: category for category in CATEGORIES}
    optional = tuple(c for c in OPTIONAL_CHANNELS if c.key in state["channels"])
    for spec in (*CATEGORIES, *CHANNELS, *optional):
        is_category = spec.key in category_by_key
        mapping = state["categories" if is_category else "channels"]
        resource_id = mapping.get(spec.key)
        if resource_id is None:
            if complete:
                findings.append(
                    f"Missing managed {'category' if is_category else 'channel'}: {spec.key}."
                )
            continue
        channel = channels.get(resource_id)
        if channel is None:
            findings.append(
                f"Tracked channel {spec.key} is missing; "
                "review state instead of replacing it blindly."
            )
            continue
        category = spec if is_category else category_by_key[spec.category]
        expected_name = spec.name
        expected_parent = None if is_category else state["categories"].get(spec.category)
        name_required = not (is_category and allow_category_renames)
        if channel.get("type") != (4 if is_category else 0) or (
            name_required and channel.get("name") != expected_name
        ):
            findings.append(f"Tracked channel {spec.key}: unexpected name or channel type.")
        if channel.get("parent_id") != expected_parent:
            findings.append(f"Tracked channel {spec.key}: wrong category parent.")
        if not is_category and category.community:
            slowmode = channel.get("rate_limit_per_user")
            if type(slowmode) is not int or slowmode < 10:
                findings.append(
                    f"Tracked channel {spec.key}: slowmode must be at least 10 seconds."
                )
        if set(state["roles"]) != set(ROLE_NAMES):
            findings.append(f"Tracked channel {spec.key}: audience roles are incomplete.")
            continue
        expected_acl = permission_overwrites(
            state["guild_id"],
            state["bot_user_id"],
            state["roles"],
            category,
            application_commands=spec.key == "item-requests",
        )
        try:
            if _acl(channel.get("permission_overwrites")) != _acl(expected_acl):
                findings.append(
                    f"Tracked channel {spec.key}: unsafe or unsynced permission overwrites "
                    "(including extra role/member allows)."
                )
        except (ProvisionError, TypeError, ValueError):
            findings.append(f"Tracked channel {spec.key}: invalid permission overwrites.")
    return findings


def _privacy_findings(snapshot: dict) -> tuple[list[str], list[str]]:
    guild = snapshot["guild"]
    findings, limitations = [], []
    if "DISCOVERABLE" in guild.get("features", []) or "PREVIEW_ENABLED" in guild.get(
        "features", []
    ):
        findings.append("Server Discovery or public preview is enabled; the owner must disable it.")
    if guild.get("widget_enabled") is True:
        findings.append("Server widget is enabled; disable it before exposing paid alerts.")
    elif "widget_enabled" not in guild:
        limitations.append(
            "The guild response omitted widget status; runtime audit cannot verify it "
            "without Manage Guild. Provisioning checks the widget endpoint."
        )
    for field, minimum in (("verification_level", 2), ("explicit_content_filter", 2)):
        if guild.get(field, -1) < minimum:
            findings.append(f"Server {field} is below {minimum}.")
    if guild.get("default_message_notifications") != 1:
        findings.append("Server default notifications must be mentions-only.")
    limitations.append(
        "The server owner always bypasses channel permissions. Administrator access also "
        "bypasses them; all configured role permissions are checked."
    )
    limitations.append(
        "AutoMod link and text-safety policies require separate owner verification: "
        "the minimal collector bot lacks Manage Guild and cannot read AutoMod rules. "
        "Denying Embed Links prevents previews, not plain-text URLs."
    )
    return findings, limitations


def audit(client: DiscordClient, guild_id: str, state: dict) -> dict:
    """Read-only privacy audit, suitable for the runtime's fail-closed send gate.

    This inspects guild policy, roles, category inheritance, every managed channel,
    and unexpected channels/overwrites. It cannot stop members copying messages or
    verify application/billing assignments outside Discord.
    """
    try:
        guild_id = _snowflake(guild_id, "guild ID")
        state = _validate_state(state, guild_id)
        snapshot = _snapshot(
            client, guild_id, state["bot_user_id"], state.get("membership_bot_user_id")
        )
        # Category headings are cosmetic. Stable mapped IDs, channel types,
        # parent relationships and exact ACLs remain mandatory at runtime.
        # Provisioning keeps strict names to avoid adopting/renaming resources.
        findings = _role_findings(snapshot, state) + _resource_findings(
            snapshot, state, allow_category_renames=True
        )
        privacy, limitations = _privacy_findings(snapshot)
        findings.extend(privacy)
        if "membership_bot_user_id" in state:
            limitations.append(
                "The bound membership bot is a trusted access manager: Manage Roles lets it "
                "assign Starter/Pro to itself or others. It has no direct private-channel "
                "allow overwrite. Optional Create Invite permits subscriber joins; "
                "billing lifecycle and app configuration require separate verification."
            )
        return {"ok": not findings, "findings": findings, "limitations": limitations}
    except ProvisionError as exc:
        return {
            "ok": False,
            "findings": [f"Cannot verify server privacy: {exc}"],
            "limitations": [],
        }
    except (TypeError, ValueError):
        return {
            "ok": False,
            "findings": [
                "Cannot verify server privacy: invalid state or Discord resource response."
            ],
            "limitations": [],
        }


def bind_membership(
    client: DiscordClient,
    guild_id: str,
    membership_bot_user_id: str,
    state_path: Path,
) -> dict:
    """Verify a membership access manager before atomically binding its local ID.

    No Discord mutation occurs. A rejected or unavailable verification preserves
    the existing state file byte-for-byte. An explicit successful binding delegates
    paid-role assignment to the verified integration, including access for itself.
    """
    try:
        guild_id = _snowflake(guild_id, "guild ID")
        state_path = Path(state_path)
        state = _load_state(state_path, guild_id)
        state["membership_bot_user_id"] = _snowflake(
            membership_bot_user_id, "membership bot user ID"
        )
        state = _validate_state(state, guild_id)
        assessment = audit(client, guild_id, state)
        if not assessment["ok"]:
            return {"ok": False, "bound": False, "assessment": assessment}
        _save_state(state_path, state)
        return {"ok": True, "bound": True, "assessment": assessment, "state": state}
    except ProvisionError as exc:
        reason = str(exc)
    except DiscordError:
        reason = "Discord verification is unavailable; membership trust was not saved."
    except OSError:
        reason = "Unable to read or atomically save membership trust."
    except (TypeError, ValueError):
        reason = "Invalid membership trust input or Discord resource response."
    return {
        "ok": False,
        "bound": False,
        "assessment": {"ok": False, "findings": [reason], "limitations": []},
    }


def provision(
    client: DiscordClient,
    guild_id: str,
    bot_user_id: str,
    state_path: Path,
    *,
    apply: bool = False,
) -> dict:
    """Plan by default; explicitly apply an empty/private server layout.

    Setup needs temporary Manage Guild, Manage Channels, and Manage Roles. Never
    grant Administrator. Remove setup permissions after creation, then audit again.
    Only one setup process may run against a guild/state file at a time.
    """
    guild_id = _snowflake(guild_id, "guild ID")
    bot_user_id = _snowflake(bot_user_id, "bot user ID")
    if guild_id == bot_user_id:
        raise ProvisionError("Guild and bot user IDs must differ.")
    state_path = Path(state_path)
    state = _load_state(state_path, guild_id, bot_user_id)
    snapshot = _snapshot(client, guild_id, bot_user_id, state.get("membership_bot_user_id"))
    guild = snapshot["guild"]
    if any(feature in guild.get("features", []) for feature in ("DISCOVERABLE", "PREVIEW_ENABLED")):
        raise ProvisionError(
            "Server Discovery/public preview is enabled. The owner must disable it; "
            "do not grant Administrator to this bot."
        )
    blockers = _role_findings(snapshot, state, allow_setup=True, ignore_everyone=True)
    blockers.extend(_resource_findings(snapshot, state, complete=False))
    if not any(role["id"] == guild_id for role in snapshot["roles"]):
        blockers.append("The @everyone role is missing from the Discord inventory.")
    if blockers:
        raise ProvisionError("Setup refused: " + " ".join(blockers))

    # Provisioning will reset @everyone to zero, so its current grants cannot
    # supply the bot's create permissions. Check the post-reset role base before
    # any mutation or local ownership write. Runtime audit needs no setup grant.
    bot_roles = set(snapshot["member"].get("roles", []))
    bot_base = 0
    for role in snapshot["roles"]:
        if role["id"] != guild_id and role["id"] in bot_roles:
            bot_base |= int(role.get("permissions", "0"))
    if bot_base & PROVISION_PERMISSIONS != PROVISION_PERMISSIONS:
        raise ProvisionError(
            "Setup requires View Channels, Send Messages, Embed Links and Read Message History "
            "plus temporary Manage Server, Manage Channels and Manage Roles on the bot's "
            "guild role. Never grant Administrator. Remove setup grants after provisioning."
        )

    widget = client.request("GET", f"/guilds/{guild_id}/widget")
    if not isinstance(widget, dict) or not isinstance(widget.get("enabled"), bool):
        raise ProvisionError(
            "Cannot verify server widget settings; setup requires temporary Manage Guild."
        )
    actions = []
    security = {}
    for field in ("verification_level", "explicit_content_filter"):
        if guild.get(field, -1) < 2:
            security[field] = 2
    if guild.get("default_message_notifications") != 1:
        security["default_message_notifications"] = 1
    if security:
        actions.append({"action": "update-guild-security", "settings": security})
    if widget["enabled"] or widget.get("channel_id") is not None:
        actions.append({"action": "disable-widget"})
    everyone = next(role for role in snapshot["roles"] if role["id"] == guild_id)
    if int(everyone.get("permissions", "0")) or everyone.get("mentionable"):
        actions.append({"action": "restrict-everyone", "permissions": "0", "mentionable": False})
    for key, name in ROLE_NAMES.items():
        if key not in state["roles"]:
            actions.append({"action": "create-role", "key": key, "name": name, "permissions": "0"})
    for category in CATEGORIES:
        if category.key not in state["categories"]:
            actions.append(
                {
                    "action": "create-category",
                    "key": category.key,
                    "name": category.name,
                    "audience": category.audience,
                }
            )
    for channel in CHANNELS:
        if channel.key not in state["channels"]:
            actions.append(
                {"action": "create-channel", "key": channel.key, "category": channel.category}
            )

    if not apply:
        findings = _role_findings(snapshot, state) + _resource_findings(snapshot, state)
        privacy, limitations = _privacy_findings(snapshot)
        findings.extend(privacy)
        return {
            "applied": False,
            "state": state,
            "actions": actions,
            "audit": {"ok": not findings, "findings": findings, "limitations": limitations},
        }

    # Initial ownership record precedes all remote mutations. Each new ID is
    # fsynced and replaced atomically before the next resource is created.
    _save_state(state_path, state)
    categories = {category.key: category for category in CATEGORIES}
    channel_specs = {channel.key: channel for channel in CHANNELS}
    for action in actions:
        kind = action["action"]
        if kind == "update-guild-security":
            client.request("PATCH", f"/guilds/{guild_id}", action["settings"])
        elif kind == "disable-widget":
            client.request(
                "PATCH", f"/guilds/{guild_id}/widget", {"enabled": False, "channel_id": None}
            )
        elif kind == "restrict-everyone":
            client.request(
                "PATCH",
                f"/guilds/{guild_id}/roles/{guild_id}",
                {"permissions": "0", "mentionable": False},
            )
        elif kind == "create-role":
            role = client.request(
                "POST",
                f"/guilds/{guild_id}/roles",
                {
                    "name": action["name"],
                    "permissions": "0",
                    "hoist": False,
                    "mentionable": False,
                },
            )
            if not isinstance(role, dict):
                raise ProvisionError(
                    "Discord did not confirm the created role; "
                    "reconcile server resources before retrying."
                )
            state["roles"][action["key"]] = _snowflake(role.get("id"), "created role ID")
            _save_state(state_path, state)
        elif kind in ("create-category", "create-channel"):
            is_category = kind == "create-category"
            category = categories[action["key"] if is_category else action["category"]]
            payload = {
                "name": category.name if is_category else channel_specs[action["key"]].name,
                "type": 4 if is_category else 0,
                "permission_overwrites": permission_overwrites(
                    guild_id, bot_user_id, state["roles"], category
                ),
            }
            if not is_category:
                payload["parent_id"] = state["categories"][category.key]
                payload["topic"] = channel_specs[action["key"]].topic
                payload["rate_limit_per_user"] = 10 if category.community else 0
            channel = client.request("POST", f"/guilds/{guild_id}/channels", payload)
            if not isinstance(channel, dict):
                raise ProvisionError(
                    "Discord did not confirm the created channel; "
                    "reconcile server resources before retrying."
                )
            state["categories" if is_category else "channels"][action["key"]] = _snowflake(
                channel.get("id"), "created channel ID"
            )
            _save_state(state_path, state)
    return {
        "applied": True,
        "state": state,
        "actions": actions,
        "audit": audit(client, guild_id, state),
    }
