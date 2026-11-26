"""Bounded channel-parallel delivery of durable, immutable burst envelopes."""

from __future__ import annotations

import math
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress

from .api import DiscordError, RateLimitSource, retry_local_contention
from .audit_guard import AuditGuard
from .monitoring import emit_event, error_fields
from .policy import FREE_ROUTE_KEY, HIGH_VALUE_MIN_CENTS, RETIRED_ROUTE_KEYS

# Seconds of background-budget priority claimed per check while alerts are due.
PRIORITY_SECONDS = 10.0


def _high_value(batch):
    """An $80+ product alert: kept through outages until its (longer) expiry."""
    return batch["route"] != FREE_ROUTE_KEY and any(
        (job["item"].get("value_cents") or 0) >= HIGH_VALUE_MIN_CENTS for job in batch["jobs"]
    )


class _AuditClient:
    def __init__(self, client, stopping):
        self.client, self.stopping = client, stopping

    def request(self, *args, **kwargs):
        if self.stopping.is_set():
            raise DiscordError("Delivery stopping")
        return retry_local_contention(
            self.client.request, *args, stop_event=self.stopping, **kwargs
        )


def deliver_due(
    store,
    client,
    state,
    *,
    live=False,
    now=None,
    auditor=None,
    limit=20,
    concurrency=4,
    stop_event=None,
    event_sink=None,
    correlation_id=None,
):
    """Send up to `limit` messages; counters distinguish items from messages.

    No fill delay, timer backdating or provider-limit bypass. The executor
    never holds more than one active envelope per channel.

    The privacy audit is advisory (audit_guard.py): findings and audit errors
    emit ``privacy_warning`` and sending continues. Pass a shared AuditGuard
    as ``auditor`` to cache it across calls; a plain auditor (or None) gets a
    guard for this call, so it runs at most once per call.
    """
    result = {
        "sent": 0,
        "messages": 0,
        "retried": 0,
        "failed": 0,
        "skipped": 0,
        "blocked": False,
        "throttled": 0,
        "audit_seconds": 0.0,
        "elapsed_seconds": 0.0,
        "wait_seconds": 5.0,
    }
    stopping = stop_event if stop_event is not None else threading.Event()
    correlation_id = correlation_id or uuid.uuid4().hex

    def current():
        return time.time() if now is None else now

    def next_wait():
        checked_at = current()
        audience = "paid" if store.has_due_paid(now=checked_at) else "any"
        return store.next_due_in(now=checked_at, audience=audience)

    def defer_learned_routes():
        hint = getattr(client, "cooldown_delay", None)
        if hint is None:
            return
        for route, channel in state.get("channels", {}).items():
            if route in RETIRED_ROUTE_KEYS:
                continue
            if not isinstance(channel, str) or not re.fullmatch(r"\d{17,20}", channel):
                continue
            wait = hint("POST", f"/channels/{channel}/messages")
            if isinstance(wait, (int, float)) and math.isfinite(wait) and wait > 0:
                # These are the client's observed provider/global gates. Move
                # scheduling to their deadline before spending a full audit;
                # the actual HTTP reservation still rechecks every limit.
                store.defer_route(route, until=current() + wait)

    def claim_priority():
        # Background Discord callers shrink to a small share while alerts are due.
        prioritize = getattr(client, "prioritize", None)
        if callable(prioritize):
            with suppress(Exception):  # scheduling hint only
                prioritize(PRIORITY_SECONDS)

    def event(name, batch=None, **fields):
        if batch is not None:
            jobs = batch["jobs"]
            delays = [max(0, current() - job["due"]) for job in jobs]
            route = batch["route"]
            fields.update(
                dispatch_id=batch["id"],
                route=route,
                items=len(jobs),
                tier="free"
                if route == "yesterday-recap"
                else "starter"
                if route == "starter-items"
                else "pro",
                job_ids=[job["id"] for job in jobs],
                item_ids=[job["item_id"] for job in jobs],
                value_cents=[job["item"].get("value_cents") for job in jobs],
                attempt=max(job["attempts"] for job in jobs),
                due_delay_seconds_min=min(delays),
                due_delay_seconds_max=max(delays),
                due_at_min=min(job["due"] for job in jobs),
                due_at_max=max(job["due"] for job in jobs),
                next_attempt_at_min=min(job["next_attempt"] for job in jobs),
                next_attempt_at_max=max(job["next_attempt"] for job in jobs),
            )
        emit_event(event_sink, name, component="channel", correlation_id=correlation_id, **fields)

    if not live or stopping.is_set():
        return result
    if not 1 <= concurrency <= 4 or not 1 <= limit <= 100:
        raise ValueError("invalid delivery bounds")
    started = time.monotonic()
    defer_learned_routes()
    # Also retire stale jobs when every other job is still in a future tier.
    if not store.has_due(now=current()) or next_wait() > 0:
        result["wait_seconds"] = max(0.01, next_wait())
        return result
    claim_priority()
    guard = AuditGuard.wrap(auditor, event_sink=event_sink)

    def send_batch(batch):
        count = len(batch["jobs"])
        if stopping.is_set():
            store.retry_batch(
                batch,
                now=current(),
                delay=0.01,
                reason="stopping",
                count_attempt=False,
                ambiguous=False,
            )
            event("dispatch_retry", batch, reason="stopping", retry_after_seconds=0.01)
            return {"retried": 0}
        if not store.validate_dispatch(batch, now=current()):
            event("dispatch_skipped", batch, reason="invalid_dispatch")
            return {"skipped": count}
        channel = state.get("channels", {}).get(batch["route"])
        if not isinstance(channel, str) or not re.fullmatch(r"\d{17,20}", channel):
            store.finish_batch(batch, "failed", now=current(), reason="missing_destination")
            event("dispatch_failed", batch, reason="missing_destination")
            return {"failed": count}
        send_started = time.monotonic()
        try:
            event("dispatch_sending", batch, phase="send")
            response = client.request("POST", f"/channels/{channel}/messages", batch["payload"])
            message_id = response.get("id") if isinstance(response, dict) else None
            if not isinstance(message_id, str) or not re.fullmatch(r"\d{17,20}", message_id):
                raise DiscordError("Discord response did not contain a message ID")
            settled = store.finish_batch(batch, "sent", now=current(), message_id=message_id)
            event(
                "dispatch_sent" if settled else "dispatch_skipped",
                batch,
                settled=settled,
                elapsed_seconds=time.monotonic() - send_started,
                **({} if settled else {"reason": "lease_changed", "ambiguous": True}),
            )
            return {"messages": 1, "sent" if settled else "skipped": count}
        except DiscordError as exc:
            if exc.status == 413 and not exc.ambiguous:
                outcome = store.split_rejected_batch(batch, now=current())
                counter = (
                    "retried"
                    if outcome in {"split", "held"}
                    else ("failed" if outcome == "failed" else "skipped")
                )
                event(
                    {
                        "retried": "dispatch_retry",
                        "failed": "dispatch_failed",
                        "skipped": "dispatch_skipped",
                    }[counter],
                    batch,
                    reason="http_413_review" if outcome == "held" else "http_413",
                    ambiguous=outcome == "held",
                    **error_fields(exc, status=413),
                )
                return {counter: count}
            limited = exc.status == 429
            attempts = max(job["attempts"] for job in batch["jobs"])
            # An outage (5xx, timeouts, network) must not drop an $80+ alert:
            # it keeps retrying (backoff capped at 300 s) until it expires.
            exhausted = not limited and attempts >= 10 and not _high_value(batch)
            if exc.status in {400, 401, 403, 404} or exhausted:
                store.finish_batch(
                    batch,
                    "failed",
                    now=current(),
                    reason=f"http_{exc.status}" if exc.status else "retry_limit",
                )
                event(
                    "dispatch_failed",
                    batch,
                    reason=f"http_{exc.status}" if exc.status else "retry_limit",
                    ambiguous=exc.ambiguous,
                    elapsed_seconds=time.monotonic() - send_started,
                    **error_fields(exc, status=exc.status or 0),
                )
                return {"failed": count}
            delay = exc.retry_after if limited else min(300, 2 ** min(attempts, 8))
            if delay is None or not math.isfinite(delay):
                delay = 5
            delay = max(0.01, delay)
            if limited:
                local = exc.rate_limit_source is RateLimitSource.LOCAL
                if exc.global_limit and not local:
                    store.defer_global(until=current() + delay)
                elif not exc.global_limit:
                    store.defer_route(batch["route"], until=current() + delay)
                # A local global-scope refusal (shared window, another
                # worker's pending 429) sent nothing to Discord: never persist
                # it as a gate that pauses every route; this batch alone waits.
            store.retry_batch(
                batch,
                now=current(),
                delay=delay,
                reason="rate_limited" if limited else "remote_retry",
                count_attempt=not limited,
                ambiguous=exc.outcome.may_have_been_accepted,
            )
            event(
                "dispatch_throttled" if limited else "dispatch_retry",
                batch,
                reason="rate_limited" if limited else "remote_retry",
                scope="global" if exc.global_limit else "route",
                ambiguous=exc.ambiguous,
                elapsed_seconds=time.monotonic() - send_started,
                **error_fields(exc, status=exc.status or 0, retry_after_seconds=delay),
            )
            return {"retried": count, "throttled": int(limited)}

    def send(batch):
        started = time.monotonic()
        try:
            return send_batch(batch)
        except Exception as exc:
            event(
                "worker_error",
                batch,
                phase="dispatch",
                **error_fields(exc),
                ambiguous=True,
                elapsed_seconds=time.monotonic() - started,
            )
            raise

    submitted = 0
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="discord-route") as pool:
        while submitted < limit and not stopping.is_set():
            defer_learned_routes()
            if next_wait() > 0 or not store.has_due(now=current()):
                break
            claim_priority()
            # Never gates sending: on 2026-11-25 a fail-closed audit stopped
            # every alert for ~80 min over a harmless Server Booster role.
            audit_started = time.monotonic()
            try:
                guard(
                    _AuditClient(client, stopping),
                    state.get("guild_id"),
                    state,
                    caller="channel",
                    stop_event=stopping,
                )
            finally:
                result["audit_seconds"] += time.monotonic() - audit_started
            if stopping.is_set():
                break
            wave, routes = [], []
            capacity = min(concurrency, limit - submitted)
            for slot in range(capacity):
                if stopping.is_set():
                    break
                # Never put Free in the same wave as outstanding due paid work:
                # waiting for a slow Free HTTP call would hold the next paid
                # envelope on an already occupied channel behind that call.
                batch = store.claim_batch(
                    now=current(),
                    exclude_routes=routes,
                    audience="paid",
                    size=10,
                    fair=slot == 1 or capacity == 1,
                )
                if batch is None and not store.has_due_paid(now=current()):
                    batch = store.claim_batch(now=current(), exclude_routes=routes)
                if batch is None:
                    break
                routes.append(batch["route"])
                # A durable claim precedes any HTTP work. Comparing its original
                # due and retry deadline with the outcome distinguishes waiting
                # before claim from provider/settlement latency after claim.
                event("dispatch_claimed", batch)
                wave.append(pool.submit(send, batch))
                submitted += 1
            if not wave:
                break
            for future in as_completed(wave):
                # Unexpected exceptions retain the already persisted envelope
                # and lease for recovery; the worker reports a sanitized error.
                for key, value in future.result().items():
                    result[key] += value
    result["elapsed_seconds"] = round(time.monotonic() - started, 4)
    result["audit_seconds"] = round(result["audit_seconds"], 4)
    result["wait_seconds"] = 5 if result["blocked"] else min(30, max(0.01, next_wait()))
    event("delivery_cycle", **result)
    return result
