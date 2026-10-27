"""Local ingest server.

Replaces what used to be an Azure Function. The reviewer portal can only be
read from a signed-in browser, so something on this machine has to be awake to
receive what the extension reads anyway -- and once that is true, a cloud
function is a bill and a deployment step buying nothing.

Binds to loopback only. Nothing here is reachable from the network.

    python src/server.py
"""

from __future__ import annotations

import argparse
import contextlib
import hmac
import ipaddress
import json
import logging
import math
import os
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discord_bridge.rolling_logs import RollingLogHandler  # noqa: E402

from browser_diagnostics import BrowserDiagnosticsStore  # noqa: E402
from config import (  # noqa: E402
    clear_user_rules,
    load_rules,
    rules_snapshot,
    rules_to_dicts,
    save_user_rules,
    seed_mode,
)
from filters import first_match  # noqa: E402
from models import Item  # noqa: E402
from notifiers import build_notifier  # noqa: E402
from notifiers.discord import DiscordNotifier  # noqa: E402
from notifiers.telegram import TelegramNotifier  # noqa: E402
from pipeline import process  # noqa: E402
from relay_settings import RelaySettingsStore  # noqa: E402
from relay_watchdog import RelayWatchdog  # noqa: E402
from scanner_control import (  # noqa: E402
    MAX_COMMANDS_BYTES,
    MAX_SETTINGS_BYTES,
    MAX_STATUS_BYTES,
    ScannerCommandsStore,
    ScannerSettingsStore,
    ScannerStatusStore,
)
from scanner_control import (
    parse_json as parse_scanner_json,
)
from sources.webhook_source import parse_json_ingest_payload  # noqa: E402
from state import SeenStore, default_path  # noqa: E402
from telegram_commands import (  # noqa: E402
    TelegramCommandPoller,
    disabled_command_health,
)

log = logging.getLogger("notifier")

MAX_INGEST_BYTES = 4_000_000
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
LAST_RELAY_MARKER = "runtime:last-successful-relay"
LAST_NOTIFICATION_MARKER = "runtime:last-item-notification"
BROWSER_HEARTBEAT_STALE_SECONDS = 180
BROWSER_PHASES = frozenset({
    "passive", "waiting-verification", "ready", "sweeping", "waiting-user",
    "ingest-retry", "complete", "unreachable",
})
# A loosened filter can match thousands of recorded items; cap the burst.
RESCAN_ALERT_LIMIT = 25


