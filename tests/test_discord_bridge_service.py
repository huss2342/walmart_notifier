"""Actual loopback HTTP and mocked remote delivery boundaries."""

import http.client
import json
import threading

import pytest
from discord_bridge.api import DiscordClient, DiscordError
from discord_bridge.core import JobStore, normalize_item
from discord_bridge.service import deliver_due, make_server


@pytest.fixture
def endpoint(tmp_path):
    store = JobStore(tmp_path / "http.sqlite3")
    server = make_server(store, key="k" * 40, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, store
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def request(server, method, path, data=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
    body = json.dumps(data).encode() if data is not None else None
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    result = response.status, dict(response.headers), json.loads(response.read())
    connection.close()
    return result


def test_http_is_private_authenticated_and_rejects_browser_origins(endpoint):
    server, store = endpoint
    assert server.server_address[0] == "127.0.0.1"
    assert request(server, "GET", "/health")[0] == 401
    headers = {"Authorization": "Bearer " + "k" * 40}
    status, response_headers, data = request(server, "GET", "/health", headers=headers)
    assert status == 200 and data["live_sending"] is False
    assert response_headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in response_headers
    headers["Origin"] = "https://evil.test"
    assert request(server, "GET", "/health", headers=headers)[0] == 403
    assert store.counts() == {}


def test_http_rejects_mixed_batch_and_returns_durable_acceptance(endpoint):
    server, store = endpoint
    headers = {"Authorization": "Bearer " + "k" * 40, "Content-Type": "application/json"}
    good = {"item_id": "ip-20449164214", "title": "Audio card", "value_usd": 29.99}
    assert (
        request(server, "POST", "/v1/observations", {"items": [good, {"oops": 1}]}, headers)[0]
        == 400
    )
    assert store.counts() == {}
    first = request(server, "POST", "/v1/observations", {"items": [good]}, headers)
    assert first[0] == 202 and first[2]["new"] == 1
    assert (
        request(server, "POST", "/v1/observations", {"items": [good]}, headers)[2]["duplicates"]
        == 1
    )
    assert sum(store.counts().values()) == 5


def test_http_enforces_size_content_type_and_does_not_send(endpoint):
    server, _store = endpoint
    headers = {"Authorization": "Bearer " + "k" * 40}
    assert request(server, "POST", "/v1/observations", {"items": []}, headers)[0] == 415
    headers["Content-Type"] = "application/json"
    assert request(server, "POST", "/v1/observations", {"blob": "x" * 530000}, headers)[0] == 413


def test_short_secret_is_refused(tmp_path):
    with pytest.raises(ValueError):
        make_server(JobStore(tmp_path / "short.sqlite3"), key="password", port=0)


def test_postcommit_monitor_hook_cannot_change_durable_acceptance(tmp_path):
    store = JobStore(tmp_path / "hook.sqlite3")
    observed = []

    def accepted(result):
        observed.append(result)
        assert store.counts()["pending"] == 5
        raise OSError("monitor unavailable")

    server = make_server(store, key="k" * 40, port=0, on_accepted=accepted)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        headers = {"Authorization": "Bearer " + "k" * 40, "Content-Type": "application/json"}
        good = {"item_id": "ip-20449164214", "title": "Audio card", "value_usd": 29.99}
        response = request(server, "POST", "/v1/observations", {"items": [good]}, headers)
        assert response[0] == 202 and response[2]["new"] == 1
        assert observed == [{"accepted": 1, "new": 1, "duplicates": 0, "queued": 1}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class FakeClient:
    def __init__(self, error=None):
        self.sent = []
        self.error = error

    def request(self, method, path, payload=None):
        if self.error:
            raise self.error
        self.sent.append((method, path, payload))
        return {"id": "123456789012345678"}


def test_worker_defaults_to_preview_and_fails_closed_on_audit(tmp_path):
    store = JobStore(tmp_path / "worker.sqlite3")
    store.record(
        [normalize_item({"item_id": "ip-20449164214", "title": "Audio", "value_usd": 29.99})],
        now=1000,
    )
    client = FakeClient()
    state = {"guild_id": "123456789012345678", "channels": {}}
    assert deliver_due(store, client, state, now=1000)["sent"] == 0
    assert not client.sent
    result = deliver_due(
        store,
        client,
        state,
        live=True,
        now=1000,
        auditor=lambda *_args: {"ok": False, "findings": ["leak"]},
    )
    assert result["blocked"] and not client.sent
    assert store.counts() == {"pending": 5}


def test_worker_rate_limit_retry_and_price_update_cannot_leak_starter(tmp_path):
    store = JobStore(tmp_path / "worker.sqlite3")
    raw = {"item_id": "ip-20449164214", "title": "Audio", "value_usd": 29.99}
    store.record([normalize_item(raw)], now=1000)
    routes = ["pro-all", "type-other", "price-20-40", "starter-items"]
    state = {
        "channels": {route: str(100000000000000000 + index) for index, route in enumerate(routes)}
    }

    def okay(*_args):
        return {"ok": True, "findings": []}

    client = FakeClient(DiscordError("rate limited", status=429, retry_after=30))
    result = deliver_due(store, client, state, live=True, now=1000, auditor=okay)
    assert result["retried"] == 3  # Independent channel throttles no longer stop the cycle.
    client.error = None
    deliver_due(store, client, state, live=True, now=1031, auditor=okay)
    assert len(client.sent) == 3
    store.record([normalize_item({**raw, "value_usd": 99.99})], now=1200)
    deliver_due(store, client, state, live=True, now=1300, auditor=okay)
    assert len(client.sent) == 3
    assert store.counts()["skipped"] == 1
    assert all(call[2]["enforce_nonce"] is True for call in client.sent)


def test_rate_limit_waits_do_not_exhaust_delivery_attempts_across_restart(tmp_path):
    path = tmp_path / "limited.sqlite3"
    store = JobStore(path)
    # Unknown value creates all-items/category jobs plus tomorrow's free history.
    store.record([normalize_item({"item_id": "ip-20449164214", "title": "Audio"})], now=1000)
    state = {
        "channels": {
            "pro-all": "123456789012345678",
            "type-other": "123456789012345679",
        }
    }
    client = FakeClient(DiscordError("rate limited", status=429, retry_after=30))

    def okay(*_args):
        return {"ok": True}

    for index in range(24):
        now = 1000 + index * 30
        # A process restart must preserve the wait as well as the unsent jobs.
        store = JobStore(path)
        result = deliver_due(store, client, state, live=True, now=now, auditor=okay)
        assert result["retried"] == 2 and result["failed"] == 0
        assert store.counts() == {"pending": 3}
        assert not client.sent
    client.error = None
    result = deliver_due(JobStore(path), client, state, live=True, now=1720, auditor=okay)
    assert result["sent"] == 2
    assert store.counts() == {"sent": 2, "pending": 1}


def test_connection_failures_remain_bounded(tmp_path):
    store = JobStore(tmp_path / "bounded.sqlite3")
    store.record([normalize_item({"item_id": "ip-20449164214", "title": "Audio"})], now=1000)
    state = {
        "channels": {
            "pro-all": "123456789012345678",
            "type-other": "123456789012345679",
        }
    }

    def okay(*_args):
        return {"ok": True}

    client = FakeClient(DiscordError("connection failed"))
    for now in range(1000, 2800, 10):
        deliver_due(store, client, state, live=True, now=now, auditor=okay)
    assert store.counts() == {"failed": 2, "pending": 1}
    assert not client.sent


def test_rate_limited_jobs_still_expire_instead_of_replaying_stale_alerts(tmp_path):
    store = JobStore(tmp_path / "expired.sqlite3")
    store.record([normalize_item({"item_id": "ip-20449164214", "title": "Audio"})], now=1000)
    state = {
        "channels": {
            "pro-all": "123456789012345678",
            "type-other": "123456789012345679",
        }
    }

    def okay(*_args):
        return {"ok": True}

    client = FakeClient(DiscordError("rate limited", status=429, retry_after=3600))
    result = deliver_due(store, client, state, live=True, now=1000, auditor=okay)
    assert result["retried"] == 2
    deliver_due(store, client, state, live=True, now=2800, auditor=okay)
    assert store.counts() == {"expired": 2, "pending": 1}
    assert not client.sent


def test_api_client_never_redirects_credentials_or_accepts_arbitrary_hosts():
    client = DiscordClient("private-bot-token")
    for path in ("https://evil.test", "//evil.test", "/../secret", "/channels/1?token=x"):
        with pytest.raises(ValueError):
            client.request("GET", path)
    assert "private-bot-token" not in repr(client)
