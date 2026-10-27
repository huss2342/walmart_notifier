"""Persistent operational evidence is bounded, sanitized and delivery-independent."""

import gzip
import json
import os

import pytest
from discord_bridge.api import DiscordError
from discord_bridge.core import JobStore
from discord_bridge.delivery import deliver_due
from discord_bridge.monitoring import DeliveryMonitor

from test_discord_burst_delivery import STATE, Remote
from test_discord_burst_store import only_route, product


def records(path):
    if path.suffix == ".gz":
        return [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_provider_rejections_keep_numeric_diagnostics_without_raw_error_bodies(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    assert monitor.emit(
        "dispatch_retry",
        status=413,
        provider_code=40005,
        request_bytes=15578,
        response_bytes=81,
        provider_body="PRIVATE RESPONSE",
    )
    row = records(monitor.path)[0]
    assert (row["provider_code"], row["request_bytes"], row["response_bytes"]) == (40005, 15578, 81)
    assert "PRIVATE" not in json.dumps(row)
    assert monitor.emit(
        "dispatch_retry",
        provider_code="PRIVATE",
        request_bytes=float("inf"),
        response_bytes=-1,
    )
    assert (
        not {"provider_code", "request_bytes", "response_bytes"} & records(monitor.path)[1].keys()
    )


def test_health_distinguishes_payload_review_holds_from_unclassified_failures(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1)], now=1000)
    only_route(store, "pro-all")
    batch = store.claim_batch(now=1000)
    store.retry_batch(batch, now=1000, delay=1, reason="remote_retry", ambiguous=True)
    batch = store.claim_batch(now=1001)
    assert store.split_rejected_batch(batch, now=1001) == "held"
    metrics = store.delivery_metrics(now=1002)
    assert metrics["reasons"]["pending"] == {"http_413_review": 1}


