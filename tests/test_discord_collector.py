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
    assert delivered["sent"] == 3  # Pro all + furniture + $10-20. Starter/free wait.
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
        assert [button["label"] for button in payload["components"][0]["components"]] == [
            "Open review item",
            "View product",
        ]
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


def test_invalid_batch_does_not_accept_valid_prefix(discord_server):
    base, notifier, _ = discord_server
    with pytest.raises(urllib.error.HTTPError) as error:
        post(base, [product(), product(1234568, url="https://evil.example/ip/1234568")])
    assert error.value.code == 400
    assert notifier.store.counts() == {}


def test_seed_mode_suppresses_future_jobs_after_restart(tmp_path):
    path = tmp_path / "queue.sqlite3"
    notifier = DiscordNotifier(JobStore(path))
    first = notifier.ingest([Item.from_dict(product())], seed_only=True)
    assert first.seeded == 1 and first.as_dict()["queued"] == 0
    restarted = DiscordNotifier(JobStore(path))
    second = restarted.ingest([Item.from_dict(product(category="electronics"))])
    assert second.as_dict()["queued"] == 0
    assert restarted.store.counts() == {}


def test_dry_mode_and_failed_privacy_audit_do_not_send(tmp_path):
    client = FakeDiscord()
    notifier = DiscordNotifier(JobStore(tmp_path / "q"), client=client, state=mapping())
    notifier.ingest([Item.from_dict(product())])
    assert notifier.deliver_once()["sent"] == 0 and client.calls == []
    notifier.live = True
    notifier.auditor = lambda *_: {"ok": False, "findings": ["bad permissions"]}
    assert notifier.deliver_once()["blocked"] is True and client.calls == []


def test_diagnostics_only_use_private_operations_after_audit(tmp_path):
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
    notifier.auditor = lambda *_: {"ok": False}
    assert not notifier.send(Item(title="blocked", item_id="bot-check", source="diagnostic"))
    assert len(client.calls) == 1


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
    assert health["discord"]["jobs"] == {"pending": 5}
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


def test_starter_and_free_clocks_survive_collector_restart(tmp_path):
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
    assert dict(first)["starter-items"] == detected + 300
    assert dict(first)["yesterday-recap"] == detected + 86400
    assert dict(first) == dict(second)
