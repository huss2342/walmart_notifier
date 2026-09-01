"""End-to-end tests against a real local server on an ephemeral port.

The old Azure build had no test covering the HTTP layer, which is exactly where
both of its blocking bugs lived. These start the actual server and talk to it
over a socket.
"""

import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

import server
from state import SeenStore


class RecordingNotifier:
    instances = []

    def __init__(self):
        self.sent = []
        RecordingNotifier.instances.append(self)

    @classmethod
    def from_env(cls):
        return cls()

    def send(self, item, priority="normal"):
        self.sent.append((item.item_id, priority, item.value_usd))
        return True


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(monkeypatch, tmp_path):
    """A running server plus the notifier it will use."""
    RecordingNotifier.instances.clear()
    monkeypatch.setattr(server, "build_notifier", RecordingNotifier.from_env)
    monkeypatch.setenv("RULES_JSON", json.dumps(
        {"rules": [{"name": "test", "min_value_usd": 10.0, "priority": "high"}]}
    ))
    monkeypatch.delenv("SEED_MODE", raising=False)

    port = free_port()
    server.Handler.store = SeenStore(tmp_path / "seen.json")
    server.Handler.token = ""
    # serve() builds this once at startup; the tests stand the handler up
    # directly, so they have to supply it the same way.
    server.Handler.notifier = RecordingNotifier.from_env()

    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", port), server.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def post(base, payload, token=None, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    req = urllib.request.Request(f"{base}/ingest", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("X-Ingest-Token", token)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def get(base, path="/health"):
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as resp:
        return resp.status, json.loads(resp.read())


ITEM = {
    "item_id": "ip-20583371838",
    "title": "Kojic Acid Cleansing Soap Bar",
    "value_usd": 25.99,
    "url": "https://www.walmart.com/ip/Kojic-Acid-Soap/20583371838",
    "claims_remaining": 3,
}


def test_health_reports_configuration(live_server):
    status, body = get(live_server)
    assert status == 200
    assert body["status"] == "ok"
    assert body["rules"] == ["test"]
    assert body["notifier_configured"] is True


def test_ingest_notifies_once_then_dedupes(live_server):
    status, summary = post(live_server, {"items": [ITEM]})
    assert status == 200
    assert summary["notified"] == 1

    _, again = post(live_server, {"items": [ITEM]})
    assert again["new"] == 0 and again["notified"] == 0

    sent = [s for n in RecordingNotifier.instances for s in n.sent]
    assert sent == [("ip-20583371838", "high", 25.99)]


def test_dedupe_survives_a_restart(live_server, tmp_path):
    post(live_server, {"items": [ITEM]})
    # A fresh store reading the same file must already know the item.
    assert SeenStore(tmp_path / "seen.json").is_new("ip-20583371838") is False


def test_item_below_the_threshold_is_not_sent(live_server):
    cheap = {**ITEM, "item_id": "ip-111222333", "value_usd": 4.99}
    _, summary = post(live_server, {"items": [cheap]})
    assert summary["matched"] == 0 and summary["notified"] == 0


def test_token_is_enforced_when_set(live_server, monkeypatch):
    monkeypatch.setattr(server.Handler, "token", "sekrit")
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(live_server, {"items": [ITEM]}, token="wrong")
    assert exc.value.code == 403

    status, summary = post(live_server, {"items": [ITEM]}, token="sekrit")
    assert status == 200 and summary["notified"] == 1


def test_unknown_route_is_404(live_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(live_server, "/nope")
    assert exc.value.code == 404


def test_garbage_body_yields_nothing_and_does_not_crash(live_server):
    """Unparseable input degrades to zero items, not a dead server.

    The markup extractor is the fallback for anything that is not JSON, and it
    finds no item links in noise. A relay that occasionally posts something odd
    should never take the notifier down.
    """
    status, summary = post(live_server, None, raw=b"\x00\x01 not json {{{")
    assert status == 200
    assert summary["seen"] == 0 and summary["notified"] == 0
    assert get(live_server)[0] == 200


def test_preflight_allows_the_extension_origin(live_server):
    req = urllib.request.Request(f"{live_server}/ingest", method="OPTIONS")
    req.add_header("Origin", "chrome-extension://abcdefghijklmnop")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 204
        assert resp.headers["Access-Control-Allow-Origin"] == \
            "chrome-extension://abcdefghijklmnop"


def test_seed_mode_records_without_sending(live_server, monkeypatch):
    monkeypatch.setenv("SEED_MODE", "true")
    _, summary = post(live_server, {"items": [ITEM]})
    assert summary["seeded"] == 1 and summary["notified"] == 0
    assert not [s for n in RecordingNotifier.instances for s in n.sent]
