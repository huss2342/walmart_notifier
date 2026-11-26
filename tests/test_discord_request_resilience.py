"""Catch-ups survive sold-out items and restarts, gather every search wave into
one DM, and parked possibly-sent DMs settle from the DM's own history."""

import sqlite3
import time

import pytest
from discord_bridge.api import DeliveryOutcome, DiscordError
from discord_bridge.core import JobStore
from discord_bridge.monitoring import _REASONS
from discord_bridge.request_delivery import RECONCILE_WINDOW, RequestWorker, _snowflake_at
from discord_bridge.request_store import (
    BACKLOG_GATHER_SECONDS,
    BACKLOG_MAX_GATHER_SECONDS,
    RECONCILE_DELAY_SECONDS,
    RequestStore,
)

from test_discord_request_catchup import claims, item, older_matches, run, states
from test_discord_request_delivery import DM, STATE, Provider
from test_discord_requests import GUILD, USER, observe, product

DUE = 300 + BACKLOG_GATHER_SECONDS


def reasons(store):
    with sqlite3.connect(store.path) as db:
        return dict(db.execute(
            "SELECT reason,COUNT(*) FROM personal_request_alerts GROUP BY reason").fetchall())


def sell_out_on_first_validate(store, jobs, products, *, pick=lambda job: job["item_id"]):
    """The scanner sees one catch-up item out of stock between claim and send."""
    original, fired = store.validate, []

    def validate(job, now=None):
        if not fired:
            target = pick(job)
            fired.append(target)
            gone = next(p for p in products if p["item_id"] == target)
            jobs.record([{**gone, "available": False}], now=now)
        return original(job, now=now)

    store.validate = validate
    return fired


# 1. A sold-out item or a restart never drops the rest of a catch-up.


def test_sold_out_top_item_is_dropped_and_the_rest_still_go_out(tmp_path):
    products = [item(index, value=10000 + index) for index in range(5)]
    store, jobs = older_matches(tmp_path, products)
    gone = sell_out_on_first_validate(store, jobs, products)
    provider = Provider()
    run(RequestWorker(provider, STATE, store, enabled=True), DUE, ticks=20)
    assert gone == [products[-1]["item_id"]], "the highest value leads the catch-up"
    assert len(provider.messages) == 1
    payload = provider.messages[0]
    assert payload["content"].startswith("4 items matching your requests were already listed")
    assert [rank for rank, _ in claims(payload)] == list(range(1, 5))
    assert products[-1]["review_url"] not in str(payload)
    assert states(store) == {"sent": 4, "cancelled": 1}
    assert reasons(store) == {None: 4, "unavailable": 1}


def test_sold_out_lower_item_is_dropped_and_numbering_stays_contiguous(tmp_path):
    products = [item(index, value=10000 + index) for index in range(4)]
    store, jobs = older_matches(tmp_path, products)
    sell_out_on_first_validate(
        store, jobs, products, pick=lambda job: job["bundle"][1]["item_id"])
    provider = Provider()
    run(RequestWorker(provider, STATE, store, enabled=True), DUE, ticks=20)
    (payload,) = provider.messages
    assert payload["content"].startswith("3 items matching")
    assert [rank for rank, _ in claims(payload)] == list(range(1, 4))
    assert states(store) == {"sent": 3, "cancelled": 1}


def test_shutdown_mid_cycle_hands_the_catchup_back_instead_of_cancelling(tmp_path):
    products = [item(index, value=10000 + index) for index in range(5)]
    store, _ = older_matches(tmp_path, products)
    provider = Provider()
    worker = RequestWorker(provider, STATE, store, enabled=True)
    provider.on_member = worker.stop_event.set  # Restart begins mid-cycle.
    result = worker.tick(now=DUE)
    assert result["retried"] == 1 and result["skipped"] == 0
    assert provider.messages == [] and states(store) == {"pending": 5}
    restarted = RequestWorker(Provider(), STATE, RequestStore(store.path), enabled=True)
    run(restarted, DUE + 1, ticks=5)
    (payload,) = restarted.client.messages
    assert payload["content"].startswith("5 items matching")
    assert states(store) == {"sent": 5}


def test_single_new_listing_that_sold_out_is_still_cancelled(tmp_path):
    store = RequestStore(tmp_path / "requests.sqlite3", guild_id=GUILD)
    store.set_dm_status(USER, "ready")
    store.add(USER, "air fryer", now=10)
    observe(store, [product()], now=11)
    worker = RequestWorker(Provider(), STATE, store, enabled=True)
    original = store.validate

    def validate(job, now=None):
        observe(store, [product(available=False)], now=now)
        return original(job, now=now)

    store.validate = validate
    assert worker.tick(now=12)["skipped"] == 1
    assert worker.client.messages == [] and states(store) == {"cancelled": 1}


# 2. One /request add -> one catch-up, however many search waves it takes.