class RuntimeStatus:
    """Thread-safe process and relay state shared by HTTP and Telegram."""

    def __init__(self, store: SeenStore):
        self.store = store
        self.started_at = datetime.now(UTC)
        self.started_monotonic = time.monotonic()
        self._lock = threading.RLock()
        self._last_successful_relay = self._load_last_relay()
        self._last_notification = self._load_last_notification()
        self._last_attempt: dict | None = None
        # Browser reports are deliberately process-local. A saved ready phase
        # would misrepresent a closed browser after a server restart.
        self._browser: dict | None = None

    def _load_last_relay(self) -> dict | None:
        raw = self.store.get_marker(LAST_RELAY_MARKER)
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            log.warning("Ignoring an invalid saved relay status.")
            return None
        return _validated_relay_record(value)

    def _load_last_notification(self) -> dict | None:
        raw = self.store.get_marker(LAST_NOTIFICATION_MARKER)
        if not raw:
            return None
        try:
            return _validated_timestamp_record(json.loads(raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            log.warning("Ignoring an invalid saved item-notification status.")
            return None

    def _persist_record(self, marker: str, record: dict) -> None:
        try:
            self.store.set_marker(marker, json.dumps(record, separators=(",", ":")))
        except Exception as exc:
            # Status persistence must never turn a delivered alert into a retry.
            log.error("Could not persist runtime status (%s).", type(exc).__name__)

    def record_relay(self, summary: dict) -> None:
        record = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "summary": _safe_summary(summary),
        }
        with self._lock:
            self._last_attempt = record
            if (record["summary"].get("notified") or 0) > 0:
                self._last_notification = {"at": record["at"]}
                self._persist_record(LAST_NOTIFICATION_MARKER, self._last_notification)
            if record["summary"].get("failed") or record["summary"].get("pending"):
                return
            self._last_successful_relay = record
            self._persist_record(LAST_RELAY_MARKER, record)

    def record_browser(self, report: object) -> None:
        sanitized = _validated_browser_report(report)
        with self._lock:
            self._browser = {
                **sanitized,
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }

    def record_notification(self) -> None:
        """Background Discord delivery is distinct from accepting a relay."""
        with self._lock:
            self._last_notification = {"at": datetime.now(UTC).isoformat(timespec="seconds")}
            self._persist_record(LAST_NOTIFICATION_MARKER, self._last_notification)

    def record_failure(self, error_type: str) -> None:
        with self._lock:
            self._last_attempt = {
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "error_type": error_type[:100],
            }

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            last_success = _with_age(self._last_successful_relay)
            last_attempt = _with_age(self._last_attempt)
            last_notification = _with_age(self._last_notification)
            browser = _with_age(self._browser)
        return {
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "uptime_seconds": max(0, int(time.monotonic() - self.started_monotonic)),
            "last_successful_relay": last_success,
            "last_relay_attempt": last_attempt,
            "last_notification": last_notification,
            "browser": browser,
        }


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _validated_browser_report(value: object) -> dict:
    required = {"phase", "tab_count", "reachable", "refresh_minutes", "paused_until"}
    optional = {"last_sweep_pages", "last_sweep_at"}
    if not isinstance(value, dict):
        raise ValueError("browser report must be a JSON object")
    if not required.issubset(value) or value.keys() - required - optional:
        raise ValueError("browser report has missing or unsupported fields")
    if not isinstance(value["phase"], str) or value["phase"] not in BROWSER_PHASES:
        raise ValueError("unsupported browser phase")
    if not isinstance(value["reachable"], bool):
        raise ValueError("reachable must be a boolean")
    report = {**value, "last_sweep_pages": value.get("last_sweep_pages", 0),
              "last_sweep_at": value.get("last_sweep_at", 0)}
    # High Value has 62 searches, each capped at 100 pages by the extension.
    for key, maximum in (("tab_count", 100), ("last_sweep_pages", 6200)):
        count = report[key]
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= maximum:
            raise ValueError(f"{key} must be an integer from 0 to {maximum}")
    bounds = {
        "refresh_minutes": 120,
        "paused_until": datetime.now(UTC).timestamp() * 1000 + 7 * 86400 * 1000,
        "last_sweep_at": math.inf,
    }
    for key, maximum in bounds.items():
        number = report[key]
        if not _finite_number(number) or not 0 <= number <= maximum:
            raise ValueError(f"invalid {key}")
    return report


def _safe_summary(summary: object) -> dict[str, int | float | None]:
    keys = (
        "seen",
        "new",
        "duplicates",
        "filtered",
        "matched",
        "notified",
        "failed",
        "pending",
        "seeded",
        "queued",
        "value_known",
        "value_unknown",
        "min_value_usd",
        "max_value_usd",
    )
    if not isinstance(summary, dict):
        return {}
    result: dict[str, int | float | None] = {}
    for key in keys:
        if key not in summary:
            continue
        value = summary[key]
        if value is None or _finite_number(value):
            result[key] = value
    return result


def _validated_timestamp_record(value: object) -> dict | None:
    if not isinstance(value, dict) or not isinstance(value.get("at"), str):
        return None
    try:
        stamp = datetime.fromisoformat(value["at"])
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    return {"at": stamp.isoformat(timespec="seconds")}


def _validated_relay_record(value: object) -> dict | None:
    record = _validated_timestamp_record(value)
    if record is None:
        return None
    summary = _safe_summary(value.get("summary"))
    if not summary:
        return None
    return {**record, "summary": summary}


def _with_age(record: dict | None) -> dict | None:
    if record is None:
        return None
    result = dict(record)
    try:
        stamp = datetime.fromisoformat(str(result["at"]))
        result["age_seconds"] = max(0, int((datetime.now(UTC) - stamp).total_seconds()))
    except (KeyError, TypeError, ValueError):
        result["age_seconds"] = None
    return result


def _duration(seconds: object) -> str:
    value = max(0, int(seconds)) if isinstance(seconds, (int, float)) else 0
    if value < 60:
        return f"{value}s"
    if value < 3600:
        return f"{value // 60}m"
    if value < 86400:
        return f"{value // 3600}h {value % 3600 // 60}m"
    return f"{value // 86400}d {value % 86400 // 3600}h"


def _rule_status_text() -> str:
    rules = load_rules()
    if len(rules) > 1:
        return f"Filters: {len(rules)} active rules; use /filters for the full list"
    rule = rules[0]
    if rule.min_value_usd is None:
        value_range = "no minimum"
    elif rule.max_value_usd is None:
        value_range = f"${rule.min_value_usd:,.2f}+"
    else:
        value_range = f"${rule.min_value_usd:,.2f}-${rule.max_value_usd:,.2f}"
    unknown = "on" if rule.alert_on_unknown_value else "off"
    return f"Filter: {value_range}; unknown-value alerts {unknown}; details: /filters"


def _browser_status_lines(browser: object) -> list[str]:
    if not isinstance(browser, dict):
        return [
            "⚠️ Browser heartbeat: not received; "
            "open the reviewer tab with the extension enabled"
        ]
    phase = browser["phase"]
    lines = [f"Browser phase: {phase}; reviewer tabs: {browser['tab_count']}"]
    if browser.get("age_seconds", 0) > BROWSER_HEARTBEAT_STALE_SECONDS:
        lines.append(
            "⚠️ Browser heartbeat disconnected; check that Chrome and the extension are running"
        )
    elif not browser["reachable"] and phase != "waiting-verification":
        lines.append(
            "⚠️ No reviewer tab is responding; reopen the reviewer page or reload the extension"
        )
    if not browser["tab_count"]:
        lines.append("⚠️ Open the Walmart reviewer tab to receive item updates")
    if browser["refresh_minutes"] == 0:
        lines.append(
            "⚠️ Auto-refresh is disabled; enable it in the extension for regular item checks"
        )
    if phase == "waiting-verification":
        lines.append(
            "⚠️ Solve Walmart's verification in the reviewer tab; automation resumes automatically "
            "when the reviewer page is ready again"
        )
    elif phase == "waiting-user":
        lines.append("Scanning waits until you stop clicking or scrolling for 30 seconds")
    elif phase == "ingest-retry":
        lines.append("⚠️ Item delivery is retrying; check the latest relay error below")
    return lines


def _telegram_status_text(runtime: RuntimeStatus, store: SeenStore) -> str:
    snapshot = runtime.snapshot()
    lines = [
        "✅ Notifier process + Telegram: online",
        f"Uptime: {_duration(snapshot['uptime_seconds'])}",
    ]
    commands = Handler.telegram_commands
    if commands is not None:
        destination = commands.health().get("delivery_chat")
        if destination in ("private", "group"):
            lines.append(f"Alerts destination: {destination}")
    lines.extend(_browser_status_lines(snapshot.get("browser")))
    notification = snapshot.get("last_notification")
    if isinstance(notification, dict):
        lines.append(
            f"Last successful item notification: {notification['at']} "
            f"({_duration(notification.get('age_seconds'))} ago)"
        )
    else:
        lines.append("Last item notification: unknown (tracking begins with this version)")
    last = snapshot.get("last_successful_relay")
    if isinstance(last, dict):
        age = _duration(last.get("age_seconds"))
        lines.append(f"Last successful reviewer relay: {age} ago")
        summary = last.get("summary")
        if isinstance(summary, dict):
            lines.append(
                f"Latest ingest batch: {summary.get('seen', 0)} seen, "
                f"{summary.get('new', 0)} new, "
                f"{summary.get('matched', 0)} matched, "
                f"{summary.get('notified', 0)} alerted"
            )
            if not summary.get("notified"):
                seen, new = summary.get("seen", 0), summary.get("new", 0)
                if seen and not new and summary.get("duplicates", 0) == seen:
                    lines.append(
                        "Quiet because the latest batch contained "
                        "only already-seen items (duplicates)"
                    )
                elif new and not summary.get("matched") and summary.get("filtered", 0) == new:
                    lines.append(
                        "Quiet because all new items were filtered out; "
                        "use /filters to inspect the rules"
                    )
    else:
        lines.append("⚠️ No successful reviewer relay has been recorded yet")

    attempt = snapshot.get("last_relay_attempt")
    if isinstance(attempt, dict) and attempt.get("error_type"):
        lines.append(f"⚠️ Latest relay attempt failed ({attempt['error_type']})")
    elif isinstance(attempt, dict) and isinstance(attempt.get("summary"), dict):
        summary = attempt["summary"]
        if summary.get("failed") or summary.get("pending"):
            lines.append(
                f"⚠️ Latest attempt: {summary.get('failed', 0)} failed, "
                f"{summary.get('pending', 0)} pending"
            )

    lines.extend((_rule_status_text(), f"Recorded items: {len(store):,}"))
    if seed_mode():
        lines.append("⚠️ Seed mode is ON; item alerts are disabled")
    return "\n".join(lines)


class Handler(BaseHTTPRequestHandler):
    server_version = "ReviewerNotifier/2.1"

    # Injected by serve().
    store: SeenStore
    token: str
    # Built once. Rebuilding per request re-ran the env parsing, and on a
    # half-configured install logged the same failure on every single relay.
    notifier: object
    runtime_status: RuntimeStatus
    browser_diagnostics: BrowserDiagnosticsStore
    relay_settings: RelaySettingsStore
    scanner_settings: ScannerSettingsStore
    scanner_commands: ScannerCommandsStore
    scanner_status: ScannerStatusStore
    telegram_commands: TelegramCommandPoller | None = None

    # --- helpers ------------------------------------------------------------

    def _cors(self) -> None:
        # The extension's service worker has host permission for this origin, so
        # its fetch is exempt from CORS. Answering anyway costs nothing and
        # removes a whole class of silent, hard-to-read failures.
        origin = self.headers.get("Origin", "")
        if origin.startswith("chrome-extension://") or origin.startswith("moz-extension://"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Ingest-Token")
            self.send_header("Access-Control-Allow-Methods",
                             "GET, POST, PUT, DELETE, OPTIONS")
            # Compatibility with Chrome's older Private Network Access
            # preflight. Current Local Network Access uses a user permission,
            # but this harmless response keeps older builds working too.
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")

    def _reply(self, code: int, payload: dict | str) -> None:
        body = (json.dumps(payload) if isinstance(payload, dict) else payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type",
                         "application/json" if isinstance(payload, dict) else "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not self.token:
            return True
        return hmac.compare_digest(self.headers.get("X-Ingest-Token", ""), self.token)

    def _trusted_origin(self) -> bool:
        """Allow extension requests and command-line clients with no Origin.

        Loopback is not an authorization boundary by itself: a normal web page
        can submit a form to localhost. Mutating routes therefore reject web
        origins even when the optional ingest token is intentionally blank.
        """
        origin = self.headers.get("Origin", "")
        return not origin or origin.startswith(("chrome-extension://", "moz-extension://"))

    def _require_json(self) -> bool:
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if media_type == "application/json":
            return True
        self._reply(415, {"error": "Content-Type must be application/json"})
        return False

    def log_message(self, fmt: str, *args) -> None:
        # Route through logging instead of stderr, and drop the noisy default
        # per-request line -- the handlers below log what actually matters.
        log.debug(fmt, *args)

    # --- routes -------------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0].rstrip("/") or "/"
        scanner_paths = ("/scanner-settings", "/scanner-commands", "/scanner-status")
        protected = ("/", "/health", "/rules", "/browser-diagnostics", "/relay-settings",
                     *scanner_paths)
        if path in protected and not self._authorized():
            self._reply(403, "forbidden")
            return
        if path in scanner_paths:
            if not self._trusted_origin():
                self._reply(403, "forbidden origin")
                return
            self._read_scanner_control(path)
            return
        if path in ("/", "/health"):
            self._reply(200, {
                "status": "ok",
                "rules": [r.name for r in load_rules()],
                "notifier": type(self.notifier).__name__,
                "notifier_configured": type(self.notifier).__name__ != "NullNotifier",
                "notifier_diagnostic": self._notifier_diagnostic(),
                "discord": (
                    self.notifier.health() if isinstance(self.notifier, DiscordNotifier) else None
                ),
                "seed_mode": seed_mode(),
                "seen_items": len(self.store),
                "observed_values": self.store.observed_value_stats(),
                "runtime": self.runtime_status.snapshot(),
                "telegram_commands": (
                    self.telegram_commands.health()
                    if self.telegram_commands is not None
                    else disabled_command_health()
                ),
            })
            return

        if path == "/browser-diagnostics":
            self._reply(200, self.browser_diagnostics.snapshot())
            return

        if path == "/relay-settings":
            try:
                settings = self.relay_settings.read()
            except (TypeError, ValueError, RecursionError):
                self._reply(400, {"error": "Invalid relay-settings.json; using browser settings."})
                return
            except OSError:
                self._reply(500, {"error": "Could not read relay-settings.json."})
                return
            self._reply(200, {"settings": settings})
            return

        if path == "/rules":
            rules, source = rules_snapshot()
            self._reply(200, {
                "rules": rules_to_dicts(rules),
                "source": source,
            })
            return

        self._reply(404, "not found")

    def do_PUT(self) -> None:  # noqa: N802
        """Save rules edited in the extension's options page."""
        path = self.path.split("?")[0].rstrip("/")
        if path not in ("/rules", "/relay-settings", "/scanner-settings", "/scanner-commands"):
            self._reply(404, "not found")
            return
        if not self._authorized():
            self._reply(403, "forbidden")
            return
        if not self._trusted_origin():
            self._reply(403, "forbidden origin")
            return
        if not self._require_json():
            return

        if path in ("/scanner-settings", "/scanner-commands"):
            self._save_scanner_control(path)
            return

        if path == "/relay-settings":
            body = self._read_body(16 * 1024)
            if body is None:
                return
            try:
                settings = self.relay_settings.save(json.loads(body.decode("utf-8")))
            except (TypeError, ValueError, RecursionError):
                self._reply(400, {"error": "Invalid scan settings."})
                return
            except OSError:
                self._reply(500, {"error": "Could not save relay-settings.json."})
                return
            self._reply(200, {"settings": settings})
            return

        body = self._read_body()
        if body is None:
            return
        try:
            save_user_rules(json.loads(body.decode("utf-8")))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._reply(400, {"error": str(exc)})
            return
        except OSError as exc:
            log.exception("Could not save rules.")
            self._reply(500, {"error": str(exc)})
            return
        rules, source = rules_snapshot()
        self._reply(200, {"rules": rules_to_dicts(rules), "source": source})

    def do_DELETE(self) -> None:  # noqa: N802
        """Discard UI-saved rules and go back to the bundled defaults."""
        if self.path.split("?")[0].rstrip("/") != "/rules":
            self._reply(404, "not found")
            return
        if not self._authorized():
            self._reply(403, "forbidden")
            return
        if not self._trusted_origin():
            self._reply(403, "forbidden origin")
            return
        try:
            removed = clear_user_rules()
        except OSError as exc:
            self._reply(500, {"error": str(exc)})
            return
        rules, source = rules_snapshot()
        self._reply(200, {
            "removed": removed,
            "rules": rules_to_dicts(rules),
            "source": source,
        })

    def _read_body(self, maximum: int | None = None) -> bytes | None:
        """Read the request body, replying with an error and returning None."""
        if maximum is None:
            maximum = MAX_INGEST_BYTES
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reply(400, "bad content-length")
            return None
        if length < 0:
            self._reply(400, "bad content-length")
            return None
        if length > maximum:
            self._reply(413, "payload too large")
            return None
        return self.rfile.read(length) if length else b""

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if path not in ("/ingest", "/test-notification", "/bot-check", "/rescan", "/relay-status",
                        "/browser-diagnostics", "/scanner-status"):
            self._reply(404, "not found")
            return
        if not self._authorized():
            self._reply(403, "forbidden")
            return
        if not self._trusted_origin():
            self._reply(403, "forbidden origin")
            return

        if path == "/scanner-status":
            if self._require_json():
                self._save_scanner_control(path)
            return

        if path == "/relay-status":
            self._receive_browser_status()
            return

        if path == "/browser-diagnostics":
            self._receive_browser_diagnostics()
            return

        if path == "/test-notification":
            self._send_test_notification()
            return
        if path == "/bot-check":
            self._send_bot_check_alert()
            return
        if path == "/rescan":
            self._rescan()
            return

        if not self._require_json():
            self.runtime_status.record_failure("InvalidIngestContentType")
            return

        body = self._read_body()
        if body is None:
            self.runtime_status.record_failure("InvalidIngestRequest")
            return
        try:
            items = parse_json_ingest_payload(body, source="extension")
        except (TypeError, ValueError) as exc:
            self.runtime_status.record_failure("InvalidIngestPayload")
            log.warning("Rejected ingest payload: %s", exc)
            self._reply(400, {"error": str(exc)})
            return

        # Rules are re-read per request on purpose: editing src/rules.json
        # takes effect on the next relay, with no restart.
        try:
            if isinstance(self.notifier, DiscordNotifier):
                summary = self.notifier.ingest(items, seed_only=seed_mode())
                # Preserve local observation diagnostics without using personal
                # dedupe as an admission gate for the independent Discord queue.
                for item in items:
                    self.store.mark_seen(item.item_id, item.title, item.value_usd)
            else:
                summary = process(
                    items, load_rules(), self.store, self.notifier, seed_only=seed_mode()
                )
        except Exception as exc:
            self.runtime_status.record_failure(type(exc).__name__)
            if isinstance(self.notifier, DiscordNotifier):
                if isinstance(exc, ValueError):
                    self._reply(400, {"error": "invalid Discord product observation"})
                    return
                log.error("Discord intake failed (%s); retry required.", type(exc).__name__)
                self._reply(503, {"error": "durable intake unavailable; retry"})
                return
            log.exception("Ingest processing failed.")
            self._reply(500, {
                "error": "ingest processing failed",
                "error_type": type(exc).__name__,
            })
            return
        self.runtime_status.record_relay(summary.as_dict())
        # Append once per request rather than once per item, after the relay
        # marker is recorded so it lands in the same write. This costs the
        # number of new records; the snapshot is rewritten only on compaction.
        self.store.flush()
        if summary.notified or summary.failed:
            log.info("Ingest: %s", json.dumps(summary.as_dict()))
        else:
            log.debug("Ingest: %s", json.dumps(summary.as_dict()))
        self._reply(200, summary.as_dict())

    def _receive_browser_status(self) -> None:
        if not self._require_json():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            self.runtime_status.record_browser(json.loads(body.decode("utf-8")))
        except (TypeError, ValueError) as exc:
            self._reply(400, {"error": str(exc)})
            return
        # Heartbeats must not renew the item-relay silence watchdog or imply
        # successful item notifications merely because the browser is awake.
        self._reply(200, {"ok": True})

    def _scanner_control_store(self, path: str) -> tuple:
        if path == "/scanner-settings":
            return self.scanner_settings, "settings", MAX_SETTINGS_BYTES
        if path == "/scanner-commands":
            return self.scanner_commands, "commands", MAX_COMMANDS_BYTES
        return self.scanner_status, "status", MAX_STATUS_BYTES

    def _read_scanner_control(self, path: str) -> None:
        store, key, _ = self._scanner_control_store(path)
        try:
            payload = store.snapshot() if key == "status" else {key: store.read()}
        except (TypeError, ValueError, RecursionError):
            self._reply(400, {"error": f"Invalid scanner {key}."})
            return
        except OSError:
            self._reply(500, {"error": f"Could not read scanner {key}."})
            return
        self._reply(200, payload)

    def _save_scanner_control(self, path: str) -> None:
        store, key, maximum = self._scanner_control_store(path)
        body = self._read_body(maximum)
        if body is None:
            return
        try:
            incoming = parse_scanner_json(body)
            if key == "status":
                store.record_report(incoming)
                payload = {"ok": True}
            else:
                payload = {key: store.save(incoming)}
        except (TypeError, ValueError, RecursionError):
            self._reply(400, {"error": f"Invalid scanner {key}."})
            return
        except OSError:
            self._reply(500, {"error": f"Could not save scanner {key}."})
            return
        # Control and health traffic must never imply item relay/delivery or
        # reset its silence watchdog; only the scanner owns command execution.
        self._reply(200, payload)

    def _receive_browser_diagnostics(self) -> None:
        if not self._require_json():
            return
        body = self._read_body(128 * 1024)
        if body is None:
            return
        try:
            self.browser_diagnostics.record_report(json.loads(body.decode("utf-8")))
        except (TypeError, ValueError, RecursionError):
            self._reply(400, {"error": "Invalid browser diagnostic report."})
            return
        except OSError as exc:
            log.error("Could not persist browser diagnostics (%s).", type(exc).__name__)
            self._reply(500, {"error": "Could not persist browser diagnostics."})
            return
        # Diagnostic traffic must not reset relay staleness or send alerts.
        self._reply(200, {"ok": True})

    def _rescan(self) -> None:
        """Re-apply the current rules to already-recorded items.

        Dedupe means a recorded item is never judged again, so tightening or
        loosening a filter has no effect on anything already seen -- and once
        the whole catalogue is recorded, that is everything. This re-runs the
        rules over stored records.

        It reports by default and only alerts when explicitly asked, because
        a loosened filter can match thousands of recorded items at once.
        """
        apply = False
        limit = RESCAN_ALERT_LIMIT
        body = self._read_body()
        if body is None:
            return
        if body:
            try:
                data = json.loads(body.decode("utf-8"))
                apply = bool(data.get("apply", False))
                limit = int(data.get("limit", RESCAN_ALERT_LIMIT))
            except (AttributeError, TypeError, ValueError):
                self._reply(400, {"error": "body must be a JSON object"})
                return
        limit = max(0, min(limit, RESCAN_ALERT_LIMIT))

        if apply and isinstance(self.notifier, DiscordNotifier):
            self._reply(409, {
                "error": "Discord accepts fresh observations; historical rescans cannot publish."
            })
            return

        rules = load_rules()
        matches: list[tuple[str, dict]] = []
        for item_id, record in self.store.iter_records():
            value = record.get("value_usd")
            item = Item(
                item_id=item_id,
                title=str(record.get("title") or ""),
                value_usd=value if isinstance(value, (int, float)) else None,
            )
            if first_match(item, rules) is not None:
                matches.append((item_id, record))

        # Highest value first: if the alert budget is spent, spend it on the
        # items most worth knowing about.
        matches.sort(key=lambda pair: pair[1].get("value_usd") or 0, reverse=True)
        payload = {
            "ok": True,
            "applied": apply,
            "scanned": len(self.store),
            "matched": len(matches),
            "alert_limit": limit,
            "sample": [
                {"item_id": item_id, "title": record.get("title"),
                 "value_usd": record.get("value_usd")}
                for item_id, record in matches[:10]
            ],
        }

        if not apply:
            payload["note"] = (
                "Nothing was sent. POST {\"apply\": true} to alert on these."
            )
            self._reply(200, payload)
            return

        sent = 0
        failed = 0
        for item_id, record in matches[:limit]:
            value = record.get("value_usd")
            item = Item(
                item_id=item_id,
                title=str(record.get("title") or ""),
                value_usd=value if isinstance(value, (int, float)) else None,
                url=f"https://www.walmart.com/ip/{item_id.removeprefix('ip-')}",
                source="rescan",
            )
            try:
                delivered = self.notifier.send(item, priority="high")
            except Exception:
                log.exception("Rescan alert failed for %s.", item_id)
                delivered = False
            if delivered:
                sent += 1
            else:
                failed += 1
        payload["alerted"] = sent
        payload["failed"] = failed
        payload["suppressed"] = max(0, len(matches) - limit)
        log.info("Rescan alerted on %d of %d matching records.", sent, len(matches))
        self._reply(200, payload)

    def _notifier_diagnostic(self) -> str:
        """A notifier-owned message guaranteed not to contain its secrets."""
        diagnostic = getattr(self.notifier, "last_diagnostic", "")
        return diagnostic[:500] if isinstance(diagnostic, str) else ""

    def _send_bot_check_alert(self) -> None:
        """Tell the user Walmart showed a bot check and automation has paused.

        The message text is fixed here rather than taken from the request, so
        this route cannot be used to push arbitrary content. Only the pause
        length is read from the body, and it is clamped.
        """
        paused_minutes = 0
        body = self._read_body()
        if body is None:
            return
        if body:
            try:
                data = json.loads(body.decode("utf-8"))
                paused_minutes = int(data.get("paused_minutes", 0))
            except (AttributeError, TypeError, ValueError):
                paused_minutes = 0
        paused_minutes = max(0, min(paused_minutes, 7 * 24 * 60))
        pause = (f"Automation paused for {paused_minutes} min." if paused_minutes
                 else "Automation paused.")
        item = Item(
            item_id="bot-check",
            title=f"Walmart bot check: solve it by hand in the reviewer tab. {pause}",
            url="https://www.walmart.com/reviews/claim-product?q=",
            source="diagnostic",
        )
        try:
            delivered = self.notifier.send(item, priority="urgent")
        except Exception as exc:
            log.exception("Bot-check alert raised an unexpected error.")
            self._reply(503, {"ok": False, "error_type": type(exc).__name__})
            return
        log.warning("Walmart bot check reported by the extension; %s", pause)
        self._reply(200 if delivered else 503, {"ok": bool(delivered)})

    def _send_test_notification(self) -> None:
        """Exercise the configured notifier without filters or dedupe state."""
        notifier_name = type(self.notifier).__name__
        item = Item(
            item_id="notifier-test",
            title="TEST: Reviewer notifier is working",
            source="diagnostic",
        )
        try:
            delivered = self.notifier.send(item, priority="high")
        except Exception as exc:
            log.exception("Test notification raised an unexpected error.")
            self._reply(503, {
                "ok": False,
                "error": "test notification failed",
                "error_type": type(exc).__name__,
                "notifier": notifier_name,
            })
            return
        if not delivered:
            payload = {
                "ok": False,
                "error": "test notification delivery failed",
                "notifier": notifier_name,
            }
            if diagnostic := self._notifier_diagnostic():
                payload["detail"] = diagnostic
            self._reply(503, payload)
            return
        log.info("Test notification sent through %s.", notifier_name)
        payload = {
            "ok": True,
            "message": "test notification sent",
            "notifier": notifier_name,
        }
        if diagnostic := self._notifier_diagnostic():
            payload["detail"] = diagnostic
        self._reply(200, payload)


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    if normalized.lower().rstrip(".") == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _is_unspecified_host(host: str) -> bool:
    """Return whether *host* is an all-interfaces bind address."""
    normalized = host.strip()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        return ipaddress.ip_address(normalized).is_unspecified
    except ValueError:
        return False


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _require_safe_bind(
    host: str,
    token: str,
    *,
    container_loopback_only: bool = False,
) -> None:
    """Reject an unauthenticated network listener unless Compose contains it.

    A bridged container must listen on its wildcard interface for Docker's port
    proxy to reach it.  The supplied Compose file publishes that port only on
    the host's 127.0.0.1 and sets CONTAINER_LOOPBACK_ONLY to attest to that
    boundary.  The exception is deliberately limited to wildcard binds; it is
    not a general escape hatch for LAN addresses or hostnames.
    """
    compose_loopback_proxy = container_loopback_only and _is_unspecified_host(host)
    if not _is_loopback_host(host) and not token and not compose_loopback_proxy:
        raise ValueError(
            f"Refusing to bind to non-loopback host {host!r} without INGEST_TOKEN; "
            "set a non-empty INGEST_TOKEN, bind to 127.0.0.1, or use the supplied "
            "loopback-only Compose service"
        )


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          state_path: str | None = None) -> None:
    token = os.environ.get("INGEST_TOKEN", "").strip()
    container_loopback_only = _env_flag("CONTAINER_LOOPBACK_ONLY")
    _require_safe_bind(
        host,
        token,
        container_loopback_only=container_loopback_only,
    )
    container_managed = container_loopback_only and _is_unspecified_host(host)

    # Unbuffered-ish stdout so the banner and log lines appear immediately when
    # this is piped to a file or run as a scheduled task.
    with contextlib.suppress(AttributeError, OSError):
        sys.stdout.reconfigure(line_buffering=True)

    Handler.store = SeenStore(state_path)
    Handler.token = token
    Handler.notifier = build_notifier()
    Handler.runtime_status = RuntimeStatus(Handler.store)
    Handler.browser_diagnostics = BrowserDiagnosticsStore(
        Handler.store.path.with_name("browser-diagnostics.json")
        if Handler.store.path else ":memory:"
    )
    Handler.relay_settings = RelaySettingsStore(
        Handler.store.path.with_name("relay-settings.json")
        if Handler.store.path else ":memory:"
    )
    Handler.scanner_settings = ScannerSettingsStore(
        Handler.store.path.with_name("scanner-settings.json")
        if Handler.store.path else ":memory:"
    )
    Handler.scanner_commands = ScannerCommandsStore(
        Handler.store.path.with_name("scanner-commands.json")
        if Handler.store.path else ":memory:"
    )
    Handler.scanner_status = ScannerStatusStore(
        Handler.store.path.with_name("scanner-status.json")
        if Handler.store.path else ":memory:"
    )
    Handler.telegram_commands = None

    notifier = Handler.notifier
    if isinstance(notifier, DiscordNotifier):
        notifier.start(on_sent=Handler.runtime_status.record_notification)
    watchdog = RelayWatchdog.from_env(Handler.runtime_status, notifier, dict(os.environ))
    watchdog.start()
    rules = load_rules()

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True

    command_poller: TelegramCommandPoller | None = None
    if isinstance(notifier, TelegramNotifier):
        try:
            command_poller = TelegramCommandPoller(
                notifier,
                Handler.store,
                lambda: _telegram_status_text(Handler.runtime_status, Handler.store),
            )
            Handler.telegram_commands = command_poller
            command_poller.start()
        except Exception as exc:
            # Remote status is optional. Never make it a prerequisite for item
            # notifications or the local ingest endpoint.
            log.error(
                "Could not start Telegram status commands (%s); alerts remain enabled.",
                type(exc).__name__,
            )

    display_host = "127.0.0.1" if container_managed else host
    print(f"Reviewer notifier listening on http://{display_host}:{port}")
    print(f"  ingest endpoint : http://{display_host}:{port}/ingest")
    print(f"  state file      : {Handler.store.path or '(memory)'}  ({len(Handler.store)} seen)")
    print(f"  notifier        : {type(notifier).__name__}")
    print(f"  rules           : {', '.join(r.name for r in rules)}")
    print(f"  token required  : {'yes' if Handler.token else 'no'}")
    if container_managed:
        print("  host exposure   : Docker publishes this service on 127.0.0.1 only")
    diagnostic = getattr(notifier, "last_diagnostic", "")
    if isinstance(diagnostic, str) and diagnostic:
        print(f"  notifier note   : {diagnostic[:500]}")
    if command_poller is not None:
        print("  phone status    : send /status to the configured Telegram destination")
    if seed_mode():
        print("  SEED_MODE       : ON - recording items as seen, sending nothing")
    if type(notifier).__name__ == "NullNotifier":
        print("\n  WARNING: no push channel configured - nothing will reach your phone.")
        provider_name = (os.environ.get("NOTIFY_PROVIDER") or "ntfy").strip().lower()
        if provider_name == "telegram":
            print("  Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in notifier.env.")
        elif provider_name == "pushover":
            print("  Set PUSHOVER_TOKEN and PUSHOVER_USER_KEY in notifier.env.")
        else:
            print("  Set NTFY_TOPIC in notifier.env. Generate one with:")
            print('    python -c "import secrets; print(secrets.token_hex(16))"')
    if container_managed:
        print("\nManaged by Docker Desktop as reviewer-item-notifier.")
        print("Use Docker Desktop or `docker compose logs -f notifier` to view logs.")
    else:
        print("\nLeave this running. Ctrl-C to stop.")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        watchdog.stop()
        if isinstance(notifier, DiscordNotifier):
            notifier.stop()
        if command_poller is not None:
            command_poller.stop()
        Handler.store.close()
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Local reviewer-item notifier.")
    parser.add_argument("--host", default=os.environ.get("BIND_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument("--state", default=os.environ.get("STATE_PATH"),
                        help=f"dedupe file (default: {default_path()})")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    log_directory = Path(args.state).parent if args.state else default_path().parent
    file_handler = RollingLogHandler(
        log_directory / "notifier.log", max_bytes=2 * 1024 * 1024,
        total_bytes=16 * 1024 * 1024,
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    # Delivery events already have their own durable, sanitized JSONL archive.
    file_handler.addFilter(lambda record: record.name != "discord_bridge.monitoring")
    logging.getLogger().addHandler(file_handler)
    try:
        serve(args.host, args.port, args.state)
    finally:
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()


if __name__ == "__main__":
    main()
