"""Value priority, tier fairness, and operational queue visibility."""

from discord_bridge.core import FREE_ROUTE_KEY, JobStore

from test_discord_burst_store import only_route, product


def test_paid_due_check_ignores_free_future_expired_and_terminal_jobs(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1, value_usd=10)], now=1000)
    assert store.has_due_paid(now=1000) is True
    with store._connect() as db:
        db.execute(
            "UPDATE jobs SET state='sent' WHERE route!='starter-items' AND route!=?",
            (FREE_ROUTE_KEY,),
        )
    assert store.has_due_paid(now=1299) is False
    assert store.has_due_paid(now=1300) is True
    with store._connect() as db:
        db.execute(
            "UPDATE jobs SET state='leased',lease_until=999999,next_attempt=999999 "
            "WHERE route='starter-items'"
        )
    assert store.has_due_paid(now=1301) is True  # Still prioritise paid retry/in-flight work.
    assert store.has_due_paid(now=999999) is False
    with store._connect() as db:
        db.execute("UPDATE jobs SET state='failed' WHERE route='starter-items'")
    assert store.has_due_paid(now=87400) is False  # Free is due; paid is terminal.


def test_welcome_priority_check_never_runs_full_queue_analytics(tmp_path, monkeypatch):
    from notifiers.discord import DiscordNotifier

    store = JobStore(tmp_path / "q")
    notifier = DiscordNotifier(store)

    def forbidden(**_kwargs):
        raise AssertionError("full metrics must not run on the per-request priority path")

    monkeypatch.setattr(store, "delivery_metrics", forbidden)
    assert notifier._paid_due() is False
    store.record([product(1)], now=1000)
    assert store.has_due_paid(now=1000) is True


def test_queue_analytics_use_covering_indexes_for_payload_sized_job_history(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1)], now=1000)
    queries = [
        (
            "SELECT COUNT(*),MIN(due),SUM(route=?) FROM jobs WHERE state IN ('pending','leased') "
            "AND due<=? AND expires>?",
            (FREE_ROUTE_KEY, 1000, 1000),
            "jobs_metrics_pending",
        ),
        (
            "SELECT route,state,due,expires,next_attempt FROM jobs "
            "WHERE state IN ('pending','leased')",
            (),
            "jobs_metrics_pending",
        ),
        (
            "SELECT route,due,sent_at,message_id FROM jobs WHERE state='sent' "
            "AND sent_at BETWEEN ? AND ?",
            (1000, 1300),
            "jobs_metrics_sent",
        ),
        (
            "SELECT state,reason,COUNT(*) FROM jobs WHERE reason IS NOT NULL GROUP BY state,reason",
            (),
            "jobs_metrics_reasons",
        ),
    ]
    with store._connect() as db:
        for query, params, index in queries:
            details = " ".join(row[3] for row in db.execute("EXPLAIN QUERY PLAN " + query, params))
            assert f"COVERING INDEX {index}" in details


def values(batch):
    return [job["item"]["value_cents"] for job in batch["jobs"]]


def test_expensive_pro_backlog_precedes_a_fresh_cheap_pro_item(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1, value_usd=499)], now=1000)
    store.record([product(2, value_usd=5)], now=1200)
    only_route(store, "pro-all")
    assert values(store.claim_batch(now=1200, size=1)) == [49900]


def test_priority_bundle_keeps_four_highest_values_and_one_oldest_catchup(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record(
        [product(i, value_usd=value) for i, value in enumerate([1, 200, 10, 500, 80, 300, 50])],
        now=1000,
    )
    only_route(store, "pro-all")
    # Make the cheap observation older, rather than relying on hashed-ID ties.
    with store._connect() as db:
        db.execute("UPDATE jobs SET due=999 WHERE item_id=?", (product(0)["item_id"],))
    assert values(store.claim_batch(now=1000)) == [50000, 30000, 20000, 8000, 100]


def test_actual_dispatcher_catches_up_cheap_items_during_continuous_expensive_intake(
    tmp_path, monkeypatch
):
    from test_discord_burst_delivery import Remote, run

    # Isolate one busy channel without racing a second SQL transaction that
    # suppresses newly created destinations during an in-flight dispatch.
    monkeypatch.setattr("discord_bridge.core.route_keys", lambda item: ["pro-all"])
    store = JobStore(tmp_path / "q")
    cheap = [product(i, value_usd=5) for i in range(10)]
    store.record(cheap, now=1000)
    store.record([product(100 + i, value_usd=999) for i in range(5)], now=1200)
    only_route(store, "pro-all")

    class ReplenishingRemote(Remote):
        def request(self, method, route, payload):
            response = super().request(method, route, payload)
            start = 200 + len(self.calls) * 5
            store.record([product(start + i, value_usd=1999) for i in range(5)], now=1200)
            return response

    remote = ReplenishingRemote()
    assert run(store, remote, now=1200, limit=20)["messages"] == 20
    with store._connect() as db:
        sent_cheap = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE route='pro-all' AND state='sent' "
            "AND item_id IN (" + ",".join("?" for _ in cheap) + ")",
            [item["item_id"] for item in cheap],
        ).fetchone()[0]
    assert sent_cheap == 10