def test_welcome_events_keep_counters_but_never_member_data(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    assert monitor.emit(
        "welcome_cycle",
        phase="welcome",
        sent=1,
        reconciled=1,
        review=0,
        deferred=False,
        baseline=True,
        throttled=False,
        user_id="PRIVATE",
        nonce="PRIVATE",
        content="PRIVATE",
    )
    row = records(monitor.path)[0]
    assert row["phase"] == "welcome" and row["sent"] == row["reconciled"] == 1
    assert row["baseline"] is True and row["deferred"] is False
    assert "PRIVATE" not in json.dumps(row)


def test_json_log_allowlists_fields_and_correlates_without_payloads(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    assert monitor.emit(
        "dispatch_sent",
        correlation_id="a" * 32,
        dispatch_id="b" * 25,
        route="pro-all",
        job_ids=["c" * 25],
        value_cents=[19999],
        items=1,
        payload={"token": "PRIVATE"},
        exception="PRIVATE body",
        user_id="PRIVATE",
        title="PRIVATE",
    )
    row = records(monitor.path)[0]
    assert row["event"] == "dispatch_sent"
    assert row["entry_point"] == "collector_worker"
    assert row["correlation_id"] == "a" * 32
    assert row["dispatch_id"] == "b" * 25
    assert row["value_cents"] == [19999]
    assert "PRIVATE" not in json.dumps(row)
    assert len(row["run_id"]) == 32


def test_rotation_keeps_a_bounded_number_of_complete_json_files(tmp_path):
    monitor = DeliveryMonitor(
        tmp_path, entry_point="collector_worker", console=False, max_bytes=700, backups=2
    )
    for _ in range(30):
        monitor.emit("worker_error", error_type="RuntimeError", phase="worker")
    paths = list(tmp_path.glob("delivery.jsonl*"))
    assert len(paths) == 3
    assert all(records(path) for path in paths)
    assert all(path.stat().st_size <= 700 for path in paths)


def test_delivery_monitor_reports_sixty_day_compressed_retention(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    status = monitor.status()
    assert status["retention_days"] == 60
    assert status["total_max_bytes"] == 32 * 1024 * 1024
    assert status["compressed_archives"] is True


def test_paid_envelope_logs_all_ten_members_with_a_fixed_upper_bound(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    job_ids = [f"{index:032x}" for index in range(12)]
    item_ids = [f"ip-{index}" for index in range(12)]
    values = [50000 + index for index in range(12)]
    assert monitor.emit(
        "dispatch_sent", route="pro-all", job_ids=job_ids, item_ids=item_ids, value_cents=values
    )
    row = records(monitor.path)[0]
    assert row["job_ids"] == job_ids[:10]
    assert row["item_ids"] == item_ids[:10]
    assert row["value_cents"] == values[:10]


def test_short_file_write_completes_one_valid_event(tmp_path, monkeypatch):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    original = os.write
    monkeypatch.setattr(os, "write", lambda descriptor, data: original(descriptor, data[:7]))
    assert monitor.emit("worker_error", phase="worker", error_type="RuntimeError")
    assert records(monitor.path)[0]["event"] == "worker_error"


def test_queue_heartbeat_distinguishes_future_delay_from_overdue_retry(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)

    class Store:
        def counts(self):
            return {"pending": 7, "sent": 20}

        def delivery_metrics(self, *, now):
            return {
                "due_items": 2,
                "by_tier": {
                    "pro": {"due": 2, "retry_wait": 2, "future": 0},
                    "free": {"due": 0, "retry_wait": 0, "future": 5},
                },
                "recent_delivery_latency_seconds": {
                    "pro": {"samples": 20, "p50": 0.5, "p95": 3.1, "max": 4.0}
                },
                "reasons": {"expired": {"stale": 4}},
                "payload": {"token": "PRIVATE"},
            }

    assert monitor.heartbeat(Store(), now=1000)
    assert not monitor.heartbeat(Store(), now=1059)
    assert monitor.heartbeat(Store(), now=1060)
    rows = records(monitor.path)
    assert len(rows) == 2
    assert rows[0]["queue"]["by_tier"]["pro"]["retry_wait"] == 2
    assert rows[0]["queue"]["by_tier"]["free"]["future"] == 5
    assert rows[0]["queue"]["recent_delivery_latency_seconds"]["pro"]["p95"] == 3.1
    assert "PRIVATE" not in json.dumps(rows)


def run(store, remote, sink):
    return deliver_due(
        store,
        remote,
        STATE,
        live=True,
        now=1001,
        auditor=lambda *_: {"ok": True},
        event_sink=sink,
        correlation_id="d" * 32,
    )


def test_sent_and_throttle_events_follow_the_same_durable_dispatch(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1)], now=1000)
    only_route(store, "pro-all")
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    first = run(store, Remote(blocked=STATE["channels"]["pro-all"]), monitor.emit)
    assert first["retried"] == 1
    throttle = next(row for row in records(monitor.path) if row["event"] == "dispatch_throttled")
    assert throttle["scope"] == "route" and throttle["retry_after_seconds"] == 30
    deliver_due(
        store,
        Remote(),
        STATE,
        live=True,
        now=1031,
        auditor=lambda *_: {"ok": True},
        event_sink=monitor.emit,
    )
    sent = next(row for row in records(monitor.path) if row["event"] == "dispatch_sent")
    assert sent["dispatch_id"] == throttle["dispatch_id"]
    assert sent["job_ids"] == throttle["job_ids"]
    assert sent["due_delay_seconds_max"] == 31
    assert sent["value_cents"] == [1999]


def test_permanent_failure_and_privacy_block_are_sanitized(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1)], now=1000)
    only_route(store, "pro-all")
    monitor = DeliveryMonitor(tmp_path, entry_point="standalone_worker", console=False)

    class Denied:
        def request(self, *_):
            raise DiscordError("PRIVATE provider response", status=403)

    assert run(store, Denied(), monitor.emit)["failed"] == 1
    failure = next(row for row in records(monitor.path) if row["event"] == "dispatch_failed")
    assert failure["status"] == 403 and failure["reason"] == "http_403"
    store.record([product(2)], now=1000)
    result = deliver_due(
        store,
        Remote(),
        STATE,
        live=True,
        now=1001,
        auditor=lambda *_: {"ok": False, "findings": ["PRIVATE"]},
        event_sink=monitor.emit,
    )
    assert result["blocked"]
    assert any(row["event"] == "privacy_blocked" for row in records(monitor.path))
    assert "PRIVATE" not in monitor.path.read_text(encoding="utf-8")


