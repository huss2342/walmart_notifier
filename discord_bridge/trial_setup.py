"""Reviewable setup for owner-issued and automatic Discord Pro trials."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path

from .api import DiscordClient, DiscordError
from .layout import CATEGORIES, CHANNELS, OPTIONAL_CHANNELS, permission_overwrites
from .provision import ProvisionError, _load_state, _save_state, _snowflake
from .request_setup import request_audit


def trial_access_plan(state, role_id):
    """Exact permission payloads; no remote writes or role adoption by name."""
    role_id = _snowflake(role_id, "Pro Trial role ID")
    candidate = copy.deepcopy(state)
    if role_id in {state["guild_id"], state["bot_user_id"], *state["roles"].values()} - {
        state["roles"].get("trial")
    }:
        raise ProvisionError("Trial role must be distinct from existing resources")
    if state["roles"].get("trial") not in {None, role_id}:
        raise ProvisionError("A different trial role is already bound")
    candidate["roles"]["trial"] = role_id
    category_by_key = {category.key: category for category in CATEGORIES}
    updates = []
    for spec in (*CATEGORIES, *CHANNELS, *OPTIONAL_CHANNELS):
        is_category = spec.key in category_by_key
        mapping = candidate["categories" if is_category else "channels"]
        category = spec if is_category else category_by_key[spec.category]
        if spec.key not in mapping or category.audience != "pro":
            continue
        updates.append({"channel_id": mapping[spec.key], "payload": {
            "permission_overwrites": permission_overwrites(
                state["guild_id"], state["bot_user_id"], candidate["roles"], category,
                application_commands=spec.key == "item-requests",
            ),
        }})
    spec = next(c for c in OPTIONAL_CHANNELS if c.key == "trial-management")
    channel = {
        "name": spec.name, "type": 0,
        "parent_id": candidate["categories"]["operations"], "topic": spec.topic,
        "permission_overwrites": permission_overwrites(
            state["guild_id"], state["bot_user_id"], candidate["roles"],
            category_by_key["operations"], application_commands=True,
        ),
        "rate_limit_per_user": 0,
    }
    return {
        "applied": False,
        "role": {"name": "Pro Trial", "permissions": "0", "mentionable": False, "hoist": False},
        "hierarchy": "Pro Trial < collector bot < paid Pro < Whop bot",
        "runtime_permission_addition": "Manage Roles",
        "channel": channel, "updates": updates,
        "welcome_default": {"enabled": False, "hours": 24},
    }


def bind_trial_access(client, state_path, role_id, channel_id, *, apply=False):
    """Bind only exact, already-prepared resources after the full privacy audit."""
    from .__main__ import read_state

    saved = read_state(state_path)
    state = _load_state(state_path, saved["guild_id"], saved["bot_user_id"])
    trial_access_plan(state, role_id)
    channel_id = _snowflake(channel_id, "trial-management channel ID")
    current = state["channels"].get("trial-management")
    tracked = set(state["categories"].values()) | set(state["channels"].values())
    if current not in {None, channel_id} or (channel_id in tracked and current != channel_id):
        raise ProvisionError("Trial channel ID conflicts with an existing binding")
    state["roles"]["trial"] = role_id
    state["channels"]["trial-management"] = channel_id
    assessment = request_audit(client, state)
    if assessment.get("ok") is not True:
        raise ProvisionError("Trial resources must pass the complete server privacy audit")
    if apply:
        _save_state(Path(state_path), state)
    return {"applied": apply, "state": state, "audit": assessment}


def main(argv=None):
    from .__main__ import STATE_FILE, load_settings, read_state

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "bind", "register"))
    parser.add_argument("--state", type=Path, default=STATE_FILE)
    parser.add_argument("--role")
    parser.add_argument("--channel")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    client = None
    try:
        state = read_state(args.state)
        if args.action == "register" and not args.apply:
            from .trial_commands import trial_command_definition

            print(json.dumps({"applied": False, "command": trial_command_definition()}, indent=2))
            return 0
        if args.action in {"plan", "bind"} and not args.role:
            raise ValueError("Use --role with the exact Pro Trial role ID")
        if args.action == "plan":
            print(json.dumps(trial_access_plan(state, args.role), indent=2))
            return 0
        if args.action == "bind" and not args.channel:
            raise ValueError("Use --channel with the exact trial-management channel ID")
        load_settings()
        client = DiscordClient(os.environ.get("DISCORD_BOT_TOKEN", ""))
        if args.action == "bind":
            result = bind_trial_access(
                client, args.state, args.role, args.channel, apply=args.apply,
            )
        else:
            from .trial_commands import register_trial_commands

            if not state.get("roles", {}).get("trial") or not state.get("channels", {}).get(
                "trial-management"
            ):
                raise ValueError("Bind trial resources before command registration")
            if not request_audit(client, state).get("ok"):
                raise ValueError("Privacy audit must pass before registering trials")
            result = {"applied": True, "command": register_trial_commands(client, state)}
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, KeyError, DiscordError, ProvisionError) as exc:
        print(json.dumps({"error": type(exc).__name__, "status": getattr(exc, "status", None)}))
        return 2
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
