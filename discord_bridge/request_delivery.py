"""Bounded personal alerts over the same rate-aware Discord REST transport."""

from __future__ import annotations

import math
import re
import sqlite3
import threading
import time
import uuid

from .api import DeliveryOutcome, DiscordError
from .monitoring import emit_event, error_fields
from .payload_limits import payload_problems
from .request_render import product_link, render_bundle_message, render_request_message
from .request_store import RequestStore, member_ref

__all__ = ["RequestWorker", "render_bundle_message", "render_request_message"]

# Parked-DM reconciliation: at most one history read per interval while idle,
# over a window starting this long before the claim (clock-skew margin).
RECONCILE_INTERVAL_SECONDS = 60
RECONCILE_SLACK_SECONDS = 15 * 60
RECONCILE_WINDOW = 100
_DISCORD_EPOCH_MS = 1_420_070_400_000
_LINK = re.compile(r"\]\((https://[^)\s]+)\)")


def _snowflake(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9]{17,20}", value) is not None


def _snowflake_at(timestamp):
    """The smallest Discord ID at this Unix time, or None if it has none."""
    if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
        return None
    value = str((int(timestamp * 1000) - _DISCORD_EPOCH_MS) << 22)
    return value if _snowflake(value) else None


def _delay(value, default=5):
    return max(0.05, value) if type(value) in (int, float) and math.isfinite(value) else default


def _size_rejected(exc):
    """Discord refused the body itself: 413, or 400 Invalid Form Body (50035),
    which also reports embed-size errors. The same body must never be resent."""
    return exc.status == 413 or (exc.status == 400 and exc.provider_code == 50035)


