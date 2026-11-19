"""Pruning old dispatch envelopes keeps every row dedupe and restocks rely on."""

import threading

from discord_bridge.core import JobStore, normalize_item

from collector_prune import CollectorPruner, prune_dispatches

DAY = 86400
T0 = 1_800_000_000.0


def offer(number, value=149.99):
    return normalize_item({"item_id": f"ip-{number}", "title": "Oak desk", "value_usd": value,
                           "url": f"https://www.walmart.com/ip/{number}"})


def deliver(store, now):
    while (batch := store.claim_batch(now=now, size=5)) is not None:
        store.finish_batch(batch, "sent", now=now, message_id="123456789012345678")


def count(store, sql):
    with store._connect() as db:
        return db.execute(sql).fetchone()[0]


def test_only_old_finished_dispatches_are_deleted_and_dedupe_still_holds(tmp_path):
    store = JobStore(tmp_path / "collector.sqlite3")
    old = [offer(20000000 + n) for n in range(12)]
    store.record(old, now=T0)
    deliver(store, T0 + 1)
    recent = [offer(30000000 + n) for n in range(3)]
    store.record(recent, now=T0 + 20 * DAY)
    deliver(store, T0 + 20 * DAY + 1)
    waiting = offer(40000000)
    store.record([waiting], now=T0 + 20 * DAY + 2)
    retry = store.claim_batch(now=T0 + 20 * DAY + 3, size=5)  # A retry in flight.
    store.retry_batch(retry, now=T0 + 20 * DAY + 3, delay=30, reason="test")
    before = count(store, "SELECT COUNT(*) FROM dispatches")
    jobs = count(store, "SELECT COUNT(*) FROM jobs")
    items = count(store, "SELECT COUNT(*) FROM items")

    deleted = prune_dispatches(store.path, now=T0 + 20 * DAY + 5, batch=4, pause=0)

    assert deleted > 0
    assert count(store, "SELECT COUNT(*) FROM dispatches") == before - deleted
    assert count(store, "SELECT COUNT(*) FROM dispatches WHERE state='pending'") == 1, "retry kept"
    assert count(store, "SELECT COUNT(*) FROM jobs") == jobs, "jobs are never deleted"
    assert count(store, "SELECT COUNT(*) FROM items") == items, "items are never deleted"
    with store._connect() as db:
        kept = " ".join(row[0] for row in db.execute("SELECT members FROM dispatches"))
        sent_recent = [row[0] for row in db.execute(
            "SELECT id FROM jobs WHERE item_id LIKE 'ip-3%' AND state='sent'")]
        sent_old = [row[0] for row in db.execute(
            "SELECT id FROM jobs WHERE item_id LIKE 'ip-2%' AND state='sent'")]
    assert sent_recent and all(job in kept for job in sent_recent), "recent envelopes kept"
    assert sent_old and not any(job in kept for job in sent_old), "old envelopes gone"

    # Dedupe is untouched: the old offers are duplicates, nothing is queued again.
    again = store.record(old, now=T0 + 20 * DAY + 10)
    assert again["new"] == 0 and again["queued"] == 0
    # A restock still re-arms the finished job and gets a fresh dispatch.
    back = store.record([old[0]], now=T0 + 21 * DAY, restocked={old[0]["item_id"]})
    assert back["queued"] == 1
    deliver(store, T0 + 21 * DAY)
    assert count(store, "SELECT COUNT(*) FROM jobs WHERE state='pending' "
                        "AND route!='yesterday-recap' AND item_id='ip-20000000'") == 0


def test_pruning_stops_at_the_cutoff_and_can_be_turned_off(tmp_path):
    store = JobStore(tmp_path / "collector.sqlite3")
    store.record([offer(20000001)], now=T0)
    deliver(store, T0 + 1)
    assert prune_dispatches(store.path, now=T0 + 13 * DAY, pause=0) == 0, "younger than 14 days"
    assert prune_dispatches(store.path, now=T0 + 15 * DAY, pause=0) > 0
    stop = threading.Event()
    assert CollectorPruner(store.path, stop_event=stop, keep_days=0).health()["enabled"] is False
    assert CollectorPruner(store.path, stop_event=stop, keep_days=2).keep_days == 7, \
        "never below a week: free recap jobs live 8 days"
