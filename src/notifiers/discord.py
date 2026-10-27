"""Collector adapter for the private, durable Discord feeds.

Intake deliberately bypasses personal rules and SeenStore: those belong to the
old single-destination notifier. A successful intake means queued, not sent.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from discord_bridge.api import DiscordClient, DiscordError
from discord_bridge.classification import classify_product
from discord_bridge.core import JobStore, normalize_item
from discord_bridge.monitoring import DeliveryMonitor, emit_event
from discord_bridge.service import deliver_due

from models import Item
from pipeline import RunSummary

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "data" / "discord_bridge"


@dataclass(slots=True)
class DiscordSummary(RunSummary):
    queued: int = 0

    def as_dict(self):
        return {**RunSummary.as_dict(self), "queued": self.queued}


def project_item(item: Item) -> dict:
    """Drop quota, raw payloads, search terms and all account-specific fields."""
    classification = classify_product(
        item.title,
        category_hint=item.category,
        search_query=item.raw.get("query", ""),
    )
    return normalize_item(
        {
            "item_id": item.item_id,
            "title": re.sub(r"\s+", " ", item.title).strip()[:2000],
            "value_usd": item.value_usd,
            "url": item.url,
            "image_url": item.image_url,
            "category": classification.category,
            "available": item.raw.get("available", True),
        }
    )


class DiscordNotifier:
    def __init__(
        self,
        store,
        *,
        client=None,
        state=None,
        live=False,
        auditor=None,
        welcome_enabled=False,
        welcome_path=None,
        membership_enabled=False,
        membership_path=None,
        requests_enabled=False,
        requests_token=None,
    ):
        self.store = store
        self.client = client
        self.state = state or {}
        self.live = live
        self.auditor = auditor
        self.last_diagnostic = (
            "Discord delivery enabled." if live else "Discord preview: sending OFF."
        )
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._last_delivery = None
        self._lock = threading.Lock()
        self.monitor = DeliveryMonitor(self.store.path.parent, entry_point="collector_worker")
        self.welcome = None
        if live and welcome_enabled:
            from discord_bridge.welcome import WelcomeWorker

            self.welcome = WelcomeWorker(
                self.client,
                self.state,
                welcome_path or self.store.path.parent / "welcome.sqlite3",
                enabled=True,
                auditor=self.auditor,
                stop_event=self._stop,
                paid_due=self._paid_due,
                event_sink=self.monitor.emit,
            )
        self.membership = None
        if live and membership_enabled:
            from discord_bridge.membership import MembershipWorker

            self.membership = MembershipWorker(
                self.client,
                self.state,
                membership_path or self.store.path.parent / "membership.sqlite3",
                enabled=True,
                auditor=self.auditor,
                stop_event=self._stop,
                paid_due=self._paid_due,
                event_sink=self.monitor.emit,
            )
        self.requests = None
        if live and requests_enabled:
            from discord_bridge.request_runtime import RequestRuntime

            self.requests = RequestRuntime(
                self.client,
                self.state,
                self.store.path,
                token=requests_token or "",
                auditor=self.auditor,
                stop_event=self._stop,
                event_sink=self.monitor.emit,
            )

    def _paid_due(self):
        return self.store.has_due_paid(now=time.time())

    @classmethod
    def from_env(cls):
        from discord_bridge.__main__ import load_settings, read_state

        live = os.environ.get("DISCORD_LIVE", "false").strip().lower() == "true"
        if os.environ.get("SEED_MODE", "false").strip().lower() in {"true", "1", "yes"}:
            live = False
        client = None
        state = {}
        if live:
            load_settings()
            client = DiscordClient(os.environ.get("DISCORD_BOT_TOKEN", ""))
            state = read_state(Path(os.environ.get("DISCORD_STATE_PATH", PRIVATE / "server.json")))
        store = JobStore(os.environ.get("DISCORD_QUEUE_PATH", PRIVATE / "collector.sqlite3"))
        return cls(
            store,
            client=client,
            state=state,
            live=live,
            welcome_enabled=os.environ.get("DISCORD_WELCOME_ENABLED", "false").strip().lower()
            == "true",
            welcome_path=os.environ.get("DISCORD_WELCOME_PATH"),
            membership_enabled=os.environ.get("DISCORD_MEMBERSHIP_ENABLED", "false").strip().lower()
            == "true",
            membership_path=os.environ.get("DISCORD_MEMBERSHIP_PATH"),
            requests_enabled=os.environ.get("DISCORD_REQUESTS_ENABLED", "false").strip().lower()
            == "true",
            requests_token=os.environ.get("DISCORD_BOT_TOKEN", ""),
        )

    def ingest(self, items: list[Item], *, seed_only=False) -> DiscordSummary:
        # Validate the whole public projection before queueing any prefix.
        public = [project_item(item) for item in items]
        summary = DiscordSummary()
        for item in items:
            summary.observe(item)
        if not public:
            return summary
        result = self.store.record(public, seed_only=seed_only)
        self.monitor.intake(result, seed_only=seed_only)
        self._wake.set()
        summary.new = result["new"]
        summary.duplicates = result["duplicates"]
        summary.seeded = result["new"] if seed_only else 0
        summary.queued = result["queued"]
        summary.matched = summary.queued
        for item in public:
            log.debug(
                "Discord observation id=%s value_cents=%s category=%s available=%s",
                item["item_id"],
                item["value_cents"],
                item["category"],
                item["available"],
            )
        return summary

    def deliver_once(self):
        self.monitor.heartbeat(self.store)
        try:
            result = deliver_due(
                self.store,
                self.client,
                self.state,
                live=self.live,
                auditor=self.auditor,
                stop_event=self._stop,
                event_sink=self.monitor.emit,
            )
        finally:
            self.monitor.heartbeat(self.store)
        with self._lock:
            self._last_delivery = result
            if result["blocked"]:
                self.last_diagnostic = (
                    "Discord sending blocked by the privacy audit; queued items retained."
                )
            elif self.live:
                self.last_diagnostic = "Discord delivery enabled."
        return result

    def start(self, *, on_sent=None):
        if not self.live or self._thread is not None:
            return

        def work():
            while not self._stop.is_set():
                self._wake.clear()
                wait_seconds = 5
                try:
                    result = self.deliver_once()
                    wait_seconds = result["wait_seconds"]
                    if result["sent"] and on_sent is not None:
                        on_sent()
                except Exception as exc:
                    with self._lock:
                        self.last_diagnostic = "Discord worker error; queued items retained."
                    emit_event(
                        self.monitor.emit,
                        "worker_error",
                        phase="worker",
                        error_type=type(exc).__name__,
                    )
                self._wake.wait(min(5, max(0.01, wait_seconds)))

        self._thread = threading.Thread(target=work, name="discord-delivery", daemon=True)
        self._thread.start()
        if self.welcome is not None:
            self.welcome.start()
        if self.membership is not None:
            self.membership.start()
        if self.requests is not None:
            self.requests.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self.welcome is not None:
            self.welcome.stop()
        if self.membership is not None:
            self.membership.stop()
        if self.requests is not None:
            self.requests.stop()
        if self._thread is not None:
            self._thread.join(timeout=12)
        if (
            (self._thread is None or not self._thread.is_alive())
            and (
                self.welcome is None
                or self.welcome._thread is None
                or not self.welcome._thread.is_alive()
            )
            and (
                self.membership is None
                or self.membership._thread is None
                or not self.membership._thread.is_alive()
            )
            and (self.requests is None or not self.requests.is_alive())
        ):
            close = getattr(self.client, "close", None)
            if close:
                close()

    def health(self):
        with self._lock:
            last_delivery = self._last_delivery
        return {
            "live_sending": self.live,
            "jobs": self.store.counts(),
            "last_delivery": last_delivery,
            "personal_filters_applied": False,
            "requests": self.requests.health() if self.requests is not None else {"enabled": False},
            "queue": self.store.delivery_metrics(now=time.time()),
            "monitoring": self.monitor.status(),
            "welcome": self.welcome.health()
            if self.welcome is not None
            else {"enabled": False, "configured": False},
            "membership": self.membership.health()
            if self.membership is not None
            else {"enabled": False, "configured": False},
        }

    def send(self, item: Item, priority="normal") -> bool:
        """System notices only; product listings must go through durable intake."""
        if (
            not self.live
            or item.source != "diagnostic"
            or item.item_id not in {"notifier-test", "bot-check", "relay-stale"}
        ):
            return False
        from discord_bridge.provision import audit

        try:
            assessment = (self.auditor or audit)(
                self.client, self.state.get("guild_id"), self.state
            )
            channel = self.state.get("channels", {}).get("collector-health", "")
            if assessment.get("ok") is not True or not re.fullmatch(r"\d{17,20}", channel):
                return False
            response = self.client.request(
                "POST",
                f"/channels/{channel}/messages",
                {
                    "allowed_mentions": {"parse": []},
                    "content": item.title[:1900],
                },
            )
            return isinstance(response, dict) and bool(
                re.fullmatch(r"\d{17,20}", str(response.get("id", "")))
            )
        except (DiscordError, ValueError, KeyError, TypeError):
            return False
