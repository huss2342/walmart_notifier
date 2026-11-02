"""Trace meaningful delivery transitions without changing delivery decisions."""

import json
import sqlite3

import pytest
from discord_bridge.api import DeliveryOutcome, DiscordClient, DiscordError
from discord_bridge.core import JobStore, normalize_item, route_keys
from discord_bridge.monitoring import DeliveryMonitor, error_fields
from discord_bridge.request_commands import RequestGateway
from discord_bridge.request_runtime import RequestRuntime
from discord_bridge.service import deliver_due


def product(number=10000001):
    return normalize_item({"item_id": f"ip-{number}", "title": "Private product title",
                           "value_usd": 120, "category": "electronics", "available": True})


def records(monitor):
    return [json.loads(line) for line in monitor.path.read_text().splitlines()]


def test_intake_receipt_retains_every_public_id_without_product_payloads(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    items = [product(10000000 + n) for n in range(40)]
    monitor.intake({"accepted": 40, "new": 1, "duplicates": 39, "queued": 1}, items=items)
    row = records(monitor)[0]
    assert row["observation_item_ids"] == [item["item_id"] for item in items]
    assert row["observations"] == 40 and row["duplicates"] == 39
    assert "Private" not in json.dumps(row)


def test_observation_ids_have_independent_bounds_from_dispatch_ids(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    ids = [f"ip-{10000000 + n}" for n in range(600)]
    monitor.emit("intake", observation_item_ids=ids, item_ids=ids, payload="Private")
    row = records(monitor)[0]
    assert row["observation_item_ids"] == ids[:500]
    assert row["item_ids"] == ids[:10]
    assert "Private" not in json.dumps(row)


def test_http_context_does_not_replace_numeric_watch_request_ids(tmp_path):
    from discord_bridge.telemetry_context import bind_request

    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    with bind_request("a" * 32):
        monitor.emit("request_match", request_id=123, matched=1)
    monitor.emit("request_match", request_id=123, matched=0)
    first, second = records(monitor)
    assert first["http_request_id"] == "a" * 32
    assert first["request_id"] == second["request_id"] == 123
    assert "http_request_id" not in second


def test_route_receipts_follow_commit_and_exclude_repeated_observations(tmp_path):
    store = JobStore(tmp_path / "queue.sqlite3")
    events = []

    def capture(event, **fields):
        # The sink runs after the write transaction: another reader sees it.
        with sqlite3.connect(store.path) as db:
            assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] > 0
        events.append((event, fields))

    item = product()
    store.record([item], now=1000, event_sink=capture)
    routed = [fields for event, fields in events if event == "observation_routed"]
    assert {event["route"] for event in routed} == set(route_keys(item))
    assert all(event["item_ids"] == [item["item_id"]] for event in routed)
    assert all(event["transition"] == "queued" for event in routed)
    assert all(len(event["job_ids"]) == 1 for event in routed)
    events.clear()
    store.record([item], now=1001, event_sink=capture)
    assert not events


def test_failed_intake_emits_no_committed_routing_receipt(tmp_path, monkeypatch):
    import discord_bridge.core as core

    store = JobStore(tmp_path / "queue.sqlite3")
    events = []

    def fail_match(*_args, **_kwargs):
        raise sqlite3.OperationalError("Private storage failure")

    monkeypatch.setattr(core, "match_requests", fail_match)
    with pytest.raises(sqlite3.OperationalError):
        store.record([product()], now=1000, event_sink=lambda *args, **kwargs: events.append(args))
    assert not events
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_transport_metadata_preserves_outcome_without_exception_text(tmp_path):
    error = DiscordError("Private connection details", outcome=DeliveryOutcome.NOT_SUBMITTED,
                         transport_error="TimeoutError")
    fields = error_fields(error)
    assert fields["outcome"] == "not_submitted"
    assert fields["transport_error"] == "TimeoutError"
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    monitor.emit("dispatch_retry", **fields)
    row = records(monitor)[0]
    assert row["outcome"] == "not_submitted"
    assert row["transport_error"] == "TimeoutError"
    assert "Private" not in json.dumps(row)


def test_lifecycle_labels_are_fixed_and_arbitrary_provider_fields_are_dropped(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    monitor.emit("worker_lifecycle", component="referrals", transition="started",
                 token="Private", member_id="Private", response="Private")
    row = records(monitor)[0]
    assert row["component"] == "referrals" and row["transition"] == "started"
    assert "Private" not in json.dumps(row)


def test_burst_routes_keep_all_500_job_receipts_in_five_file_rows(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    store = JobStore(tmp_path / "queue.sqlite3")
    items = [product(10000000 + n) for n in range(500)]
    result = store.record(items, now=1000, event_sink=monitor.emit)
    monitor.intake(result, items=items)
    events = records(monitor)
    assert len(events) == 5
    routes = [event for event in events if event["event"] == "observation_routed"]
    for event in routes:
        assert len(event["observation_item_ids"]) == len(event["observation_job_ids"]) == 500
        assert len(event["item_ids"]) == len(event["job_ids"]) == 10
        assert event["items"] == 500
        with sqlite3.connect(store.path) as db:
            actual = dict(db.execute("SELECT id,item_id FROM jobs WHERE route=?",
                                     (event["route"],)))
        assert dict(zip(event["observation_job_ids"], event["observation_item_ids"],
                        strict=True)) == actual


def test_transport_captures_actual_socket_error_class_without_repeating_a_post():
    calls = []

    class OfflineTransport:
        def open(self, request, timeout):
            calls.append(request)
            raise ConnectionResetError("Private socket or account information")

    client = DiscordClient("offline-token")
    client._opener = OfflineTransport()
    try:
        with pytest.raises(DiscordError) as raised:
            client.request("POST", "/channels/1700000000000000001/messages", {"content": "Private"})
        assert error_fields(raised.value)["transport_error"] == "ConnectionResetError"
        assert error_fields(raised.value)["outcome"] == "unknown"
        assert "Private" not in str(raised.value)
        assert len(calls) == 1
    finally:
        client.close()


def test_confirmed_send_logs_claim_intent_and_settlement_for_the_same_envelope(tmp_path):
    store = JobStore(tmp_path / "queue.sqlite3")
    store.record([product()], now=1000)
    with store._connect() as db:
        db.execute("UPDATE jobs SET state='skipped' WHERE route!='pro-all'")
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)

    class Provider:
        def request(self, *_args):
            return {"id": "1700000000000000002"}

    result = deliver_due(store, Provider(), {"guild_id": "1700000000000000003",
        "channels": {"pro-all": "1700000000000000001"}}, live=True, now=1000,
        auditor=lambda *_args: {"ok": True}, event_sink=monitor.emit)
    events = [event for event in records(monitor) if event["event"].startswith("dispatch_")]
    assert [event["event"] for event in events] == [
        "dispatch_claimed", "dispatch_sending", "dispatch_sent"
    ]
    assert len({event["dispatch_id"] for event in events}) == 1
    assert all(event["item_ids"] == [product()["item_id"]] for event in events)
    assert result["sent"] == 1 and store.counts()["sent"] == 1


def test_runtime_prepare_reports_configuration_failure_once_without_poll_spam(tmp_path):
    events = []
    runtime = RequestRuntime(None, {}, tmp_path / "queue.sqlite3", token="",
                             event_sink=lambda event, **fields: events.append((event, fields)))
    assert runtime.prepare() is runtime.prepare() is False
    assert len(events) == 1
    event, fields = events[0]
    assert event == "worker_error" and fields["error_type"] == "configuration"
    assert fields["component"] == "request-runtime"


def test_gateway_logs_ready_disconnect_and_recovery_without_repeating_ready():
    events = []
    gateway = RequestGateway(None, {}, None, token="", enabled=False,
                             event_sink=lambda event, **fields: events.append((event, fields)))
    gateway._event("gateway_ready")
    gateway._event("gateway_ready")
    gateway._event("gateway_disconnected")
    gateway._event("gateway_ready")
    assert [fields["transition"] for _, fields in events] == ["ready", "disconnected", "ready"]
    assert all(event == "gateway_state" for event, _ in events)


def test_welcome_and_membership_cycles_have_distinct_operational_components(tmp_path):
    from test_discord_membership import make as membership
    from test_discord_welcome import make as welcome

    events = []
    for factory in (welcome, membership):
        worker, _, _ = factory(tmp_path)
        worker.event_sink = lambda event, **fields: events.append((event, fields))
        worker.tick()
    cycles = [fields for event, fields in events if event == "welcome_cycle"]
    assert {fields["component"] for fields in cycles} == {"welcome", "membership"}
