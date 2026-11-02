"""Retired destinations never enqueue or reach Discord, including old retries."""

import json

import pytest
from discord_bridge.core import JobStore, normalize_item, route_keys
from discord_bridge.delivery import deliver_due

from test_discord_burst_delivery import STATE, Remote


def product(number=0, value=19.99):
    return normalize_item(
        {
            "item_id": f"ip-{90000000 + number}",
            "title": "Desk lamp",
            "category": "electronics",
            "value_usd": value,
        }
    )


def legacy_job(store, *, state="pending", number=0, frozen=False, future=False):
    """Restore an old-version persisted row, without enabling retired routing."""
    store.record([product(number)], now=1000)
    with store._connect() as db:
        row = dict(
            db.execute(
                "SELECT * FROM jobs WHERE item_id=? AND route='pro-all'",
                (product(number)["item_id"],),
            ).fetchone()
        )
        db.execute("DELETE FROM jobs WHERE item_id=? AND route='starter-items'", (row["item_id"],))
        row.update(
            id=f"legacy-starter-{number}",
            route="starter-items",
            state=state,
            due=5000 if future else 1300,
            expires=10000,
            next_attempt=5000 if future else 1300,
            lease_token="old-lease" if state == "leased" else None,
            lease_until=9000 if state == "leased" else None,
            attempts=1 if frozen else 0,
            batch_id=f"legacy-dispatch-{number}" if frozen else None,
            message_id="1800000000000000001" if state == "sent" else None,
        )
        db.execute(
            "INSERT INTO jobs (" + ",".join(row) + ") VALUES (" + ",".join("?" for _ in row) + ")",
            tuple(row.values()),
        )
        if frozen:
            db.execute(
                "INSERT INTO dispatches (id,route,members,payload,state,uncertain) "
                "VALUES (?,?,?,?,?,1)",
                (
                    row["batch_id"],
                    row["route"],
                    json.dumps([row["id"]]),
                    '{"content":"old frozen alert"}',
                    state,
                ),
            )
    return row


@pytest.mark.parametrize("value", [0.01, 10, 19.99, 39.99, 40, 299, 0, None])
def test_new_observations_only_use_free_and_pro_routes(value):
    routes = route_keys(product(value=value))
    assert "starter-items" not in routes
    assert "pro-all" in routes and "type-electronics" in routes
    assert "yesterday-recap" in routes


def test_new_low_value_item_keeps_pro_immediate_and_free_at_24_hours(tmp_path):
    store = JobStore(tmp_path / "new.sqlite3")
    store.record([product()], now=1000)
    with store._connect() as db:
        due = dict(db.execute("SELECT route,due FROM jobs"))
    assert due == {
        "pro-all": 1000,
        "type-electronics": 1000,
        "price-10-20": 1000,
        "yesterday-recap": 87400,
    }


@pytest.mark.parametrize("state", ["pending", "leased"])
@pytest.mark.parametrize("frozen", [False, True])
def test_restart_retires_queued_starter_without_touching_other_jobs(tmp_path, state, frozen):
    store = JobStore(tmp_path / "legacy.sqlite3")
    old = legacy_job(store, state=state, frozen=frozen, future=True)
    with store._connect() as db:
        others = [
            tuple(row) for row in db.execute("SELECT * FROM jobs WHERE route!='starter-items'")
        ]
    for _ in range(2):  # Repeated restarts must preserve history and all active feeds.
        store = JobStore(store.path)
        with store._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (old["id"],)).fetchone()
            assert row["state"] == "skipped" and row["reason"] == "route_retired"
            assert row["lease_token"] is None and row["lease_until"] is None
            assert row["attempts"] == old["attempts"] and row["batch_id"] == old["batch_id"]
            assert [
                tuple(r) for r in db.execute("SELECT * FROM jobs WHERE route!='starter-items'")
            ] == others
            if frozen:
                dispatch = db.execute(
                    "SELECT * FROM dispatches WHERE id=?", (old["batch_id"],)
                ).fetchone()
                assert dispatch["state"] == "skipped"
                assert dispatch["payload"] == '{"content":"old frozen alert"}'
                assert dispatch["uncertain"] == 1


@pytest.mark.parametrize("state", ["sent", "failed", "expired", "skipped"])
def test_restart_preserves_terminal_starter_history(tmp_path, state):
    store = JobStore(tmp_path / "history.sqlite3")
    old = legacy_job(store, state=state, frozen=True)
    store = JobStore(store.path)
    with store._connect() as db:
        assert dict(db.execute("SELECT * FROM jobs WHERE id=?", (old["id"],)).fetchone()) == old
        assert (
            db.execute("SELECT state FROM dispatches WHERE id=?", (old["batch_id"],)).fetchone()[0]
            == state
        )


@pytest.mark.parametrize("restart", [False, True])
def test_old_pending_and_frozen_starter_never_reach_discord(tmp_path, restart):
    store = JobStore(tmp_path / "delivery.sqlite3")
    legacy_job(store, number=0)
    legacy_job(store, number=1, frozen=True)
    if restart:
        store = JobStore(store.path)

    class InspectRemote(Remote):
        def __init__(self):
            super().__init__()
            self.cooldown_checks = []

        def cooldown_delay(self, method, path):
            self.cooldown_checks.append(path)
            return 0

    remote = InspectRemote()
    result = deliver_due(store, remote, STATE, live=True, now=1300, auditor=lambda *_: {"ok": True})
    assert result["sent"] == 6
    assert all(STATE["channels"]["starter-items"] not in path for path, _ in remote.calls)
    assert all(STATE["channels"]["starter-items"] not in path for path in remote.cooldown_checks)
    assert {path for path, _ in remote.calls} == {
        f"/channels/{STATE['channels'][key]}/messages"
        for key in ("pro-all", "type-electronics", "price-10-20")
    }