def test_later_search_waves_join_the_same_catchup(tmp_path):
    first = [item(index, value=10000 + index) for index in range(4)]
    later = [item(100 + index, value=20000 + index) for index in range(4)]
    store = RequestStore(tmp_path / "r.sqlite3", guild_id=GUILD)
    store.set_dm_status(USER, "ready")
    jobs = JobStore(store.path)
    jobs.record(first + later, now=100)
    store.add(USER, "recliner", now=200)
    jobs.record(first, now=300)
    jobs.record(later, now=330)  # Another search wave surfaces more 30 s later.
    provider = Provider()
    worker = RequestWorker(provider, STATE, store, enabled=True)
    run(worker, DUE, ticks=29)
    assert provider.messages == [], "still gathering: something new arrived 30 s ago"
    run(worker, 330 + BACKLOG_GATHER_SECONDS, ticks=30)
    (payload,) = provider.messages
    assert payload["content"].startswith("8 items matching your requests were already listed")
    assert "/request listed" not in payload["content"]
    assert len(claims(payload)) == 8 and states(store) == {"sent": 8}


def test_two_large_waves_send_one_top_ten_with_the_full_count(tmp_path):
    first = [item(index, value=10000 + index) for index in range(12)]
    later = [item(100 + index, value=90000 + index) for index in range(12)]
    store = RequestStore(tmp_path / "r.sqlite3", guild_id=GUILD)
    store.set_dm_status(USER, "ready")
    jobs = JobStore(store.path)
    jobs.record(first + later, now=100)
    store.add(USER, "recliner", now=200)
    jobs.record(first, now=300)
    jobs.record(later, now=330)
    provider = Provider()
    run(RequestWorker(provider, STATE, store, enabled=True), DUE, ticks=80)
    (payload,) = provider.messages
    assert payload["content"].startswith("24 items matching")
    assert "Use `/request listed` to see all 24." in payload["content"]
    description = payload["embeds"][0]["description"]
    assert "$900.11" in description and "$900.02" in description  # Top ten: later wave.
    assert "$900.01" not in description and "$100.11" not in description
    assert states(store) == {"sent": 10, "skipped": 14}


def test_a_steady_trickle_still_sends_by_the_gather_cap(tmp_path):
    products = [item(index, value=1000 + index) for index in range(12)]
    store = RequestStore(tmp_path / "r.sqlite3", guild_id=GUILD)
    store.set_dm_status(USER, "ready")
    jobs = JobStore(store.path)
    jobs.record(products, now=100)
    store.add(USER, "recliner", now=200)
    provider = Provider()
    worker = RequestWorker(provider, STATE, store, enabled=True)
    for index, product_ in enumerate(products):  # One new older match every 30 s.
        at = 300 + 30 * index
        jobs.record([product_], now=at)
        run(worker, at, ticks=30)
    # Sent at the cap (first match + BACKLOG_MAX_GATHER_SECONDS), not starved.
    assert len(provider.messages) == 1
    assert provider.messages[0]["content"].startswith(
        f"{BACKLOG_MAX_GATHER_SECONDS // 30 + 1} items")
    assert states(store) == {"sent": 10, "skipped": 1, "pending": 1}


def test_matches_past_the_scan_bound_are_counted_and_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr("discord_bridge.request_store.CATCHUP_SCAN_LIMIT", 4)
    products = [item(index, value=500 * (index + 1)) for index in range(12)]
    store, _ = older_matches(tmp_path, products)
    provider = Provider()
    run(RequestWorker(provider, STATE, store, enabled=True), DUE, ticks=40)
    (payload,) = provider.messages
    assert payload["content"].startswith("12 items matching")
    description = payload["embeds"][0]["description"]
    # The highest values are scanned first (plus the row that started the claim).
    assert [f"${500 * rank / 100:,.2f}" in description for rank in (12, 11, 10)] == [True] * 3
    assert states(store) == {"sent": 4, "skipped": 8}


# 4. Parked possibly-sent DMs settle from the DM channel's own history.


class HistoryProvider(Provider):
    def __init__(self):
        super().__init__()
        self.history, self.history_error, self.reads = [], None, []

    def channel_messages(self, channel_id, *, limit=100, after=None, before=None):
        self.reads.append((channel_id, limit, after))
        if self.history_error:
            raise self.history_error
        return list(self.history)[:limit]


def parked(tmp_path, *, now=12):
    store = RequestStore(tmp_path / "requests.sqlite3", guild_id=GUILD)
    store.set_dm_status(USER, "ready")
    store.add(USER, "air fryer", now=now - 2)
    observe(store, [product()], now=now - 1)
    provider = HistoryProvider()
    worker = RequestWorker(provider, STATE, store, enabled=True)
    provider.send_error = DiscordError(outcome=DeliveryOutcome.UNKNOWN)
    assert worker.tick(now=now)["review"] == 1
    sent = provider.messages[-1]
    provider.send_error = None
    return store, provider, worker, sent


