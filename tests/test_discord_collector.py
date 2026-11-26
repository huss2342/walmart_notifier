"""Collector-to-Discord boundary, with real local HTTP and an offline Discord API."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from discord_bridge.core import JobStore
from discord_bridge.layout import server_manifest

import server
from models import Item
from notifiers import build_notifier
from notifiers.discord import DiscordNotifier
from operational_logging import OperationalLog
from state import SeenStore


class FakeDiscord:
    def __init__(self):
        self.calls = []

    def request(self, method, route, payload=None):
        self.calls.append((method, route, payload))
        return {"id": str(1800000000000000000 + len(self.calls))}


def mapping():
    return {
        "guild_id": "1562530389866717295",
        "channels": {
            entry["key"]: str(1700000000000000000 + i)
            for i, entry in enumerate(server_manifest()["channels"])
        },
    }


@pytest.fixture
def discord_server(tmp_path, monkeypatch):
    monkeypatch.setenv("RULES_JSON", '{"rules":[{"name":"personal","min_value_usd":79}]}')
    monkeypatch.delenv("SEED_MODE", raising=False)
    client = FakeDiscord()
    notifier = DiscordNotifier(
        JobStore(tmp_path / "discord.sqlite3"),
        client=client,
        state=mapping(),
        live=True,
        auditor=lambda *_: {"ok": True},
    )
    store = SeenStore(tmp_path / "seen.json")
    monkeypatch.setattr(server.Handler, "notifier", notifier, raising=False)
    monkeypatch.setattr(server.Handler, "store", store, raising=False)
    monkeypatch.setattr(server.Handler, "token", "", raising=False)
    monkeypatch.setattr(
        server.Handler, "runtime_status", server.RuntimeStatus(store), raising=False
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", notifier, client
    finally:
        httpd.shutdown()
        httpd.server_close()
        store.close()


def post(base, entries, path="/ingest"):
    request = urllib.request.Request(
        base + path,
        json.dumps({"items": entries}).encode(),
        {"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def product(number=1234567, **extra):
    return {
        "item_id": f"ip-{number}",
        "title": "Oak dining table",
        "value_usd": 19.99,
        "url": f"https://www.walmart.com/ip/{number}",
        **extra,
    }


def test_http_ingest_routes_before_personal_filters_and_dedupe(discord_server):
    base, notifier, client = discord_server
    server.Handler.store.mark_seen("ip-1234567", "Old personal record", 19.99)
    summary = post(base, [product(claims_remaining=0, raw={"private": "do-not-publish"})])
    assert summary["seen"] == summary["new"] == summary["queued"] == 1
    assert summary["filtered"] == summary["notified"] == summary["failed"] == 0
    assert client.calls == []  # HTTP acknowledges durable intake, not a remote send.
    delivered = notifier.deliver_once()
    assert delivered["sent"] == 3  # Pro all + furniture + $10-20. Free waits.
    wire = json.dumps(client.calls)
    assert "do-not-publish" not in wire and "claims_remaining" not in wire
    assert "type-furniture" in notifier.state["channels"]
    paths = {call[1] for call in client.calls}
    assert f"/channels/{notifier.state['channels']['type-furniture']}/messages" in paths
    second = post(base, [product()])
    assert second["duplicates"] == 1 and second["queued"] == 0
    assert notifier.deliver_once()["sent"] == 0


def test_http_image_survives_queue_restart_and_reaches_existing_alert(discord_server):
    base, notifier, client = discord_server
    image = "https://i5.walmartimages.com/asr/table.jpeg"
    assert post(base, [product(image_url=image + "?tracking=private")])["queued"] == 1
    notifier.store = JobStore(notifier.store.path)
    assert notifier.deliver_once()["sent"] == 3
    assert len(client.calls) == 3  # No separate image message or upload.
    for _, _, payload in client.calls:
        assert payload["embeds"][0]["thumbnail"] == {"url": image}
        assert "components" not in payload
        assert "sampling" in payload["embeds"][0]["url"].lower()
        assert "private" not in json.dumps(payload)


def test_unavailable_and_unknown_values_reach_free_history(discord_server):
    base, notifier, _ = discord_server
    assert post(base, [product(1234568, available=False, value_usd=None)])["queued"] == 1
    assert notifier.deliver_once()["sent"] == 0
    with notifier.store._connect() as db:
        rows = db.execute("SELECT route,due,expires,payload_snapshot FROM jobs").fetchall()
    assert len(rows) == 1 and rows[0]["route"] == "yesterday-recap"
    assert json.loads(rows[0]["payload_snapshot"])["available"] is False


def test_failed_durable_intake_returns_failure_for_extension_retry(discord_server, monkeypatch):
    base, notifier, client = discord_server

    def fail(*_, **__):
        raise OSError("disk unavailable")

    monkeypatch.setattr(notifier.store, "record", fail)
    with pytest.raises(urllib.error.HTTPError) as error:
        post(base, [product()])
    assert error.value.code == 503 and client.calls == []


def test_unpublishable_observation_is_dropped_named_and_the_rest_of_the_page_queued(
    discord_server, tmp_path, monkeypatch, caplog
):
    # Nov 17-19: five whole pages failed with a bare 400 because of one item each.
    base, notifier, _ = discord_server
    monkeypatch.setattr(server.Handler, "operations", OperationalLog(tmp_path / "ops"))
    request = urllib.request.Request(
        base + "/ingest",
        json.dumps({"items": [
            product(),
            product(1234568, url="https://evil.example/ip/1234568", title="Secret title"),
            product(1234569, url="https://www.walmart.com/ip/Lamp/7654321"),
            product(1234570, value_usd=19.995),
            product(1234571, title="Bad\x00title"),
            {**product(1234572), "item_id": "not-an-ip-id"},
        ]}).encode(),
        {"Content-Type": "application/json", "X-Request-ID": "e" * 32},
        method="POST",
    )
    with caplog.at_level("WARNING"), urllib.request.urlopen(request, timeout=5) as response:
        summary = json.load(response)
    assert summary["seen"] == 6, "every observation is acknowledged, so the page advances"
    assert summary["queued"] == summary["new"] == 1 and summary["invalid"] == 5
    with notifier.store._connect() as db:
        stored = [row[0] for row in db.execute("SELECT item_id FROM items")]
        payloads = " ".join(row[0] for row in db.execute("SELECT payload FROM items"))
    assert stored == ["ip-1234567"]
    assert "evil.example" not in payloads
    assert server.Handler.store.is_new("ip-1234568"), "dropped items stay out of local history"
    assert not server.Handler.store.is_new("ip-1234567")
    rows = [
        json.loads(line)
        for line in (tmp_path / "ops" / "operations.jsonl").read_text().splitlines()
    ]
    dropped = [row for row in rows if row["event"] == "intake_invalid_item"]
    assert [(row.get("item_id"), row["field"], row["reason"]) for row in dropped] == [
        ("ip-1234568", "product_url", "foreign_host"),
        ("ip-1234569", "product_url", "different_item"),
        ("ip-1234570", "value", "fractional_cents"),
        ("ip-1234571", "title", "control_character"),
        (None, "item_id", "not_walmart_id"),
    ]
    assert {row["request_id"] for row in dropped} == {"e" * 32}
    result = next(row for row in rows if row["event"] == "intake_result")
    assert result["invalid"] == 5 and result["queued"] == 1
    logged = caplog.text
    assert "item_id=ip-1234569 field=product_url reason=different_item" in logged
    assert "item_id=malformed field=item_id reason=not_walmart_id" in logged
    for private in ("Secret title", "evil.example", "Lamp", "19.995", "not-an-ip-id"):
        assert private not in logged and private not in json.dumps(rows)


def test_a_page_of_only_unpublishable_observations_queues_nothing(discord_server):
    base, notifier, _ = discord_server
    summary = post(base, [product(1234568, url="https://evil.example/ip/1234568"),
                          product(1234569, title=" ")])
    assert summary["seen"] == 2 and summary["invalid"] == 2 and summary["queued"] == 0
    assert notifier.store.counts() == {}


def test_a_batch_the_queue_itself_refuses_is_still_rejected_whole(discord_server, monkeypatch):
    base, notifier, _ = discord_server

    def refuse(*_, **__):
        raise ValueError("expected 1 to 500 normalized items")

    monkeypatch.setattr(notifier.store, "record", refuse)
    with pytest.raises(urllib.error.HTTPError) as error:
        post(base, [product()])
    assert error.value.code == 400


def test_seed_mode_suppresses_future_jobs_after_restart(tmp_path):
    path = tmp_path / "queue.sqlite3"
    notifier = DiscordNotifier(JobStore(path))
    first = notifier.ingest([Item.from_dict(product())], seed_only=True)
    assert first.seeded == 1 and first.as_dict()["queued"] == 0
    restarted = DiscordNotifier(JobStore(path))
    second = restarted.ingest([Item.from_dict(product(category="electronics"))])
    assert second.as_dict()["queued"] == 0
    assert restarted.store.counts() == {}


def test_dry_mode_does_not_send_and_a_failed_privacy_audit_only_warns(tmp_path):
    client = FakeDiscord()
    notifier = DiscordNotifier(JobStore(tmp_path / "q"), client=client, state=mapping())
    notifier.ingest([Item.from_dict(product())])
    assert notifier.deliver_once()["sent"] == 0 and client.calls == []
    client = FakeDiscord()
    notifier = DiscordNotifier(
        JobStore(tmp_path / "q2"),
        client=client,
        state=mapping(),
        live=True,
        auditor=lambda *_: {"ok": False, "findings": ["Role 1700000000000000099: bad."]},
    )
    notifier.ingest([Item.from_dict(product())])
    result = notifier.deliver_once()
    # 2026-11-25: findings never block; the owner sees them in /health.
    assert result["blocked"] is False and result["sent"] and client.calls
    privacy = notifier.health()["privacy"]
    assert privacy["blocks_delivery"] is False and privacy["ok"] is False
    assert privacy["findings"] == ["Role 1700000000000000099: bad."]
    assert "privacy audit warning" in notifier.last_diagnostic


def test_diagnostics_only_use_private_operations_and_ignore_audit_findings(tmp_path):
    client = FakeDiscord()
    notifier = DiscordNotifier(
        JobStore(tmp_path / "q"),
        client=client,
        state=mapping(),
        live=True,
        auditor=lambda *_: {"ok": True},
    )
    assert notifier.send(
        Item(title="TEST: test notification", item_id="notifier-test", source="diagnostic")
    )
    assert (
        client.calls[0][1] == f"/channels/{notifier.state['channels']['collector-health']}/messages"
    )
    assert client.calls[0][2]["allowed_mentions"] == {"parse": []}
    assert notifier.store.counts() == {}
    assert not notifier.send(Item.from_dict(product()))
    failing = DiscordNotifier(
        JobStore(tmp_path / "q2"),
        client=client,
        state=mapping(),
        live=True,
        auditor=lambda *_: {"ok": False},
    )
    assert failing.send(Item(title="still sent", item_id="bot-check", source="diagnostic"))
    assert len(client.calls) == 2


def test_registry_selects_discord_without_telegram(monkeypatch, tmp_path):
    monkeypatch.setenv("NOTIFY_PROVIDER", "discord")
    monkeypatch.setenv("DISCORD_LIVE", "false")
    monkeypatch.setenv("DISCORD_QUEUE_PATH", str(tmp_path / "q"))
    assert isinstance(build_notifier(), DiscordNotifier)


def test_health_separates_queued_and_delivered(discord_server):
    base, _, _ = discord_server
    post(base, [product()])
    with urllib.request.urlopen(base + "/health") as response:
        health = json.load(response)
    assert health["notifier"] == "DiscordNotifier"
    assert health["discord"]["personal_filters_applied"] is False
    assert health["discord"]["jobs"] == {"pending": 4}
    assert health["runtime"]["last_notification"] is None
    assert health["runtime"]["last_successful_relay"]["summary"]["queued"] == 1


def test_rescan_cannot_replay_personal_history_into_discord(discord_server):
    base, notifier, client = discord_server
    request = urllib.request.Request(
        base + "/rescan",
        b'{"apply":true}',
        {"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 409 and client.calls == []
    assert notifier.store.counts() == {}


def test_seed_configuration_disables_background_sending(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_LIVE", "true")
    monkeypatch.setenv("SEED_MODE", "true")
    monkeypatch.setenv("DISCORD_QUEUE_PATH", str(tmp_path / "q"))
    notifier = DiscordNotifier.from_env()
    notifier.start()
    assert notifier.health()["live_sending"] is False and notifier._thread is None


def test_background_notification_timestamp_does_not_fake_relay(discord_server):
    _, _, _ = discord_server
    runtime = server.Handler.runtime_status
    runtime.record_notification()
    assert runtime.snapshot()["last_notification"] is not None
    assert runtime.snapshot()["last_successful_relay"] is None


def test_pro_and_free_clocks_survive_collector_restart(tmp_path):
    path = tmp_path / "q"
    notifier = DiscordNotifier(JobStore(path))
    notifier.ingest([Item.from_dict(product())])
    with notifier.store._connect() as db:
        first = db.execute("SELECT route,due FROM jobs ORDER BY route").fetchall()
        detected = db.execute("SELECT first_seen FROM items").fetchone()[0]
    notifier = DiscordNotifier(JobStore(path))
    notifier.ingest([Item.from_dict(product(value_usd=17.99))])
    with notifier.store._connect() as db:
        second = db.execute("SELECT route,due FROM jobs ORDER BY route").fetchall()
    assert "starter-items" not in dict(first)
    assert dict(first)["pro-all"] == detected
    assert dict(first)["yesterday-recap"] == detected + 86400
    assert dict(first) == dict(second)
