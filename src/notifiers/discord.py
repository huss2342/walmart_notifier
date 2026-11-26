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
from contextlib import nullcontext, suppress
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from discord_bridge.api import DiscordClient, DiscordError
from discord_bridge.audit_guard import AuditGuard
from discord_bridge.classification import classify_product
from discord_bridge.core import JobStore, normalize_item
from discord_bridge.elite_signal import EliteSignal
from discord_bridge.monitoring import (
    AsyncEventSink,
    DeliveryMonitor,
    emit_event,
    error_fields,
)
from discord_bridge.service import deliver_due

from collector_prune import CollectorPruner
from models import Item
from pipeline import RunSummary

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "data" / "discord_bridge"


@dataclass(slots=True)
class DiscordSummary(RunSummary):
    queued: int = 0
    # Observations dropped because they cannot be published, as
    # (position in the request, item_id or None, field, reason) labels.
    invalid: list[tuple[int, str | None, str, str]] = field(default_factory=list)
    # $80+ offers back after an hour unseen, held until a second sighting: the
    # extension re-reads (and re-sends) their page about 10 s later.
    confirming: int = 0
    # Back-again receipts for the operations log: (stage, item_id, seconds).
    back_again: list[tuple[str, str, float]] = field(default_factory=list)

    def as_dict(self):
        return {**RunSummary.as_dict(self), "queued": self.queued, "invalid": len(self.invalid),
                "confirming": self.confirming}


def _env_seconds(name, default):
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if 0 <= value <= 86400 else default


_ITEM_ID = re.compile(r"ip-\d{6,20}")
_ITEM_PATH = re.compile(r"/ip/(?:[^/?#]+/)?(\d{6,20})/?")
# normalize_item's fixed messages (never the offending value) -> (field, reason).
_INVALID = (
    ("item must be an object", "item", "not_object"),
    ("invalid item_id", "item_id", "missing_or_too_long"),
    ("item_id must be ip-", "item_id", "not_walmart_id"),
    ("invalid title", "title", "empty_or_control_character"),
    ("supply value_cents or value_usd", "value", "conflicting_fields"),
    ("value must be numeric", "value", "not_numeric"),
    ("invalid retail value", "value", "out_of_range"),
    ("retail value must use whole cents", "value", "fractional_cents"),
    ("only USD", "currency", "not_usd"),
    ("available must be a boolean", "available", "not_boolean"),
    ("invalid product_url", "product_url", "missing_or_too_long"),
    ("product_url must be", "product_url", "not_matching_item"),
)


