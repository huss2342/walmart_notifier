"""Bounded personal alerts over the same rate-aware Discord REST transport."""

from __future__ import annotations

import math
import re
import sqlite3
import threading
import time
import uuid

from .api import DeliveryOutcome, DiscordError
from .messages import render_message
from .monitoring import emit_event, error_fields
from .request_store import RequestStore, member_ref


def _snowflake(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9]{17,20}", value) is not None


def _delay(value, default=5):
    return max(0.05, value) if type(value) in (int, float) and math.isfinite(value) else default


def render_request_message(job):
    item = job["item"]
    payload = render_message(
        item, first_seen=job["observed"], last_seen=job["observed"], route="pro-all"
    )
    queries = ", ".join(query.replace("@", "@\u200b") for query in job["queries"])
    payload["content"] = "Your requested item was found!"
    payload["embeds"][0]["description"] = (
        f"Matched requests: {queries}\nObserved: <t:{int(job['observed'])}:R>"
    )
    payload.update(nonce=job["nonce"], enforce_nonce=True)
    return payload


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

    def _handle_error(self, result, job, exc, *, now, submitted, phase):
        self._event(
            "request_provider_error",
            phase=phase,
            status=exc.status,
            provider_code=exc.provider_code,
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
                status=429,
                retry_after_seconds=delay,
                scope="global" if exc.global_limit else "route",
            )
        elif exc.status in {400, 401, 403, 404}:
            self.store.settle(job, "failed", now=now, reason=f"http_{exc.status}")
            result["failed"] += 1
            self._last_error = "provider_rejected"
            self._event("dispatch_failed", reason=f"http_{exc.status}", phase=phase)
        else:
            delay = min(60, 2 ** min(job["attempts"] + 2, 6))
            self.store.retry(job, now=now, delay=delay)
            result["retried"] += 1
            self._last_error = "provider_unavailable"
            self._event("dispatch_retry", reason="remote_retry", retry_after_seconds=delay,
                        phase=phase, **error_fields(exc))

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
            fresh = self.store.validate(job, now=current())
            if fresh is None or self.stop_event.is_set():
                self.store.settle(job, "cancelled", now=current(), reason="no_current_match")
                result["skipped"] += 1
                self._event("dispatch_skipped", phase=phase, reason="no_current_match")
                return result
            channel = fresh.get("channel_id")
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
            fresh = self.store.validate(job, now=current())
            if fresh is None or self.stop_event.is_set():
                self.store.settle(job, "cancelled", now=current(), reason="no_current_match")
                result["skipped"] += 1
                self._event("dispatch_skipped", phase=phase, reason="no_current_match")
                return result
            phase = "begin_send"
            committed = self.store.begin_send(fresh, now=current(), channel_id=channel)
            if committed is None:
                result["skipped"] += 1
                self._event("dispatch_skipped", phase=phase, reason="lease_changed")
                return result
            self._active_job = committed
            phase = "render"
            payload = render_request_message(committed)
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
                self._event("dispatch_sent", items=1, messages=1,
                            elapsed_seconds=time.monotonic() - started)
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
                self.stop_event.wait(min(30, 2**failures) if failures else 1)
            self._event("worker_lifecycle", transition="stopped", phase="worker")

        self._thread = threading.Thread(target=run, name="discord-requests", daemon=True)
        self._thread.start()

    def stop(self):
        self.stop_event.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=12)
