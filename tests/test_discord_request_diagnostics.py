"""Personal alert silence must be explainable without logging member queries."""

import json
import sqlite3

import pytest
from discord_bridge.api import DiscordError
from discord_bridge.core import JobStore
from discord_bridge.monitoring import DeliveryMonitor
from discord_bridge.request_store import RequestStore

from test_discord_request_delivery import setup
from test_discord_requests import GUILD, USER, product


def records(monitor):
    return [json.loads(line) for line in monitor.path.read_text().splitlines()]


def test_watch_lifecycle_survives_deletion_in_sanitized_logs(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    store = RequestStore(tmp_path / "q", guild_id=GUILD, event_sink=monitor.emit)
    row = store.add(USER, "private solar query", min_value=100, now=10)
    store.edit(USER, row["id"], "private other query", now=11)
    assert store.delete(USER, row["id"])
    assert not store.delete(USER, row["id"])
    events = [r for r in records(monitor) if r["event"] == "request_changed"]
    assert [r["action"] for r in events] == ["add", "edit", "delete"]
    assert {r["request_id"] for r in events} == {row["id"]}
    assert len({r["member_ref"] for r in events}) == 1
    assert events[0]["query_ref"] != events[1]["query_ref"]
    assert [r["min_value_cents"] for r in events] == [10000, 10000, 10000]
    assert "private" not in json.dumps(events) and USER not in json.dumps(events)
    assert store.list(USER) == []


def test_observations_explain_sent_duplicates_after_twenty_minutes(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    store, _, provider, worker = setup(tmp_path, event_sink=monitor.emit)
    queue = JobStore(store.path)
    assert worker.tick(now=12)["sent"] == 1
    queue.record([product()], now=1300, request_event_sink=monitor.emit)
    queue.record([product(number="987654321")], now=1301, request_event_sink=monitor.emit)
    assert worker.tick(now=1302)["sent"] == 1
    events = [r for r in records(monitor) if r["event"] == "request_match"]
    assert events[0]["deduplicated"] == 1 and events[0]["queued"] == 0
    assert events[1]["queued"] == 1 and events[1]["matched"] == 1
    sends = [r for r in records(monitor) if r["event"] == "dispatch_sent"]
    assert all(r["audience"] == "personal" and r["dispatch_id"] for r in sends)
    assert sends[0]["dispatch_id"] != sends[1]["dispatch_id"]
    assert len(provider.messages) == 2


def test_personal_heartbeat_explains_blocked_and_expired_queue(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    store, _, _, worker = setup(tmp_path, event_sink=monitor.emit)
    store.set_dm_status(USER, "blocked")
    worker.tick(now=12)
    worker.tick(now=13)
    worker.tick(now=1310)
    events = [r for r in records(monitor) if r["event"] == "request_queue_summary"]
    assert len(events) == 2
    assert events[0]["queue"]["dm_blocked"] == 1
    assert events[0]["queue"]["due"] == 1
    assert events[1]["queue"]["expired"] == 1
    assert events[1]["queue"]["reasons"]["expired"] == {"stale": 1}


def test_logging_failure_cannot_rollback_matching_or_watch_changes(tmp_path):
    def broken(*args, **kwargs):
        raise OSError("private failure")

    store = RequestStore(tmp_path / "q", guild_id=GUILD, event_sink=broken)
    row = store.add(USER, "air fryer", now=10)
    JobStore(store.path).record([product()], now=11, request_event_sink=broken)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM personal_request_alerts").fetchone()[0] == 1
    assert store.list(USER)[0]["id"] == row["id"]


@pytest.mark.parametrize("provider_failure", [False, True])
def test_secondary_database_failure_is_logged_without_killing_sender(
    tmp_path, monkeypatch, provider_failure
):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    store, _, provider, worker = setup(tmp_path, event_sink=monitor.emit)
    original_retry = store.retry

    def locked(*args, **kwargs):
        error = sqlite3.OperationalError("PRIVATE database details")
        error.sqlite_errorcode = 5
        raise error

    if provider_failure:
        provider.member_error = DiscordError(status=503)
    else:
        provider.on_member = locked
    monkeypatch.setattr(store, "retry", locked)
    worker.tick(now=12)
    errors = [r for r in records(monitor) if r["event"] == "worker_error"]
    assert any(r["phase"] == "recovery" and r["sqlite_error_code"] == 5 for r in errors)
    assert all(r["audience"] == "personal" for r in errors)
    assert "PRIVATE" not in monitor.path.read_text()
    monkeypatch.setattr(store, "retry", original_retry)
    provider.member_error = provider.on_member = None
    assert worker.tick(now=80)["sent"] == 1
    assert len(provider.messages) == 1


def test_blocked_dm_and_throttle_record_provider_code_and_job(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    store, _, provider, worker = setup(tmp_path, event_sink=monitor.emit)
    provider.send_error = DiscordError(status=429, retry_after=2)
    assert worker.tick(now=12)["throttled"] == 1
    provider.send_error = DiscordError(status=403, provider_code=50007)
    assert worker.tick(now=15)["blocked"] == 1
    errors = [r for r in records(monitor) if r["event"] == "request_provider_error"]
    assert [r["status"] for r in errors] == [429, 403]
    assert errors[1]["provider_code"] == 50007 and errors[1]["phase"] == "send"
    assert len({r["dispatch_id"] for r in errors}) == 1
    assert any(r.get("reason") == "dm_blocked" for r in records(monitor))
    assert store.dm_status(USER) == "blocked"


def test_events_are_emitted_only_after_transaction_commits(tmp_path):
    path = tmp_path / "q"
    observed = []

    def sink(event, **fields):
        with sqlite3.connect(path) as db:
            observed.append(
                (event, db.execute("SELECT COUNT(*) FROM personal_requests").fetchone()[0])
            )

    store = RequestStore(path, guild_id=GUILD, event_sink=sink)
    row = store.add(USER, "solar", now=10)
    store.delete(USER, row["id"])
    assert observed == [("request_changed", 1), ("request_changed", 0)]