def test_expensive_arrival_leads_next_paid_envelope_during_low_value_burst(tmp_path, monkeypatch):
    from test_discord_burst_delivery import Remote, run

    monkeypatch.setattr("discord_bridge.core.route_keys", lambda item: ["pro-all"])
    store = JobStore(tmp_path / "q")
    store.record([product(i, value_usd=5) for i in range(40)], now=1000)
    expensive = product(999, value_usd=1999)

    class ArrivingRemote(Remote):
        def request(self, method, route, payload):
            response = super().request(method, route, payload)
            if len(self.calls) == 1:
                store.record([expensive], now=1200)
            return response

    remote = ArrivingRemote()
    assert run(store, remote, now=1200)["sent"] == 41
    assert len(remote.calls[0][1]["embeds"]) == 10
    assert remote.calls[1][1]["embeds"][0]["footer"]["text"] == expensive["item_id"]
    assert expensive["item_id"] not in {
        embed["footer"]["text"] for embed in remote.calls[0][1]["embeds"]
    }


def test_pro_precedes_overdue_starter_and_free_in_priority_slot(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1, available=False)], now=0)
    store.record([product(2, value_usd=39)], now=86000)
    with store._connect() as db:
        db.execute(
            "UPDATE jobs SET state='skipped' WHERE item_id=? AND route!='starter-items'",
            (product(2)["item_id"],),
        )
    store.record([product(3, value_usd=999)], now=86400)
    batch = store.claim_batch(now=86400, size=1)
    assert batch["route"] == "pro-all" and values(batch) == [99900]


def test_fair_slot_keeps_old_cheap_route_moving_under_high_value_arrivals(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1, value_usd=5, category="pets")], now=1000)
    with store._connect() as db:
        db.execute("UPDATE jobs SET state='skipped' WHERE route!='type-pets'")
    store.record([product(2, value_usd=999)], now=1200)
    primary = store.claim_batch(now=1200, size=1)
    assert primary["route"] == "pro-all"
    catchup = store.claim_batch(now=1200, exclude_routes=[primary["route"]], fair=True)
    assert catchup["route"] == "type-pets" and values(catchup) == [500]


def test_catchup_within_busy_pro_channel_keeps_old_cheap_items_moving(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1, value_usd=5)], now=1000)
    store.record([product(2, value_usd=999)], now=1200)
    only_route(store, "pro-all")
    primary = store.claim_batch(now=1200, size=1)
    assert values(primary) == [99900]
    store.finish_batch(primary, "sent", now=1200, message_id="1800000000000000001")
    store.record([product(3, value_usd=1999)], now=1201)
    only_route(store, "pro-all")
    catchup = store.claim_batch(now=1201, fair=True, size=1)
    assert values(catchup) == [500]


def test_price_priority_never_releases_starter_or_free_early(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1, value_usd=35)], now=1000)
    only_route(store, "starter-items")
    assert store.claim_batch(now=1299) is None
    assert store.claim_batch(now=1300)["route"] == "starter-items"
    free = JobStore(tmp_path / "free")
    free.record([product(2, value_usd=999)], now=1000)
    only_route(free, FREE_ROUTE_KEY)
    assert free.claim_batch(now=87399) is None
    assert free.claim_batch(now=87400)["route"] == FREE_ROUTE_KEY


def test_queue_metrics_distinguish_future_retry_throttle_and_terminal_history(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1, value_usd=10)], now=1000)
    batch = store.claim_batch(now=1000, size=1)
    store.retry_batch(batch, now=1000, delay=30, reason="rate_limited", count_attempt=False)
    store.defer_route(batch["route"], until=1030)
    store.record([product(2, value_usd=20)], now=-1000)
    store.has_due(now=1001)
    metrics = store.delivery_metrics(now=1001)
    assert metrics["by_tier"]["pro"]["due"] == 3
    assert metrics["by_tier"]["pro"]["retry_wait"] == 1
    assert metrics["by_tier"]["pro"]["throttled"] == 1
    assert metrics["by_tier"]["starter"]["future"] == 1
    assert metrics["by_tier"]["free"]["future"] == 2
    assert metrics["active_route_cooldowns"] == 1
    assert metrics["expired_items"] == 4
    assert metrics["reasons"]["expired"]["stale"] == 4


def test_delivery_latency_is_since_tier_due_and_omits_old_legacy_sends(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1)], now=1000)
    with store._connect() as db:
        db.execute(
            "UPDATE jobs SET state='sent',sent_at=due+7,message_id='1' WHERE route='starter-items'"
        )
        db.execute(
            "UPDATE jobs SET state='sent',sent_at=due+2,message_id='2' WHERE route='pro-all'"
        )
        db.execute(
            "UPDATE jobs SET state='sent',sent_at=NULL,message_id='3' "
            "WHERE route='type-electronics'"
        )
    metrics = store.delivery_metrics(now=1310)
    assert metrics["recent_delivery_latency_seconds"]["starter"] == {
        "samples": 1,
        "p50": 7.0,
        "p95": 7.0,
        "max": 7.0,
    }
    assert metrics["recent_delivery_latency_seconds"]["pro"]["samples"] == 0
    assert metrics["recent_delivery_latency_seconds"]["free"]["p95"] is None


def test_metrics_never_expose_arbitrary_stored_reason_text(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1)], now=1000)
    with store._connect() as db:
        db.execute("UPDATE jobs SET state='failed',reason='Bearer secret-token'")
    metrics = store.delivery_metrics(now=1000)
    assert metrics["failed_items"] == 5
    assert metrics["reasons"]["failed"] == {"other": 5}
    assert "secret-token" not in str(metrics)