class RequestWorker:
    """At most one member/product per cycle, with no public fallback.

    An optional startup/readiness auditor can block sends. Live Pro membership
    is always fetched immediately before the product POST; cached celebration
    state and the Gateway interaction's roles never authorize this worker.
    """

    def __init__(
        self, client, state, store, *, enabled=False, auditor=None, stop_event=None,
        event_sink=None,
        trial_store=None,
    ):
        self.client, self.state, self.store = client, state, store
        self.enabled, self.auditor, self.event_sink = bool(enabled), auditor, event_sink
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.guild = state.get("guild_id")
        self.pro = state.get("roles", {}).get("pro")
        self.trial_store = trial_store
        self._thread = None
        self._wake = threading.Event()
        self._cycle_lock = threading.Lock()
        self._last_error = None
        self._last_success = None
        self._last_poll_at = None
        self._last_poll_status = None
        self._last_failure_at = None
        self._recovered_at = None
        self._storage_failed = False
        self._cycle_failed = False
        self._active_job = None
        self._correlation_id = uuid.uuid4().hex
        self._next_summary = 0
        self._next_reconcile = 0
        if self.enabled:
            if not _snowflake(self.guild) or not _snowflake(self.pro) or self.guild == self.pro:
                raise ValueError(
                    "Personal alerts require the configured Discord server and Pro role."
                )
            # Verify persisted ownership even if the caller reopened without a
            # guild argument. No store mutation can move watches across guilds.
            RequestStore(self.store.path, guild_id=self.guild)

    def _event(self, name, **fields):
        context = {"component": "personal", "tier": "pro", "audience": "personal",
                   "correlation_id": self._correlation_id}
        if self._active_job is not None:
            job = self._active_job
            context.update(
                dispatch_id=job["id"],
                member_ref=member_ref(job["user_id"]),
                item_ids=[job["item_id"]],
                attempt=job["attempts"],
            )
        emit_event(self.event_sink, name, **(context | fields))

    def _member(self, user_id):
        result = self.client.request("GET", f"/guilds/{self.guild}/members/{user_id}")
        if not isinstance(result, dict):
            raise DiscordError("Invalid Discord member response")
        user, roles = result.get("user"), result.get("roles")
        if (
            not isinstance(user, dict)
            or user.get("id") != user_id
            or user.get("bot", False) is not False
            or not isinstance(roles, list)
            or not all(_snowflake(role) for role in roles)
        ):
            raise DiscordError("Invalid Discord member response")
        trial = self.state.get("roles", {}).get("trial")
        return self.pro in roles or (
            trial is not None and trial in roles and self.trial_store is not None
            and self.trial_store.active(user_id)
        )

    def _access_lost(self, job, now):
        if self.trial_store is None:
            self.store.revoke_user(job["user_id"])
        else:
            # Owner extensions and destructive watch cleanup must share the trial
            # service's lock. This sender only cancels its current unsent job;
            # the service makes the fresh, serialized decision about saved watches.
            self.store.settle(job, "cancelled", now=now, reason="pro_access_lost")

    def _failure(self, exc, *, phase, submitted=False, now=None, elapsed=0):
        self._cycle_failed = True
        self._last_error = "worker_error"
        self._last_failure_at = time.time() if now is None else now
        if isinstance(exc, sqlite3.Error):
            self._storage_failed = True
            self._last_poll_status = "error"
        self._event(
            "worker_error",
            phase=phase,
            ambiguous=submitted,
            elapsed_seconds=elapsed,
            **error_fields(exc),
        )

    def _poll_succeeded(self, now):
        self._last_poll_at = now
        self._last_poll_status = "ok"
        if self._storage_failed:
            self._recovered_at = now
            self._storage_failed = False
            # Idle storage recovery says nothing about a provider/access error.
            if self._last_error == "worker_error":
                self._last_error = None
            self._event("worker_recovered", transition="recovered", phase="claim")

    def _too_large(self, result, job, *, now, phase, reason, exc=None):
        """Split an oversized bundle into smaller continuation DMs; fail a single item.

        Called before any POST (local validator) or after a definite rejection.
        Either way the rejected body is never submitted again.
        """
        outcome = self.store.split(job, now=now, reason=reason)
        details = error_fields(exc) if exc is not None else {}
        self._last_error = "payload_too_large"
        if outcome == "split":
            result["retried"] += 1
            self._event("dispatch_retry", reason="payload_too_large", phase=phase, **details)
        elif outcome == "failed":
            result["failed"] += 1
            self._event("dispatch_failed", reason=reason, phase=phase, **details)
        else:
            result["skipped"] += 1
            self._last_error = "lease_changed"
            self._event("dispatch_skipped", reason="lease_changed", phase=phase)

    def _handle_error(self, result, job, exc, *, now, submitted, phase):
        self._event(
            "request_provider_error",
            phase=phase,
            ambiguous=submitted and exc.outcome.may_have_been_accepted,
            **error_fields(exc),
        )
        limited = exc.status == 429
        if submitted and exc.outcome.may_have_been_accepted:
            self.store.settle(job, "review", now=now, reason="ambiguous_send")
            self._last_error = "ambiguous_send"
            result["review"] += 1
            self._event("dispatch_skipped", reason="ambiguous_send", ambiguous=True, review=1)
            return
        if phase == "send" and _size_rejected(exc):
            # Before 2026-11-19 a 413 was retried with the identical body until
            # the alerts expired. A bundle now splits; a single item fails.
            self._too_large(
                result, job, now=now, phase=phase, exc=exc,
                reason="payload_too_large" if exc.status == 413 else f"http_{exc.status}",
            )
            return
        if phase in {"member", "audit"} and not limited:
            # Provider/auth/access errors do not prove the member lost Pro.
            # Keep the job bounded by its existing expiry and verify again.
            self.store.retry(job, now=now, delay=30, reason="membership_unverified")
            self._last_error = "membership_unverified"
            result["retried"] += 1
            return
        if exc.status == 404 and (
            not submitted or (phase == "send" and exc.outcome == DeliveryOutcome.REJECTED)
        ):
            # A member endpoint 404 is handled closer to that endpoint. A DM
            # channel 404 is a stale destination, never permission to go public.
            # Rejected message POSTs must also pause the cached destination;
            # only an explicit DM test can make it ready again.
            self.store.set_dm_status(job["user_id"], "needs_test")
            self.store.settle(job, "failed", now=now, reason="destination_missing")
            self._last_error = "destination_missing"
            result["blocked"] += 1
            self._event("dispatch_failed", reason="destination_missing", phase=phase)
        elif exc.status == 403 and (submitted or exc.provider_code == 50007):
            self.store.set_dm_status(job["user_id"], "blocked")
            self.store.settle(job, "failed", now=now, reason="dm_blocked")
            self._last_error = "dm_blocked"
            result["blocked"] += 1
            self._event("dispatch_failed", reason="dm_blocked", phase=phase)
        elif limited:
            delay = _delay(exc.retry_after)
            self.store.retry(job, now=now, delay=delay, reason="rate_limited")
            result["retried"] += 1
            result["throttled"] += 1
            self._last_error = "rate_limited"
            self._event(
                "dispatch_throttled",
                scope="global" if exc.global_limit else "route",
                **error_fields(exc, status=429, retry_after_seconds=delay),
            )
        elif exc.status in {400, 401, 403, 404, 413}:
            self.store.settle(job, "failed", now=now, reason=f"http_{exc.status}")
            result["failed"] += 1
            self._last_error = "provider_rejected"
            self._event("dispatch_failed", reason=f"http_{exc.status}", phase=phase)
        else:
            delay = min(60, 2 ** min(job["attempts"] + 2, 6))
            self.store.retry(job, now=now, delay=delay)
            result["retried"] += 1
            self._last_error = "provider_unavailable"
            self._event("dispatch_retry", reason="remote_retry", phase=phase,
                        **error_fields(exc, retry_after_seconds=delay))

    def _still_due(self, result, job, *, now, phase):
        """Revalidate before the next provider step; None when this cycle ends here.

        Matches that no longer apply are cancelled one by one and the rest go
        out. A shutdown hands the still-valid DM back instead of cancelling it.
        """
        fresh = self.store.validate(job, now=now)
        if fresh is None:
            self.store.settle(job, "cancelled", now=now, reason="no_current_match")
            result["skipped"] += 1
            self._event("dispatch_skipped", phase=phase, reason="no_current_match")
            return None
        if self.stop_event.is_set():
            self.store.retry(fresh, now=now, delay=0, reason="stopping")
            result["retried"] += 1
            self._event("dispatch_retry", phase=phase, reason="stopping")
            return None
        self._active_job = fresh
        return fresh

    def _own_message(self, message):
        author = message.get("author") if isinstance(message, dict) else None
        if not isinstance(author, dict):
            return False
        bot = self.state.get("bot_user_id")
        return author.get("id") == bot if _snowflake(bot) else author.get("bot") is True

    @staticmethod
    def _carries(message, items):
        """True when a DM links any of these items (Claim button or product link)."""
        urls = set()
        for row in message.get("components") or ():
            for button in (row.get("components") if isinstance(row, dict) else None) or ():
                if isinstance(button, dict) and isinstance(button.get("url"), str):
                    urls.add(button["url"])
        for embed in message.get("embeds") or ():
            if not isinstance(embed, dict):
                continue
            if isinstance(embed.get("url"), str):
                urls.add(embed["url"])
            urls.update(_LINK.findall(str(embed.get("description") or "")))
        for item in items:
            wanted = {item.get("review_url"), item.get("product_url")}
            if isinstance(item.get("item_id"), str):
                wanted.add(product_link(item))
            if urls & (wanted - {None}):
                return True
        return False

    def _reconcile(self, now):
        """Settle at most one parked possibly-sent DM from its DM channel history.

        One bounded GET of the messages posted since the DM was claimed: the
        bot's own DM linking one of its items means it was delivered; a
        complete window without one means it was not (failed, re-armed by the
        next in-stock sighting, never resent straight away). A full window,
        an unreadable channel or a non-bot author leaves it parked.
        """
        reader = getattr(self.client, "channel_messages", None)
        if reader is None or now < self._next_reconcile or self.stop_event.is_set():
            return
        self._next_reconcile = now + RECONCILE_INTERVAL_SECONDS
        candidate = self.store.reconcile_candidate(now=now)
        if candidate is None:
            return
        after = _snowflake_at(candidate["since"] - RECONCILE_SLACK_SECONDS)
        try:
            messages = reader(candidate["channel_id"], limit=RECONCILE_WINDOW, after=after)
        except DiscordError as exc:
            if exc.status not in {403, 404}:
                return  # Transient: the next idle window reads again.
            messages = None
        found = next(
            (
                message for message in messages or ()
                if self._own_message(message) and self._carries(message, candidate["items"])
            ),
            None,
        )
        if found is not None:
            outcome = "sent"
        elif messages is not None and len(messages) < RECONCILE_WINDOW:
            outcome = "undelivered"
        else:
            outcome = "unresolved"
        message_id = found.get("id") if found is not None else None
        settled = self.store.reconcile(
            candidate, outcome, message_id=message_id if _snowflake(message_id) else None
        )
        self._event(
            "dispatch_skipped",
            phase="reconcile",
            reason={"sent": "reconciled_sent", "undelivered": "reconciled_undelivered"}.get(
                outcome, "reconcile_unresolved"
            ),
            member_ref=member_ref(candidate["user_id"]),
            reconciled=settled,
        )

    def tick(self, now=None):
        result = {
            "sent": 0,
            "retried": 0,
            "failed": 0,
            "blocked": 0,
            "throttled": 0,
            "review": 0,
            "skipped": 0,
        }
        if (
            not self.enabled
            or self.stop_event.is_set()
            or not self._cycle_lock.acquire(blocking=False)
        ):
            return result
        job = None
        submitted = False
        phase = "audit"
        self._active_job = None
        self._cycle_failed = False
        self._correlation_id = uuid.uuid4().hex
        started = time.monotonic()

        def current():
            return time.time() if now is None else now

        try:
            phase = "claim"
            job = self.store.claim(now=current())
            self._poll_succeeded(current())
            self._active_job = job
            if job is None:
                try:
                    self._reconcile(current())
                except Exception as exc:
                    # Parked rows stay parked; never a send, never a failed cycle.
                    self._event("worker_error", phase="reconcile", **error_fields(exc))
                return result
            self._event("dispatch_claimed", phase="claim")
            phase = "audit"
            if self.auditor is not None:
                assessment = self.auditor(self.client, self.guild, self.state)
                if not isinstance(assessment, dict) or assessment.get("ok") is not True:
                    self.store.retry(job, now=current(), delay=30, reason="privacy_audit")
                    self._last_error = "privacy_audit"
                    result["blocked"] += 1
                    self._event("privacy_blocked", phase=phase, reason="privacy_audit")
                    return result
            # Reject known former members before even opening a private channel.
            phase = "member"
            try:
                pro = self._member(job["user_id"])
            except DiscordError as exc:
                if exc.status == 404 and exc.provider_code == 10007:
                    self._access_lost(job, current())
                    self._event("dispatch_skipped", phase=phase, reason="pro_access_lost")
                    result["blocked"] += 1
                    return result
                raise
            if not pro:
                self._access_lost(job, current())
                self._event("dispatch_skipped", phase=phase, reason="pro_access_lost")
                result["blocked"] += 1
                return result
            phase = "validate"
            job = self._still_due(result, job, now=current(), phase=phase)
            if job is None:
                return result
            channel = job.get("channel_id")
            if not channel:
                phase = "channel"
                response = self.client.request(
                    "POST", "/users/@me/channels", {"recipient_id": job["user_id"]}
                )
                channel = response.get("id") if isinstance(response, dict) else None
                channel_type = response.get("type") if isinstance(response, dict) else None
                recipients = response.get("recipients") if isinstance(response, dict) else None
                if (
                    not _snowflake(channel)
                    or type(channel_type) is not int
                    or channel_type != 1
                    or (
                        recipients is not None
                        and (
                            not isinstance(recipients, list)
                            or len(recipients) != 1
                            or not isinstance(recipients[0], dict)
                            or recipients[0].get("id") != job["user_id"]
                        )
                    )
                ):
                    raise DiscordError("Invalid Discord DM response")
                self.store.set_dm_status(job["user_id"], "ready", channel_id=channel)
            # Only the exact live role can authorize product contents. Opening a
            # channel or a slow transport must not leave a stale role snapshot.
            phase = "member"
            try:
                pro = self._member(job["user_id"])
            except DiscordError as exc:
                if exc.status == 404 and exc.provider_code == 10007:
                    self._access_lost(job, current())
                    self._event("dispatch_skipped", phase=phase, reason="pro_access_lost")
                    result["blocked"] += 1
                    return result
                raise
            if not pro:
                self._access_lost(job, current())
                self._event("dispatch_skipped", phase=phase, reason="pro_access_lost")
                result["blocked"] += 1
                return result
            phase = "validate"
            job = self._still_due(result, job, now=current(), phase=phase)
            if job is None:
                return result
            phase = "begin_send"
            committed = self.store.begin_send(job, now=current(), channel_id=channel)
            if committed is None:
                result["skipped"] += 1
                self._event("dispatch_skipped", phase=phase, reason="lease_changed")
                return result
            self._active_job = committed
            phase = "render"
            payload = render_request_message(committed)
            if payload_problems(payload):
                # Nothing was submitted; send smaller parts instead of a body
                # Discord would refuse.
                self._too_large(
                    result, committed, now=current(), phase=phase, reason="payload_too_large"
                )
                return result
            submitted = True
            phase = "send"
            self._event("dispatch_sending", phase=phase)
            response = self.client.request("POST", f"/channels/{channel}/messages", payload)
            message_id = response.get("id") if isinstance(response, dict) else None
            if not _snowflake(message_id):
                raise DiscordError("Invalid Discord message response", ambiguous=True)
            phase = "settle"
            settled = self.store.settle(job, "sent", now=current(), message_id=message_id)
            if settled:
                result["sent"] += 1
                self._last_error = None
                self._last_success = current()
                self._event("dispatch_sent", items=1 + len(committed.get("bundle") or ()),
                            messages=1, elapsed_seconds=time.monotonic() - started)
            else:
                result["review"] += 1
                self._last_error = "lease_changed"
                self._event("dispatch_skipped", phase=phase, reason="lease_changed", review=1)
            return result
        except DiscordError as exc:
            if job is not None:
                try:
                    self._handle_error(
                        result, job, exc, now=current(), submitted=submitted, phase=phase
                    )
                except Exception as recovery_error:
                    self._failure(
                        recovery_error,
                        phase="recovery",
                        submitted=submitted,
                        now=current(),
                        elapsed=time.monotonic() - started,
                    )
            return result
        except Exception as exc:
            # A programmer/serialization failure must not get swallowed into a
            # fresh send. Before submission a safe retry is possible; afterward
            # the persisted sending state needs manual reconciliation.
            # Record the original boundary before another storage operation can
            # fail. A failed recovery leaves the durable lease/sending state;
            # it must never fabricate a successful retry or another product POST.
            self._failure(
                exc,
                phase=phase,
                submitted=submitted,
                now=current(),
                elapsed=time.monotonic() - started,
            )
            try:
                if job is not None:
                    if submitted:
                        self.store.settle(
                            job, "review", now=current(), reason="unexpected_send_error"
                        )
                        result["review"] += 1
                    else:
                        self.store.retry(job, now=current(), delay=30, reason="worker_error")
                        result["retried"] += 1
            except Exception as recovery_error:
                self._failure(
                    recovery_error,
                    phase="recovery",
                    submitted=submitted,
                    now=current(),
                    elapsed=time.monotonic() - started,
                )
            return result
        finally:
            try:
                self._active_job = None
                if current() >= self._next_summary:
                    self._next_summary = current() + 60
                    try:
                        self._event("request_queue_summary", queue=self.store.stats(now=current()))
                    except Exception as exc:
                        self._failure(
                            exc,
                            phase="queue_summary",
                            now=current(),
                            elapsed=time.monotonic() - started,
                        )
            finally:
                self._cycle_lock.release()

    def health(self):
        try:
            queue = self.store.stats()
            queue_error = None
        except Exception as exc:
            # Missing metrics mean unknown queue state, never zero backlog.
            queue, queue_error = {}, error_fields(exc)
        return {
            "enabled": self.enabled,
            "running": bool(self._thread and self._thread.is_alive()),
            "last_success": self._last_success,
            "last_error": self._last_error,
            "last_poll_at": self._last_poll_at,
            "last_poll_status": self._last_poll_status,
            "last_failure_at": self._last_failure_at,
            "recovered_at": self._recovered_at,
            "queue": queue,
            "queue_available": queue_error is None,
            "queue_error": queue_error,
        }

    def start(self):
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return

        def run():
            self._event("worker_lifecycle", transition="started", phase="worker")
            failures = 0
            while not self.stop_event.is_set():
                self._cycle_failed = False
                try:
                    self.tick()
                except Exception as exc:
                    # Last containment boundary, including failures outside the
                    # normal cycle. Preserve durable state and remain stoppable.
                    self._failure(exc, phase="loop")
                failures = min(5, failures + 1) if self._cycle_failed else 0
                # One alert/second is a bounded share of the shared pool. Paid
                # channel backlog never absolutely starves personal Pro alerts.
                # A new match wakes the idle poll early; failures keep backoff.
                if failures:
                    self.stop_event.wait(min(30, 2**failures))
                else:
                    self._wake.wait(1)
                    self._wake.clear()
            self._event("worker_lifecycle", transition="stopped", phase="worker")

        self._thread = threading.Thread(target=run, name="discord-requests", daemon=True)
        self._thread.start()

    def wake(self):
        """Hint that intake may have queued an alert; the claim stays authoritative."""
        self._wake.set()

    def stop(self):
        self.stop_event.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=12)