def invalid_observation(item: Item, exc: ValueError) -> tuple[str | None, str, str]:
    """Name what made an observation unpublishable: (item_id, field, reason).

    Fixed labels only, safe for logs: never the title, URL or value itself. The
    item_id is kept only when it is a well-formed public Walmart id.
    """
    message = str(exc)
    item_id = item.item_id if _ITEM_ID.fullmatch(str(item.item_id)) else None
    name, reason = next(
        ((name, reason) for prefix, name, reason in _INVALID if message.startswith(prefix)),
        ("unknown", "other"),
    )
    if name == "title":
        title = re.sub(r"\s+", " ", item.title or "").strip()
        reason = "empty" if not title else "control_character"
    elif reason == "not_matching_item":
        parts = urlsplit(item.url or "")
        match = _ITEM_PATH.fullmatch(parts.path)
        reason = (
            "foreign_host" if parts.scheme != "https" or parts.netloc != "www.walmart.com"
            else "not_item_path" if not match
            else "different_item"
        )
    return item_id, name, reason


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
        referrals_enabled=False,
        referrals_config_path=None,
        private_dir=None,
        elite_signal=None,
        back_again=None,
    ):
        self.store = store
        # $80+ offers that come back after vanishing from every list (None: off).
        self.back_again = back_again
        # Logs and referral config may live apart from the databases (for
        # example databases on a fast container volume, logs on the host).
        private = Path(private_dir) if private_dir else self.store.path.parent
        self.client = client
        self.state = state or {}
        self.live = live
        self.last_diagnostic = (
            "Discord delivery enabled." if live else "Discord preview: sending OFF."
        )
        self._stop = threading.Event()
        self._wake = threading.Event()
        # Old sent dispatch envelopes (most of the queue file) are pruned off the
        # hot path; items and jobs, which dedupe relies on, are kept.
        self.pruner = CollectorPruner(self.store.path, stop_event=self._stop) if live else None
        self._thread = None
        self._last_delivery = None
        self._lock = threading.Lock()
        self._worker_status = {
            "last_poll_at": None,
            "last_poll_status": None,
            "poll_started_at": None,
            "last_success": None,
            "last_failure_at": None,
            "last_error": None,
            "recovered_at": None,
        }
        self._poll_started_monotonic = None
        # A live bot writes its receipts from a background thread, so intake,
        # dispatches and Discord calls never wait on the (slow) log disk.
        self.monitor = DeliveryMonitor(
            private, entry_point="collector_worker",
            background=live and isinstance(client, DiscordClient),
        )
        # Discord roles and channel overwrites are the access control. The
        # permission audit only warns the owner (privacy_warning, /health
        # discord.privacy) and never blocks any worker: on 2026-11-25 a
        # fail-closed audit stopped every alert for ~80 min. One guard serves
        # every worker, caching pass, findings and errors alike for the TTL;
        # a live bot audits on its own thread, so no send waits on it.
        self.audit_ttl = max(60.0, _env_seconds("DISCORD_AUDIT_TTL_SECONDS", 300))
        self.audit_guard = AuditGuard.wrap(
            auditor,
            ttl=self.audit_ttl,
            event_sink=self.monitor.emit,
            background=live and isinstance(client, DiscordClient),
        )
        # New/restocked in-stock items also go to the owner's auto-claim service
        # (off unless ELITE_SIGNAL_URL/KEY are set). Posting runs on its own thread.
        self.elite_signal = elite_signal
        if self.elite_signal is None and live:
            self.elite_signal = EliteSignal.from_env(self.monitor.emit)
        self._api_events = None
        if live and isinstance(self.client, DiscordClient):
            # Every Discord REST call is logged (delivery.jsonl, event
            # discord_api) through a background writer.
            self._api_events = AsyncEventSink(self.monitor.emit)
            self.client.observer = self._api_events
        self.welcome = None
        if live and welcome_enabled:
            from discord_bridge.welcome import WelcomeWorker

            self.welcome = WelcomeWorker(
                self.client,
                self.state,
                welcome_path or self.store.path.parent / "welcome.sqlite3",
                enabled=True,
                auditor=self.audit_guard,
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
                auditor=self.audit_guard,
                stop_event=self._stop,
                paid_due=self._paid_due,
                event_sink=self.monitor.emit,
            )
        self.referrals = None
        self._referral_error = None
        if live and referrals_enabled:
            try:
                if not requests_enabled or not requests_token:
                    raise ValueError("Referrals require the existing interaction gateway")
                from discord_bridge.referral_runtime import ReferralRuntime

                self.referrals = ReferralRuntime(
                    self.state,
                    referrals_config_path or private / "referral-config.json",
                    event_sink=self.monitor.emit,
                    discord_client=self.client,
                )
            except (ValueError, OSError) as exc:
                # Referral setup errors cannot disable the existing item feeds.
                self._referral_error = "configuration"
                emit_event(self.monitor.emit, "referral_error", component="referrals",
                           phase="prepare",
                           **error_fields(exc))
        if self.membership is not None:
            self.membership.referrals_enabled = self.referrals is not None
        self.requests = None
        if live and requests_enabled:
            from discord_bridge.request_runtime import RequestRuntime

            self.requests = RequestRuntime(
                self.client,
                self.state,
                self.store.path,
                token=requests_token or "",
                auditor=self.audit_guard,
                stop_event=self._stop,
                event_sink=self.monitor.emit,
                release_hook=self._wake.set,
                **(
                    {"referral_handler": self.referrals.handler}
                    if self.referrals is not None
                    else {}
                ),
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
        back_again = None
        if os.environ.get("DISCORD_BACK_AGAIN_ENABLED", "true").strip().lower() != "false":
            from back_again import BackAgain

            # Next to the queue database (the fast container volume).
            back_again = BackAgain(store.path.parent / "back-again.json", store.path)
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
            referrals_enabled=os.environ.get("DISCORD_REFERRALS_ENABLED", "false").strip().lower()
            == "true",
            referrals_config_path=os.environ.get("DISCORD_REFERRALS_CONFIG"),
            private_dir=os.environ.get("DISCORD_PRIVATE_DIR"),
            back_again=back_again,
        )

    def ingest(self, items: list[Item], *, seed_only=False) -> DiscordSummary:
        # Validate the whole public projection before queueing anything. An
        # observation that cannot be published (one glitched item: no title, a
        # canonical URL of another item, fractional cents) is dropped, counted in
        # the reply and named in the logs; the rest of the page is still queued.
        # Before Nov 19 one such item failed its whole page with a bare 400.
        summary = DiscordSummary()
        public, accepted = [], []
        for position, item in enumerate(items):
            summary.observe(item)
            try:
                public.append(project_item(item))
            except ValueError as exc:
                item_id, name, reason = invalid_observation(item, exc)
                summary.invalid.append((position, item_id, name, reason))
                log.warning(
                    "Dropped an unpublishable Discord product observation: "
                    "item_id=%s field=%s reason=%s",
                    item_id or "malformed", name, reason,
                )
                continue
            accepted.append(item)
        if not public:
            return summary
        back_again = self.back_again if not seed_only else None
        # The whole decide -> record -> settle step for comebacks is one unit, so
        # two intakes of the same offer can neither both hold nor both confirm it.
        with back_again.lock if back_again is not None else nullcontext():
            return self._record(summary, public, accepted, seed_only, back_again)

    def _record(self, summary, public, accepted, seed_only, back_again):
        # The extension flags an $80+ offer it saw out of stock, now back.
        flagged = {
            row["item_id"]
            for row, item in zip(public, accepted, strict=True)
            if item.raw.get("restocked") is True
        }
        confirmed = set()
        if back_again is not None:
            # An $80+ offer unseen for an hour is held until a second sighting
            # 9+ s later, which is then recorded as a restock ("Back in stock").
            held, confirmed, summary.back_again = back_again.plan(public, flagged)
            if held:
                summary.confirming = len(held)
                kept = [(row, item) for row, item in zip(public, accepted, strict=True)
                        if row["item_id"] not in held]
                public, accepted = [row for row, _ in kept], [item for _, item in kept]
                if not public:
                    return summary
        # Which Walmart search surfaced each item (requests/keyword lanes), so a
        # member's own search results can match even without every title word.
        searches = {
            row["item_id"]: item.raw["query"]
            for row, item in zip(public, accepted, strict=True)
            if isinstance(item.raw.get("query"), str) and 0 < len(item.raw["query"]) <= 200
        }
        restocked = flagged | confirmed
        sink, routed, announced = self.monitor.emit, None, set()
        if back_again is not None:
            # Every restock the queue routes (either path) starts the comeback cooldown.
            def sink(event, _emit=self.monitor.emit, **fields):
                if event == "observation_routed" and fields.get("transition") == "restocked":
                    announced.update(fields.get("item_ids") or ())
                return _emit(event, **fields)
        if self.elite_signal is not None and not seed_only:
            sink, routed = self.elite_signal.capture(sink)
        result = self.store.record(
            public, seed_only=seed_only, request_event_sink=self.monitor.emit,
            event_sink=sink, search_queries=searches, restocked=restocked,
            restock_any_value=confirmed,
        )
        if back_again is not None:
            back_again.settle(confirmed, announced)
        if routed:
            # Hand off before Discord delivery; the signal thread never blocks intake.
            with suppress(Exception):
                self.elite_signal.offer(public, routed)
        self.monitor.intake(result, seed_only=seed_only, items=public, searched=len(searches))
        self._wake.set()
        if self.requests is not None and not seed_only:
            self.requests.wake()
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
                auditor=self.audit_guard,
                stop_event=self._stop,
                event_sink=self.monitor.emit,
            )
        finally:
            self.monitor.heartbeat(self.store)
        privacy = self.audit_guard.status()
        with self._lock:
            self._last_delivery = result
            if self.live and privacy["findings_count"]:
                self.last_diagnostic = (
                    "Discord delivery enabled; privacy audit warning "
                    "(see /health discord.privacy)."
                )
            elif self.live:
                self.last_diagnostic = "Discord delivery enabled."
        return result

    def start(self, *, on_sent=None):
        if not self.live or self._thread is not None:
            return

        def work():
            emit_event(self.monitor.emit, "worker_lifecycle", component="channel",
                       transition="started")
            while not self._stop.is_set():
                self._wake.clear()
                wait_seconds = 5
                with self._lock:
                    self._worker_status["poll_started_at"] = time.time()
                    self._poll_started_monotonic = time.monotonic()
                try:
                    result = self.deliver_once()
                    wait_seconds = result["wait_seconds"]
                    if result["sent"]:
                        with self._lock:
                            self._worker_status["last_success"] = time.time()
                    if result["sent"] and on_sent is not None:
                        on_sent()
                    recovered = False
                    with self._lock:
                        checked_at = time.time()
                        self._worker_status["last_poll_status"] = (
                            "blocked" if result["blocked"] else "ok"
                        )
                        if not result["blocked"]:
                            if self._worker_status["last_error"] is not None:
                                self._worker_status["recovered_at"] = checked_at
                                recovered = True
                            self._worker_status["last_error"] = None
                    if recovered:
                        emit_event(self.monitor.emit, "worker_recovered", component="channel",
                                   transition="recovered")
                except Exception as exc:
                    with self._lock:
                        self.last_diagnostic = "Discord worker error; queued items retained."
                        self._worker_status.update(
                            last_poll_status="error",
                            last_failure_at=time.time(),
                            last_error=type(exc).__name__,
                        )
                    emit_event(
                        self.monitor.emit,
                        "worker_error",
                        phase="worker",
                        component="channel",
                        **error_fields(exc),
                    )
                finally:
                    with self._lock:
                        self._worker_status["last_poll_at"] = time.time()
                        self._poll_started_monotonic = None
                self._wake.wait(min(5, max(0.01, wait_seconds)))
            emit_event(self.monitor.emit, "worker_lifecycle", component="channel",
                       transition="stopped")

        self._thread = threading.Thread(target=work, name="discord-delivery", daemon=True)
        self._thread.start()
        if self.welcome is not None:
            self.welcome.start()
        if self.membership is not None:
            self.membership.start()
        if self.requests is not None:
            self.requests.start()
        if self.referrals is not None:
            self.referrals.start()
        if self.pruner is not None:
            self.pruner.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self.welcome is not None:
            self.welcome.stop()
        if self.membership is not None:
            self.membership.stop()
        if self.requests is not None:
            self.requests.stop()
        if self.referrals is not None:
            self.referrals.stop()
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
        if self.back_again is not None:
            self.back_again.flush()  # The coverage heartbeat, so a restart is no outage.
        self.monitor.flush()

    def health(self):
        with self._lock:
            last_delivery = self._last_delivery
            worker = dict(self._worker_status)
            in_progress = self._poll_started_monotonic is not None
            worker.update(
                enabled=self.live,
                running=self._thread is not None and self._thread.is_alive(),
                in_progress=in_progress,
                in_progress_seconds=max(0, time.monotonic() - self._poll_started_monotonic)
                if in_progress
                else 0,
            )
        return {
            "live_sending": self.live,
            "worker": worker,
            "jobs": self.store.counts(),
            "last_delivery": last_delivery,
            "personal_filters_applied": False,
            "requests": self.requests.health() if self.requests is not None else {"enabled": False},
            "referrals": self.referrals.health()
            if self.referrals is not None
            else {
                "enabled": bool(self._referral_error),
                "configured": False,
                "last_error": self._referral_error,
            },
            "queue": self.store.delivery_metrics(now=time.time()),
            "monitoring": {
                **self.monitor.status(),
                "api_events_dropped": self._api_events.dropped if self._api_events else 0,
            },
            "welcome": self.welcome.health()
            if self.welcome is not None
            else {"enabled": False, "configured": False},
            "membership": self.membership.health()
            if self.membership is not None
            else {"enabled": False, "configured": False},
            "back_again": self.back_again.health()
            if self.back_again is not None
            else {"enabled": False},
            "pruning": self.pruner.health() if self.pruner is not None else {"enabled": False},
            # Advisory permission audit: current findings, never a send gate.
            "privacy": self.audit_guard.status(),
        }

    def send(self, item: Item, priority="normal") -> bool:
        """System notices only; product listings must go through durable intake."""
        if (
            not self.live
            or item.source != "diagnostic"
            or item.item_id not in {"notifier-test", "bot-check", "relay-stale"}
        ):
            return False
        try:
            # Advisory only; collector-health is the owner's private channel.
            self.audit_guard(
                self.client, self.state.get("guild_id"), self.state, caller="other"
            )
            channel = self.state.get("channels", {}).get("collector-health", "")
            if not re.fullmatch(r"\d{17,20}", channel):
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
