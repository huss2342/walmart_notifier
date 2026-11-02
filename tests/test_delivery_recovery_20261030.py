"""Real durable jobs, synthetic storage failures and a fake Discord transport."""

import json
import sqlite3

from discord_bridge.monitoring import DeliveryMonitor, error_fields
from discord_bridge.request_delivery import RequestWorker
from discord_bridge.request_store import RequestStore

from test_discord_burst_delivery import Remote, run
from test_discord_burst_store import only_route, product
from test_discord_monitoring import records
from test_discord_request_delivery import STATE, setup


def storage_failure(*_args, **_kwargs):
    with sqlite3.connect(":memory:") as db:
        db.execute("SELECT * FROM PRIVATE_missing_table")


def test_original_storage_failure_precedes_recovery_failure_and_idle_poll_recovers(
    tmp_path, monkeypatch
):
    events = []
    store, _, provider, worker = setup(
        tmp_path, event_sink=lambda event, **fields: events.append({"event": event, **fields})
    )
    validate, retry = store.validate, store.retry
    monkeypatch.setattr(store, "validate", storage_failure)
    monkeypatch.setattr(store, "retry", storage_failure)
    assert worker.tick(now=12)["retried"] == 0
    failures = [event for event in events if event["event"] == "worker_error"]
    assert [event["phase"] for event in failures] == ["validate", "recovery"]
    assert all(event["sqlite_primary_code"] == sqlite3.SQLITE_ERROR for event in failures)
    assert all(event["sqlite_error_name"] == "SQLITE_ERROR" for event in failures)
    assert provider.messages == []
    assert store.stats()["leased"] == 1
    monkeypatch.setattr(store, "validate", validate)
    monkeypatch.setattr(store, "retry", retry)
    worker.tick(now=13)  # The unexpired lease is not reclaimed or posted.
    health = worker.health()
    assert health["last_poll_at"] == health["recovered_at"] == 13
    assert health["last_poll_status"] == "ok"
    assert health["last_failure_at"] == 12
    assert health["last_error"] is None
    assert health["last_success"] is None
    assert "PRIVATE" not in json.dumps(events)


def test_failed_review_persistence_keeps_sending_state_and_never_repeats(tmp_path, monkeypatch):
    events = []
    store, _, provider, worker = setup(
        tmp_path, event_sink=lambda event, **fields: events.append({"event": event, **fields})
    )
    monkeypatch.setattr(store, "settle", storage_failure)
    assert worker.tick(now=12)["sent"] == 0
    assert len(provider.messages) == 1
    assert store.stats()["sending"] == 1
    failures = [event for event in events if event["event"] == "worker_error"]
    assert [event["phase"] for event in failures] == ["settle", "recovery"]
    assert all(event["ambiguous"] for event in failures)
    reopened = RequestStore(store.path)
    restarted = RequestWorker(provider, STATE, reopened, enabled=True)
    restarted.tick(now=1000)
    assert len(provider.messages) == 1
    assert reopened.stats()["review"] == 1


def test_idle_poll_does_not_erase_unverified_membership_error(tmp_path):
    from discord_bridge.api import DiscordError

    _, _, provider, worker = setup(tmp_path)
    provider.member_error = DiscordError(status=503)
    worker.tick(now=12)
    worker.tick(now=13)
    assert worker.health()["last_error"] == "membership_unverified"
    assert worker.health()["last_poll_status"] == "ok"
    assert worker.health()["last_success"] is None


def test_outer_sender_loop_survives_and_backs_off_until_cancelled(tmp_path, monkeypatch):
    class Stop:
        def __init__(self):
            self.waits = []

        def is_set(self):
            return len(self.waits) >= 4

        def wait(self, seconds):
            self.waits.append(seconds)

    class InlineThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

        def is_alive(self):
            return False

    stop = Stop()
    _, _, _, worker = setup(tmp_path, stop_event=stop)
    monkeypatch.setattr(worker, "tick", storage_failure)
    monkeypatch.setattr("discord_bridge.request_delivery.threading.Thread", InlineThread)
    worker.start()
    assert stop.waits == [2, 4, 8, 16]
    assert worker.health()["last_error"] == "worker_error"


def test_claim_and_send_trace_preserve_original_deadline_after_retry(tmp_path):
    from discord_bridge.core import JobStore

    store = JobStore(tmp_path / "q")
    store.record([product(1)], now=1000)
    only_route(store, "pro-all")
    batch = store.claim_batch(now=1000)
    store.retry_batch(batch, now=1000, delay=1200, reason="remote_retry", ambiguous=False)
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    assert run(store, Remote(), now=2200, event_sink=monitor.emit)["sent"] == 1
    rows = records(monitor.path)
    claimed = next(row for row in rows if row["event"] == "dispatch_claimed")
    sent = next(row for row in rows if row["event"] == "dispatch_sent")
    assert claimed["dispatch_id"] == sent["dispatch_id"]
    assert claimed["due_at_min"] == claimed["due_at_max"] == 1000
    assert claimed["next_attempt_at_min"] == claimed["next_attempt_at_max"] == 2200
    assert claimed["due_delay_seconds_max"] == sent["due_delay_seconds_max"] == 1200
    assert claimed["attempt"] == sent["attempt"] == 2


def test_sqlite_diagnostics_use_numeric_constants_not_exception_text(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    error = sqlite3.OperationalError("PRIVATE SQL and credentials")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY_SNAPSHOT
    error.sqlite_errorname = "PRIVATE arbitrary name"
    assert monitor.emit("worker_error", phase="validate", **error_fields(error))
    row = records(monitor.path)[0]
    assert row["sqlite_error_code"] == sqlite3.SQLITE_BUSY_SNAPSHOT
    assert row["sqlite_primary_code"] == sqlite3.SQLITE_BUSY
    assert row["sqlite_error_name"] == "SQLITE_BUSY_SNAPSHOT"
    fake = ValueError("PRIVATE SQL")
    fake.sqlite_errorcode = sqlite3.SQLITE_ERROR
    assert error_fields(fake) == {"error_type": "ValueError"}
    assert monitor.emit("worker_error", sqlite_error_name="PRIVATE arbitrary name")
    assert "PRIVATE" not in monitor.path.read_text()


def test_health_reports_unavailable_queue_without_fabricated_empty_counts(tmp_path, monkeypatch):
    store, _, _, worker = setup(tmp_path)
    monkeypatch.setattr(store, "stats", storage_failure)
    health = worker.health()
    assert health["queue_available"] is False
    assert health["queue"] == {}
    assert "PRIVATE" not in json.dumps(health)
