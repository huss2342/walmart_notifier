"""Free alerts preserve the original public observation until the 24-hour release."""

import json
import sqlite3

import pytest
from discord_bridge.core import JobStore, normalize_item, render_message

FREE_ROUTE = "yesterday-recap"
DAY = 86400


def observation(**changes):
    return normalize_item(
        {
            "item_id": "ip-20449164214",
            "title": "Original book",
            "value_usd": 19.99,
            "category": "toys-baby-books",
            **changes,
        }
    )


def test_free_alert_waits_full_day_and_survives_restart_unavailability_and_updates(tmp_path):
    path = tmp_path / "free.sqlite3"
    store = JobStore(path)
    original = observation(image_url="https://i5.walmartimages.com/asr/original.jpeg")
    store.record([original], now=1000)
    while job := store.claim(now=1300):
        assert job["route"] != FREE_ROUTE
        store.finish(job, "sent")
    store.record(
        [
            observation(
                title="Changed listing",
                value_usd=99.99,
                available=False,
                image_url="https://i5.walmartimages.com/asr/newer-private-until-tomorrow.jpeg",
            )
        ],
        now=2000,
    )
    store = JobStore(path)
    assert store.claim(now=1000 + DAY - 0.001) is None
    job = store.claim(now=1000 + DAY)
    assert job is not None and job["route"] == FREE_ROUTE
    assert job["item"] == original
    assert job["first_seen"] == 1000
    message = render_message(job["item"], first_seen=1000, last_seen=1000, route=FREE_ROUTE)
    text = json.dumps(message).lower()
    assert message["embeds"][0]["thumbnail"]["url"] == original["image_url"]
    assert "newer-private" not in text
    assert "historical" in text and "24-hour" in text
    assert "no intentional delay" not in text
    store.finish(job, "sent")
    store.record([original], now=1000 + DAY + 5)
    assert store.claim(now=1000 + DAY + 5) is None
    assert store.counts() == {"sent": 5}


@pytest.mark.parametrize("value", [None, 0, 19.99, 299.99])
def test_every_value_can_be_released_free_even_when_first_observation_is_unavailable(
    tmp_path, value
):
    store = JobStore(tmp_path / "all-values.sqlite3")
    store.record([observation(value_usd=value, available=False)], now=1000)
    assert store.counts() == {"pending": 1}
    assert store.claim(now=1000 + DAY - 1) is None
    job = store.claim(now=1000 + DAY)
    assert job["route"] == FREE_ROUTE and job["item"]["available"] is False


def test_free_alert_has_its_own_expiry_window_after_the_day_delay(tmp_path):
    store = JobStore(tmp_path / "expiry.sqlite3")
    store.record([observation(available=False)], now=1000)
    assert not store.has_due(now=2800)
    assert store.counts() == {"pending": 1}
    # Historical free work can catch up after an outage without the paid
    # feed's short live-availability window, but retention is still bounded.
    assert store.has_due(now=1000 + DAY + 1800)
    assert store.has_due(now=1000 + DAY + 7 * DAY - 1)
    assert not store.has_due(now=1000 + DAY + 7 * DAY)
    assert store.counts() == {"expired": 1}


def test_schema_one_migration_keeps_paid_jobs_and_does_not_backdate_free_snapshots(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    item = observation()
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE items (item_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                first_seen REAL NOT NULL, last_seen REAL NOT NULL);
            CREATE TABLE jobs (id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(item_id),
                route TEXT NOT NULL, due REAL NOT NULL, expires REAL NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL, lease_token TEXT, lease_until REAL,
                message_id TEXT, reason TEXT, UNIQUE(item_id,route));
            PRAGMA user_version=1;
        """)
        db.execute(
            "INSERT INTO items VALUES (?,?,?,?)", (item["item_id"], json.dumps(item), 1000, 1000)
        )
        db.execute(
            "INSERT INTO jobs (id,item_id,route,due,expires,next_attempt) VALUES (?,?,?,?,?,?)",
            ("existing-paid-job", item["item_id"], "pro-all", 1000, 2800, 1000),
        )
    store = JobStore(path)
    job = store.claim(now=1100)
    assert job["id"] == "existing-paid-job" and job["first_seen"] == 1000
    store.finish(job, "sent")
    store.record([item], now=1200)
    while job := store.claim(now=1300):
        assert job["route"] != FREE_ROUTE
        store.finish(job, "sent")
    store = JobStore(path)
    # A new free snapshot of a legacy item must not expose recent updates using
    # the item's older first_seen clock. Paid timing above remains unchanged.
    assert store.claim(now=1200 + DAY - 1) is None
    free = store.claim(now=1200 + DAY)
    assert free["route"] == FREE_ROUTE and free["first_seen"] == 1200
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4


def test_due_free_history_cannot_hold_up_new_paid_alerts(tmp_path):
    store = JobStore(tmp_path / "priority.sqlite3")
    store.record([observation()], now=1000)
    store.record([observation(item_id="ip-20449164215")], now=1000 + DAY)
    for _ in range(3):
        job = store.claim(now=1000 + DAY)
        assert job["item_id"] == "ip-20449164215"
        assert job["route"] != FREE_ROUTE
        store.finish(job, "sent")
    assert store.claim(now=1000 + DAY)["route"] == FREE_ROUTE
