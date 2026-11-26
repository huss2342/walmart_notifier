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
from discord_bridge.delivery_watchdog import STALL_SECONDS, DeliveryWatchdog
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
        # Optional components that failed to build or start: name -> error type.
        # None of them may stop channel alerts (see _optional).
        self.component_errors = {}
        self._pending_failures = []
        self._on_sent = None
        # Old sent dispatch envelopes (most of the queue file) are pruned off the
        # hot path; items and jobs, which dedupe relies on, are kept.
        self.pruner = self._optional(
            "pruner", lambda: CollectorPruner(self.store.path, stop_event=self._stop)
        ) if live else None
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
        for name, error_type in self._pending_failures:
            emit_event(self.monitor.emit, "component_failed", component=name,
                       phase="construct", error_type=error_type)
        self._pending_failures = None
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
            self.elite_signal = self._optional(
                "elite-signal", lambda: EliteSignal.from_env(self.monitor.emit)
            )
        self._api_events = None
        if live and isinstance(self.client, DiscordClient):
            # Every Discord REST call is logged (delivery.jsonl, event
            # discord_api) through a background writer.
            self._api_events = self._optional(
                "log-writer", lambda: AsyncEventSink(self.monitor.emit)
            )
            if self._api_events is not None:
                self.client.observer = self._api_events
        self.welcome = None
        if live and welcome_enabled:
            def welcome_worker():
                from discord_bridge.welcome import WelcomeWorker

                return WelcomeWorker(
                    self.client,
                    self.state,
                    welcome_path or self.store.path.parent / "welcome.sqlite3",
                    enabled=True,
                    auditor=self.audit_guard,
                    stop_event=self._stop,
                    paid_due=self._paid_due,
                    event_sink=self.monitor.emit,
                )

            self.welcome = self._optional("welcome", welcome_worker)
        self.membership = None
        if live and membership_enabled:
            def membership_worker():
                from discord_bridge.membership import MembershipWorker

                return MembershipWorker(
                    self.client,
                    self.state,
                    membership_path or self.store.path.parent / "membership.sqlite3",
                    enabled=True,
                    auditor=self.audit_guard,
                    stop_event=self._stop,
                    paid_due=self._paid_due,
                    event_sink=self.monitor.emit,
                )

            self.membership = self._optional("membership", membership_worker)
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
            except Exception as exc:
                # Referral setup errors (bad config, Whop key, billing database
                # copy) cannot disable the existing item feeds.
                self._referral_error = "configuration"
                emit_event(self.monitor.emit, "referral_error", component="referrals",
                           phase="prepare",
                           **error_fields(exc))
        if self.membership is not None:
            self.membership.referrals_enabled = self.referrals is not None
        self.requests = None
        if live and requests_enabled:
            def request_runtime():
                from discord_bridge.request_runtime import RequestRuntime

                return RequestRuntime(
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

            self.requests = self._optional("request-runtime", request_runtime)
        # Owner alarm when due channel alerts stop going out (5 minutes), and
        # revival of the sender thread; started with delivery.
        self.watchdog = None
        if live:
            self.watchdog = self._optional("watchdog", lambda: DeliveryWatchdog(
                self.store,
                event_sink=self.monitor.emit,
                stall_seconds=max(60.0, _env_seconds("DISCORD_STALL_SECONDS", STALL_SECONDS)),
                notify=self._notify_stall,
                revive=self._revive_delivery,
                heartbeat=lambda: self.monitor.heartbeat(self.store),
                stop_event=self._stop,
            ))

    def _optional(self, name, factory):
        """Build one optional component; a failure disables only that component."""
        try:
            return factory()
        except Exception as exc:
            self.component_errors[name] = type(exc).__name__
            log.error("Discord %s disabled (%s); channel alerts continue.",
                      name, type(exc).__name__)
            if self._pending_failures is not None:  # monitor not built yet
                self._pending_failures.append((name, type(exc).__name__))
            else:
                emit_event(self.monitor.emit, "component_failed", component=name,
                           phase="construct", **error_fields(exc))
            return None

    def _guarded(self, name, phase, call, *args, **kwargs):
        """Run an optional step; report and swallow its failure."""
        try:
            return call(*args, **kwargs)
        except Exception as exc:
            self._report_failure(name, phase, exc)
            return None

    def _report_failure(self, name, phase, exc):
        self.component_errors[name] = type(exc).__name__
        log.error("Discord %s failed during %s (%s); channel alerts continue.",
                  name, phase, type(exc).__name__)
        emit_event(self.monitor.emit, "component_failed", component=name, phase=phase,
                   **error_fields(exc))

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
        back_again_error = None
        if os.environ.get("DISCORD_BACK_AGAIN_ENABLED", "true").strip().lower() != "false":
            try:
                from back_again import BackAgain

                # Next to the queue database (the fast container volume).
                back_again = BackAgain(store.path.parent / "back-again.json", store.path)
            except Exception as exc:
                # Comeback confirmation is optional; every other alert still flows.
                back_again_error = type(exc).__name__
                log.error("Back-again disabled (%s); channel alerts continue.", back_again_error)
        notifier = cls(
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
        if back_again_error is not None:
            notifier.component_errors["back-again"] = back_again_error
            emit_event(notifier.monitor.emit, "component_failed", component="back-again",
                       phase="construct", error_type=back_again_error)
        return notifier

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
            try:
                held, confirmed, summary.back_again = back_again.plan(public, flagged)
            except Exception as exc:
                # A fault in comeback bookkeeping must not refuse the batch:
                # record it as a plain sighting (extension restock flags apply).
                self._report_failure("back-again", "plan", exc)
                back_again, held, confirmed = None, set(), set()
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
            # The auto-claim hand-off is not channel alert logic: if it fails,
            # intake goes on with the plain sink.
            captured = self._guarded("elite-signal", "intake", self.elite_signal.capture, sink)
            if captured is not None:
                sink, routed = captured
        result = self.store.record(
            public, seed_only=seed_only, request_event_sink=self.monitor.emit,
            event_sink=sink, search_queries=searches, restocked=restocked,
            restock_any_value=confirmed,
        )
        # Committed: from here on nothing may turn this intake into an error.
        self._wake.set()
        if back_again is not None:
            self._guarded("back-again", "settle", back_again.settle, confirmed, announced)
        if routed:
            # Hand off before Discord delivery; the signal thread never blocks intake.
            with suppress(Exception):
                self.elite_signal.offer(public, routed)
        with suppress(Exception):
            self.monitor.intake(result, seed_only=seed_only, items=public,
                                searched=len(searches))
        if self.requests is not None and not seed_only:
            self._guarded("request-runtime", "intake", self.requests.wake)
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
        # The queue heartbeat (about 15 queries) runs on the watchdog thread
        # when that runs, so it never delays a send.
        inline_heartbeat = self.watchdog is None or not self.watchdog.is_alive()
        if inline_heartbeat:
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
            if inline_heartbeat:
                self.monitor.heartbeat(self.store)
        try:
            privacy = self.audit_guard.status()
        except Exception:
            privacy = {"findings_count": 0}
        with self._lock:
            self._last_delivery = result
            if self.live and privacy.get("findings_count"):
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
        self._on_sent = on_sent
        # Channel delivery first; every other component starts afterwards and
        # on its own, so none of them can keep alerts from going out.
        self._start_delivery_thread()
        for name, component in (
            ("watchdog", self.watchdog),
            ("welcome", self.welcome),
            ("membership", self.membership),
            ("request-runtime", self.requests),
            ("referrals", self.referrals),
            ("pruner", self.pruner),
        ):
            if component is not None:
                self._guarded(name, "start", component.start)

    def _start_delivery_thread(self):
        self._thread = threading.Thread(
            target=self._delivery_loop, name="discord-delivery", daemon=True
        )
        self._thread.start()

    def _revive_delivery(self):
        """Watchdog hook: restart the sender thread if it died. True if restarted."""
        thread = self._thread
        if self._stop.is_set() or thread is None or thread.is_alive():
            return False
        self._start_delivery_thread()
        emit_event(self.monitor.emit, "worker_lifecycle", component="channel",
                   transition="restarted")
        return True

    def _notify_stall(self, *, due_items, oldest_due_age_seconds, last_sent_age_seconds):
        """One private notice per stall to the owner's collector-health channel."""
        since = (
            f"{last_sent_age_seconds / 60:.0f} min ago" if last_sent_age_seconds is not None
            else "not since the queue began"
        )
        item = Item(
            item_id="delivery-stalled",
            title=(
                f"Channel alerts stalled: {due_items} due, oldest waiting "
                f"{oldest_due_age_seconds / 60:.0f} min; last channel post {since}. "
                "Queued alerts are kept ($80+ for 3 h). See /health discord.watchdog."
            ),
            url="https://discord.com",
            source="diagnostic",
        )
        return self.send(item, priority="high")

    def _delivery_loop(self):
        on_sent = self._on_sent
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
                    # Status bookkeeping only; never a delivery failure.
                    self._guarded("runtime-status", "settle", on_sent)
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

    def stop(self):
        self._stop.set()
        self._wake.set()
        for name, component in (
            ("watchdog", self.watchdog),
            ("welcome", self.welcome),
            ("membership", self.membership),
            ("request-runtime", self.requests),
            ("referrals", self.referrals),
        ):
            if component is not None:
                self._guarded(name, "stop", component.stop)
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
        def part(component, read, fallback):
            # One failing optional component never breaks /health.
            if component is None:
                return fallback
            try:
                return read()
            except Exception as exc:
                return {**fallback, "health_error": type(exc).__name__}

        disabled = {"enabled": False, "configured": False}
        rate_budget = getattr(self.client, "priority_status", None)
        return {
            "live_sending": self.live,
            "worker": worker,
            "jobs": self.store.counts(),
            "last_delivery": last_delivery,
            "personal_filters_applied": False,
            "requests": part(self.requests, lambda: self.requests.health(), {"enabled": False}),
            "referrals": part(self.referrals, lambda: self.referrals.health(), {
                "enabled": bool(self._referral_error),
                "configured": False,
                "last_error": self._referral_error,
            }) if self.referrals is not None else {
                "enabled": bool(self._referral_error),
                "configured": False,
                "last_error": self._referral_error,
            },
            "queue": self.store.delivery_metrics(now=time.time()),
            "monitoring": {
                **self.monitor.status(),
                "api_events_dropped": self._api_events.dropped if self._api_events else 0,
            },
            "welcome": part(self.welcome, lambda: self.welcome.health(), disabled),
            "membership": part(self.membership, lambda: self.membership.health(), disabled),
            "back_again": part(self.back_again, lambda: self.back_again.health(),
                               {"enabled": False}),
            "pruning": part(self.pruner, lambda: self.pruner.health(), {"enabled": False}),
            # Advisory permission audit: current findings, never a send gate.
            "privacy": part(self.audit_guard, lambda: self.audit_guard.status(), {}),
            # Owner alarm: due channel alerts unsent for DISCORD_STALL_SECONDS.
            "watchdog": part(self.watchdog, lambda: self.watchdog.status(), {"enabled": False}),
            # Optional components that failed and were switched off.
            "components": {"failed": dict(self.component_errors)},
            # Channel sends own the Discord budget; background gets a share.
            "rate_budget": part(rate_budget, rate_budget, {}) if callable(rate_budget) else {},
        }

    def send(self, item: Item, priority="normal") -> bool:
        """System notices only; product listings must go through durable intake."""
        if (
            not self.live
            or item.source != "diagnostic"
            or item.item_id not in {"notifier-test", "bot-check", "relay-stale",
                                    "delivery-stalled"}
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
