"""Run with python -m discord_bridge. All external mutations are explicit."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
from pathlib import Path

from .api import DiscordClient, DiscordError
from .core import JobStore, render_message, route_keys, validate_batch
from .monitoring import DeliveryMonitor, emit_event
from .provision import ProvisionError
from .service import deliver_due, make_server

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "data" / "discord_bridge"
ENV_FILE = PRIVATE / "local.env"
STATE_FILE = PRIVATE / "server.json"
DATABASE = PRIVATE / "queue.sqlite3"


def initialize() -> dict:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    if ENV_FILE.exists():
        return {"created": False, "note": "Existing local settings preserved."}
    descriptor = os.open(ENV_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        if os.name == "nt":
            output = subprocess.run(
                ["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True
            )
            sid = next(csv.reader(output.stdout.strip().splitlines()))[-1]
            if not re.fullmatch(r"S-1-\d+(?:-\d+)+", sid):
                raise ValueError("Could not identify the Windows owner")
            subprocess.run(
                [
                    "icacls",
                    str(ENV_FILE),
                    "/inheritance:r",
                    "/grant:r",
                    f"*{sid}:(R,W)",
                    "*S-1-5-18:(F)",
                ],
                capture_output=True,
                check=True,
            )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            handle.write(
                "# Private local settings. Never paste this file into chat.\n"
                f"DISCORD_BRIDGE_INGEST_KEY={secrets.token_urlsafe(32)}\n"
                "DISCORD_BOT_TOKEN=\n"
            )
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        ENV_FILE.unlink(missing_ok=True)
        raise ValueError("Private settings could not be secured; nothing was configured") from None
    return {"created": True, "note": "Private settings created; live sending remains off."}


def load_settings(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator and key in {"DISCORD_BRIDGE_INGEST_KEY", "DISCORD_BOT_TOKEN"}:
            os.environ.setdefault(key, value.strip())


def read_state(path: Path) -> dict:
    state = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(state, dict)
        or state.get("schema_version") != 1
        or not re.fullmatch(r"\d{17,20}", str(state.get("guild_id", "")))
    ):
        raise ValueError("Invalid server mapping; provision the intended server first")
    return state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Private Discord setup and independent delayed feeds"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create private local secrets without sending or modifying Discord")
    sub.add_parser("plan", help="Print the proposed roles/channels without network calls")
    preview = sub.add_parser("preview", help="Inspect one input file without queueing or sending")
    preview.add_argument("input", type=Path)
    enqueue = sub.add_parser("enqueue", help="Durably queue one file; does not send")
    enqueue.add_argument("input", type=Path)
    enqueue.add_argument("--database", type=Path, default=DATABASE)
    provision_parser = sub.add_parser(
        "provision", help="Dry-run by default; --apply creates server resources"
    )
    provision_parser.add_argument("--guild", required=True)
    provision_parser.add_argument("--state", type=Path, default=STATE_FILE)
    provision_parser.add_argument("--apply", action="store_true")
    audit_parser = sub.add_parser("audit", help="Read-only check of the actual protected channels")
    audit_parser.add_argument("--state", type=Path, default=STATE_FILE)
    membership = sub.add_parser(
        "trust-membership", help="Audit and locally bind a verified paid-role management bot"
    )
    membership.add_argument("--bot", required=True)
    membership.add_argument("--state", type=Path, default=STATE_FILE)
    serve = sub.add_parser("serve", help="Authenticated loopback intake; preview mode by default")
    serve.add_argument("--port", type=int, default=8788)
    serve.add_argument("--database", type=Path, default=DATABASE)
    serve.add_argument("--state", type=Path, default=STATE_FILE)
    serve.add_argument("--live", action="store_true", help="Explicitly enable actual Discord sends")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(json.dumps(initialize()))
            return 0
        if args.command == "plan":
            from .layout import server_manifest

            print(json.dumps(server_manifest(), indent=2))
            return 0
        if args.command in {"preview", "enqueue"}:
            if args.input.stat().st_size > 524288:
                raise ValueError("input exceeds 512 KiB")
            items = validate_batch(json.loads(args.input.read_text(encoding="utf-8")))
            if args.command == "enqueue":
                store = JobStore(args.database)
                result = store.record(items)
                DeliveryMonitor(store.path.parent, entry_point="cli_enqueue").intake(result)
                print(json.dumps(result))
            else:
                print(
                    json.dumps(
                        [
                            {
                                "item_id": item["item_id"],
                                "routes": route_keys(item),
                                "starter_delay_seconds": 300,
                                "example": render_message(
                                    item, first_seen=0, last_seen=0, route="pro-all"
                                ),
                            }
                            for item in items
                        ],
                        indent=2,
                    )
                )
            return 0
        load_settings()
        if args.command in {"provision", "audit", "trust-membership"}:
            from .provision import audit, bind_membership, provision

            client = DiscordClient(os.environ.get("DISCORD_BOT_TOKEN", ""))
            if args.command == "provision":
                if not re.fullmatch(r"\d{17,20}", args.guild):
                    raise ValueError("guild must be the intended server's numeric ID")
                me = client.request("GET", "/users/@me")
                result = provision(client, args.guild, me["id"], args.state, apply=args.apply)
            elif args.command == "audit":
                state = read_state(args.state)
                result = audit(client, state["guild_id"], state)
            else:
                if not re.fullmatch(r"\d{17,20}", args.bot):
                    raise ValueError("bot must be the verified membership bot's numeric ID")
                state = read_state(args.state)
                result = bind_membership(client, state["guild_id"], args.bot, args.state)
            print(json.dumps(result, indent=2))
            return 2 if result.get("ok") is False else 0
        if not 1024 <= args.port <= 65535:
            raise ValueError("port must be between 1024 and 65535")
        store = JobStore(args.database)
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        monitor = DeliveryMonitor(store.path.parent, entry_point="standalone_worker")
        state = read_state(args.state) if args.live else {}
        client = DiscordClient(os.environ.get("DISCORD_BOT_TOKEN", "")) if args.live else None
        if args.live:
            from .provision import audit

            try:
                assessment = audit(client, state["guild_id"], state)
            except (DiscordError, ValueError, KeyError, TypeError, OSError) as exc:
                emit_event(
                    monitor.emit,
                    "privacy_blocked",
                    reason="audit_error",
                    phase="audit",
                    error_type=type(exc).__name__,
                    status=getattr(exc, "status", None) or 0,
                )
                monitor.heartbeat(store, force=True)
                print(
                    json.dumps({"error": "Startup audit failed; queued jobs retained"}),
                    file=sys.stderr,
                )
                return 2
            if assessment.get("ok") is not True:
                emit_event(monitor.emit, "privacy_blocked", reason="privacy_audit", phase="audit")
                monitor.heartbeat(store, force=True)
                print(
                    json.dumps({"error": "Permission audit failed; sending refused", **assessment})
                )
                return 2
        stopping, waking = threading.Event(), threading.Event()
        server = make_server(
            store,
            key=os.environ.get("DISCORD_BRIDGE_INGEST_KEY", ""),
            port=args.port,
            live=args.live,
            on_queued=waking.set,
            on_accepted=monitor.intake,
        )

        def worker():
            monitor.heartbeat(store)
            while not stopping.is_set():
                waking.clear()
                wait_seconds = 5
                try:
                    result = deliver_due(
                        store,
                        client,
                        state,
                        live=True,
                        stop_event=stopping,
                        event_sink=monitor.emit,
                    )
                    wait_seconds = result["wait_seconds"]
                except Exception as exc:
                    emit_event(
                        monitor.emit, "worker_error", phase="worker", error_type=type(exc).__name__
                    )
                finally:
                    monitor.heartbeat(store)
                waking.wait(min(5, max(0.01, wait_seconds)))

        delivery_thread = threading.Thread(target=worker, name="discord-delivery", daemon=True)
        requests_runtime = None
        if (
            args.live
            and os.environ.get("DISCORD_REQUESTS_ENABLED", "false").strip().lower() == "true"
        ):
            from .request_runtime import RequestRuntime

            requests_runtime = RequestRuntime(
                client,
                state,
                store.path,
                token=os.environ.get("DISCORD_BOT_TOKEN", ""),
                stop_event=stopping,
                event_sink=monitor.emit,
            )
            requests_runtime.start()
        if args.live:
            delivery_thread.start()
        print(
            f"Private bridge listening on http://127.0.0.1:{args.port}; "
            f"live sending {'ON' if args.live else 'OFF'}",
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stopping.set()
            waking.set()
            server.server_close()
            if requests_runtime is not None:
                requests_runtime.stop()
            if args.live:
                delivery_thread.join(timeout=12)
            if (
                client is not None
                and not delivery_thread.is_alive()
                and (requests_runtime is None or not requests_runtime.is_alive())
            ):
                client.close()
        return 0
    except (ValueError, KeyError, TypeError, OSError, DiscordError, ProvisionError) as exc:
        # Do not print provider bodies, token-bearing URLs, inputs or stack traces.
        safe = (
            str(exc)
            if isinstance(exc, (ValueError, DiscordError, ProvisionError))
            else "Check local configuration and file access"
        )
        print(json.dumps({"error": safe}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