def test_broken_event_sink_cannot_turn_a_successful_send_into_a_retry(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1)], now=1000)
    only_route(store, "pro-all")
    remote = Remote()

    def broken(*_, **__):
        raise OSError("PRIVATE inaccessible file")

    assert run(store, remote, broken)["sent"] == 1
    assert run(store, remote, broken)["sent"] == 0
    assert len(remote.calls) == 1 and store.counts()["sent"] == 1


def test_intake_summary_and_monitor_failure_do_not_disclose_inputs(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    monitor.intake({"new": 2, "duplicates": 1, "queued": 2, "token": "PRIVATE"}, seed_only=False)
    row = records(monitor.path)[0]
    assert row["event"] == "intake" and row["queued"] == 2
    assert "PRIVATE" not in json.dumps(row)
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("", encoding="utf-8")
    broken = DeliveryMonitor(blocked, entry_point="collector_worker", console=False)
    assert not broken.emit("worker_error", error_type="RuntimeError", phase="worker")
    assert broken.status()["last_error"] == "file_unavailable"
    assert broken.status()["dropped_events"] == 1


def test_unexpected_dispatch_error_is_correlated_without_exception_text(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1)], now=1000)
    only_route(store, "pro-all")
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)

    class Broken:
        def request(self, *_):
            raise RuntimeError("PRIVATE raw body")

    with pytest.raises(RuntimeError):
        run(store, Broken(), monitor.emit)
    row = next(row for row in records(monitor.path) if row["event"] == "worker_error")
    assert row["phase"] == "dispatch" and row["ambiguous"] is True
    assert row["error_type"] == "RuntimeError" and row["job_ids"]
    assert row["correlation_id"] == "d" * 32
    assert store.counts()["leased"] == 1  # Persisted recovery, no speculative resend.
    assert "PRIVATE" not in monitor.path.read_text(encoding="utf-8")


def test_idle_collector_emits_persistent_heartbeat_and_exposes_log_health(tmp_path):
    from notifiers.discord import DiscordNotifier

    notifier = DiscordNotifier(JobStore(tmp_path / "q"))
    notifier.deliver_once()
    notifier.deliver_once()
    rows = records(notifier.monitor.path)
    assert len(rows) == 1 and rows[0]["event"] == "queue_summary"
    assert rows[0]["queue"]["due_items"] == 0
    assert rows[0]["queue"]["by_tier"]["pro"]["due"] == 0
    assert rows[0]["queue"]["recent_delivery_latency_seconds"]["pro"]["p95"] is None
    assert notifier.health()["monitoring"]["last_error"] is None


@pytest.mark.parametrize("audit_error", [False, True])
def test_standalone_startup_audit_refusal_is_persisted_before_worker_starts(
    tmp_path, monkeypatch, audit_error, capsys
):
    from discord_bridge import __main__ as cli

    monkeypatch.setattr(cli, "load_settings", lambda: None)
    monkeypatch.setattr(cli, "read_state", lambda *_: STATE)
    monkeypatch.setattr(cli, "DiscordClient", lambda *_: object())

    def refused(*_):
        if audit_error:
            raise DiscordError("PRIVATE provider response", status=403)
        return {"ok": False}

    monkeypatch.setattr("discord_bridge.provision.audit", refused)
    assert cli.main(["serve", "--live", "--database", str(tmp_path / "q")]) == 2
    rows = records(tmp_path / "delivery.jsonl")
    assert [row["event"] for row in rows] == ["privacy_blocked", "queue_summary"]
    assert rows[0]["entry_point"] == "standalone_worker"
    assert "PRIVATE" not in json.dumps(rows)
    assert "PRIVATE" not in capsys.readouterr().err