def later(now=12):
    return now + 60 + RECONCILE_DELAY_SECONDS


def bot_copy(payload, *, author=None):
    return {"id": "888888888888888888", "channel_id": DM,
            "author": author or {"id": "999999999999999999", "bot": True},
            "content": payload["content"], "embeds": payload["embeds"],
            "components": payload.get("components", [])}


def test_parked_dm_found_in_history_is_settled_as_sent(tmp_path):
    store, provider, worker, sent = parked(tmp_path)
    provider.history = [{"id": "888888888888888880", "author": {"id": USER}, "content": "hi"},
                        bot_copy(sent)]
    assert worker.tick(now=later() - 1)["sent"] == 0
    assert provider.reads == [], "too soon after the lease"
    worker.tick(now=later())
    assert provider.reads == [], "at most one idle check per interval"
    worker.tick(now=later() + 59)
    assert provider.reads == [(DM, RECONCILE_WINDOW, None)]
    assert states(store) == {"sent": 1} and reasons(store) == {"reconciled_sent": 1}
    observe(store, [product()], now=later() + 5)
    run(worker, later() + 6, ticks=3)
    assert len(provider.messages) == 1, "never sent twice"


def test_parked_dm_missing_from_complete_history_rearms_without_sending_now(tmp_path):
    store, provider, worker, sent = parked(tmp_path)
    pasted = {**bot_copy(sent), "author": {"id": USER}}  # A member pasting the link.
    provider.history = [pasted]
    worker.tick(now=later())
    assert states(store) == {"failed": 1} and reasons(store) == {"reconciled_undelivered": 1}
    run(worker, later() + 1, ticks=120)
    assert len(provider.messages) == 1, "no DM until the item is seen in stock again"
    observe(store, [product()], now=later() + 200)
    assert worker.tick(now=later() + 201)["sent"] == 1
    assert len(provider.messages) == 2 and states(store) == {"sent": 1}


@pytest.mark.parametrize(
    "history,error",
    [
        ([{"id": str(888888888888888000 + n), "author": {"id": USER}}
          for n in range(RECONCILE_WINDOW)], None),
        ([], DiscordError(status=404, provider_code=10003)),
        ([], DiscordError(status=403, provider_code=50001)),
    ],
)
def test_inconclusive_or_unreadable_history_stays_parked(tmp_path, history, error):
    store, provider, worker, _ = parked(tmp_path)
    provider.history, provider.history_error = history, error
    worker.tick(now=later())
    assert states(store) == {"review": 1} and reasons(store) == {"reconcile_unresolved": 1}
    run(worker, later() + 60, ticks=200)
    assert len(provider.reads) == 1, "one read per parked DM"


def test_transient_history_errors_retry_on_a_later_idle_window(tmp_path):
    store, provider, worker, sent = parked(tmp_path)
    provider.history_error = DiscordError(status=503)
    worker.tick(now=later())
    assert reasons(store) == {"ambiguous_send": 1}
    worker.tick(now=later() + 1)
    assert len(provider.reads) == 1, "at most one read per interval"
    provider.history_error, provider.history = None, [bot_copy(sent)]
    worker.tick(now=later() + 60)
    assert states(store) == {"sent": 1} and len(provider.reads) == 2


def test_parked_catchup_is_matched_by_any_item_and_settled_together(tmp_path):
    products = [item(index, value=10000 + index) for index in range(3)]
    store, _ = older_matches(tmp_path, products)
    provider = HistoryProvider()
    provider.send_error = DiscordError(ambiguous=True)
    worker = RequestWorker(provider, STATE, store, enabled=True)
    assert worker.tick(now=DUE)["review"] == 1
    assert states(store) == {"review": 3}
    payload = provider.messages[-1]
    # Only the embed links carry the items (no buttons): still found.
    provider.history = [{**bot_copy(payload), "components": []}]
    worker.tick(now=later(DUE))
    assert states(store) == {"sent": 3} and reasons(store) == {"reconciled_sent": 3}


def test_history_window_starts_before_the_claim(tmp_path):
    now = time.time()
    store, provider, worker, sent = parked(tmp_path, now=now)
    worker.tick(now=later(now))
    ((_, _, after),) = provider.reads
    assert abs((int(after) >> 22) - (int(_snowflake_at(now - 15 * 60)) >> 22)) <= 2


def test_bot_identity_from_state_is_required_when_known(tmp_path):
    store, provider, worker, sent = parked(tmp_path)
    worker.state = {**STATE, "bot_user_id": "999999999999999990"}
    provider.history = [bot_copy(sent)]  # Another bot's message, not ours.
    worker.tick(now=later())
    assert states(store) == {"failed": 1}


def test_reconcile_reasons_are_allowed_in_operational_events():
    assert {"reconciled_sent", "reconciled_undelivered", "reconcile_unresolved",
            "stopping"} <= _REASONS
