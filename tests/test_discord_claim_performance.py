"""Bound queue-claim work while a large future Free history is retained."""

import json
import sqlite3
from contextlib import contextmanager

import pytest
from discord_bridge.core import FREE_ROUTE_KEY, JobStore, normalize_item, render_batch


def _product(number, *, available=True):
    return normalize_item(
        {
            "item_id": f"ip-{10000000 + number}",
            "title": f"Queue profiling product {number}",
            "value_usd": number + 100,
            "category": "electronics",
            "available": available,
        }
    )


def test_frozen_paid_claim_has_bounded_work_with_thousands_of_future_free_jobs(
    tmp_path, monkeypatch
):
    store = JobStore(tmp_path / "queue.sqlite3")
    store.record([_product(number) for number in range(60)], now=1000)
    envelopes = {}
    with store._connect() as db:
        db.execute("UPDATE jobs SET state='skipped' WHERE route!='pro-all'")
        rows = db.execute(
            "SELECT jobs.*,items.payload,items.first_seen,items.last_seen "
            "FROM jobs JOIN items USING(item_id) WHERE route='pro-all' ORDER BY jobs.id"
        ).fetchall()
        # Persist the old ten-card format explicitly. New-envelope wire caps
        # must not shrink this fixture or repackage an attempted legacy send.
        for index, first in enumerate(range(0, len(rows), 10), 1):
            jobs = [store._job(row) for row in rows[first : first + 10]]
            batch_id = f"{index:025x}"
            payload = render_batch(jobs)
            payload.update(nonce=batch_id, enforce_nonce=True)
            envelopes[batch_id] = {"id": batch_id, "jobs": jobs, "payload": payload}
            db.execute(
                "INSERT INTO dispatches(id,route,members,payload,state,uncertain) "
                "VALUES(?,'pro-all',?,?,'pending',1)",
                (batch_id, json.dumps([job["id"] for job in jobs]), json.dumps(payload)),
            )
            db.executemany(
                "UPDATE jobs SET batch_id=?,attempts=1,next_attempt=1600,reason='remote_retry' "
                "WHERE id=?",
                [(batch_id, job["id"]) for job in jobs],
            )

    for first in range(1000, 6000, 500):
        store.record(
            [_product(number, available=False) for number in range(first, first + 500)],
            now=1000,
        )
    # One sibling's retry deadline still excludes its entire immutable envelope.
    blocked = next(iter(envelopes.values()))
    with store._connect() as db:
        db.execute(
            "UPDATE jobs SET next_attempt=1601 WHERE id=?",
            (blocked["jobs"][0]["id"],),
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM jobs WHERE route=? AND state='pending' AND due>1600",
                (FREE_ROUTE_KEY,),
            ).fetchone()[0]
            == 5000
        )

    # Count SQLite VM instructions instead of depending on CPU, disk or CI speed.
    # An unbounded correlated retry lookup scans the entire future Free cohort
    # for each paid candidate, while a batch-index lookup only inspects siblings.
    instructions = 0
    interval = 1000
    budget = 250_000
    connect = store._connect

    def progress():
        nonlocal instructions
        instructions += interval
        return instructions > budget

    @contextmanager
    def measured_connection():
        with connect() as db:
            db.set_progress_handler(progress, interval)
            try:
                yield db
            finally:
                db.set_progress_handler(None, 0)

    monkeypatch.setattr(store, "_connect", measured_connection)
    try:
        claimed = store.claim_batch(now=1600, audience="paid", size=10)
    except sqlite3.OperationalError as exc:
        if exc.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT:
            pytest.fail(f"Queue claim exceeded its {budget:,}-instruction SQLite budget")
        raise

    assert instructions <= budget
    assert claimed is not None and len(claimed["jobs"]) == 10
    assert claimed["id"] != blocked["id"]
    assert claimed["payload"] == envelopes[claimed["id"]]["payload"]
    assert {job["item_id"] for job in claimed["jobs"]} == {
        job["item_id"] for job in envelopes[claimed["id"]]["jobs"]
    }
