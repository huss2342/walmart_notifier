"""Explicit, dry-run-first setup for the optional personal request channel."""

from __future__ import annotations

import argparse
import json
import os
import threading
from pathlib import Path

from .api import DiscordClient, DiscordError
from .layout import CATEGORIES, OPTIONAL_CHANNELS, permission_overwrites
from .provision import ProvisionError, _load_state, _save_state, _snowflake, audit


def request_audit(client, state, *, stop_event=None):
    """Allow the shared transport's short local cooldowns during a read-only audit."""
    stopped = stop_event if stop_event is not None else threading.Event()

    class Reader:
        def request(self, method, path):
            if method != "GET":
                raise ValueError("Request feature audit must be read-only")
            for attempt in range(8):
                if stopped.is_set():
                    raise ValueError("Request feature stopped")
                try:
                    return client.request(method, path)
                except DiscordError as exc:
                    if exc.status != 429 or attempt == 7 or exc.retry_after > 3:
                        raise
                    if stopped.wait(max(0.05, exc.retry_after)):
                        raise ValueError("Request feature stopped") from None
            raise ValueError("Request feature audit deferred")

    return audit(Reader(), state["guild_id"], state)


def request_channel_plan(state: dict) -> dict:
    spec = next(c for c in OPTIONAL_CHANNELS if c.key == "item-requests")
    category = next(c for c in CATEGORIES if c.key == spec.category)
    return {
        "name": spec.name,
        "type": 0,
        "parent_id": state["categories"][spec.category],
        "topic": spec.topic,
        "permission_overwrites": permission_overwrites(
            state["guild_id"],
            state["bot_user_id"],
            state["roles"],
            category,
            application_commands=True,
        ),
        "rate_limit_per_user": 0,
    }


def bind_request_channel(client, state_path: Path, channel_id: str, *, apply=False) -> dict:
    """Bind only an exact owner-specified channel; never create/adopt by name."""
    from .__main__ import read_state

    saved = read_state(state_path)
    state = _load_state(state_path, saved["guild_id"], saved["bot_user_id"])
    channel_id = _snowflake(channel_id, "request channel ID")
    current = state["channels"].get("item-requests")
    if current is not None and current != channel_id:
        raise ProvisionError("Request channel is already bound; review the existing mapping")
    tracked = set(state["categories"].values()) | set(state["channels"].values())
    if channel_id in tracked and current != channel_id:
        raise ProvisionError("Request channel ID already belongs to another managed resource")
    state["channels"]["item-requests"] = channel_id
    assessment = request_audit(client, state)
    if assessment.get("ok") is not True:
        raise ProvisionError("Request channel does not pass the server privacy audit")
    if apply:
        _save_state(state_path, state)
    return {"applied": apply, "state": state, "audit": assessment}


def main(argv=None):
    from .__main__ import STATE_FILE, load_settings, read_state

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "bind", "register"))
    parser.add_argument("--state", type=Path, default=STATE_FILE)
    parser.add_argument("--channel")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    client = None
    try:
        state = read_state(args.state)
        if args.action == "plan":
            print(json.dumps(request_channel_plan(state), indent=2))
            return 0
        if args.action == "register" and not args.apply:
            from .request_commands import request_command_definition

            print(json.dumps({"applied": False, "command": request_command_definition()}, indent=2))
            return 0
        if args.action == "bind" and not args.channel:
            raise ValueError("bind requires --channel with the owner-created channel ID")
        load_settings()
        client = DiscordClient(os.environ.get("DISCORD_BOT_TOKEN", ""))
        if args.action == "bind":
            result = bind_request_channel(client, args.state, args.channel, apply=args.apply)
            print(json.dumps(result, indent=2))
        else:
            from .request_commands import register_request_commands

            if "item-requests" not in state["channels"]:
                raise ValueError("Bind and audit the request channel before registering commands")
            if not request_audit(client, state).get("ok"):
                raise ValueError("Server privacy audit must pass before registering commands")
            result = register_request_commands(client, state)
            print(json.dumps({"applied": True, "command_id": result["id"]}))
        return 0
    except (ValueError, OSError, KeyError, DiscordError, ProvisionError) as exc:
        print(json.dumps({"error": type(exc).__name__, "status": getattr(exc, "status", None)}))
        return 2
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
