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
from sources.webhook_source import MAX_ITEMS
from state import SeenStore


class RecordingNotifier:
    instances = []

    def __init__(self):
        self.sent = []
        self.items = []
        RecordingNotifier.instances.append(self)

    @classmethod
    def from_env(cls):
        return cls()

    def send(self, item, priority="normal"):
        self.items.append(item)
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
    server.Handler.runtime_status = server.RuntimeStatus(server.Handler.store)
    server.Handler.telegram_commands = None

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
    assert body["notifier_diagnostic"] == ""
    assert "state_file" not in body
    assert body["observed_values"] == {
        "value_known": 0,
        "value_unknown": 0,
        "min_value_usd": None,
        "max_value_usd": None,
    }
    assert body["runtime"]["uptime_seconds"] >= 0
    assert body["runtime"]["last_successful_relay"] is None
    assert body["telegram_commands"]["enabled"] is False


def test_ingest_notifies_once_then_dedupes(live_server):
    status, summary = post(live_server, {"items": [ITEM]})
    assert status == 200
    assert summary == {
        "seen": 1,
        "new": 1,
        "duplicates": 0,
        "filtered": 0,
        "matched": 1,
        "notified": 1,
        "failed": 0,
        "pending": 0,
        "seeded": 0,
        "value_known": 1,
        "value_unknown": 0,
        "min_value_usd": 25.99,
        "max_value_usd": 25.99,
    }

    _, again = post(live_server, {"items": [ITEM]})
    assert again["new"] == 0 and again["duplicates"] == 1
    assert again["notified"] == 0

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


@pytest.mark.parametrize(
    "raw",
    [
        b"\x00\x01 not json {{{",
        b'{"items": [broken',
        b'{"items": [{"title": "TV", "value_usd": NaN}]}',
        b"\xff\xfe",
        b"[" * 2_000 + b"]" * 2_000,
    ],
)
def test_malformed_json_is_400_and_server_survives(live_server, raw):
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(live_server, None, raw=raw)

    assert exc.value.code == 400
    assert json.loads(exc.value.read())["error"]
    assert get(live_server)[0] == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"items": "not-an-array"},
        {"items": [42]},
        {"items": [{"item_id": "id-without-content"}]},
        {"items": [{"title": 42}]},
        {"items": [{"title": "TV", "item_id": []}]},
        {"items": [{"title": "TV", "url": 42}]},
        {"items": [{"title": "TV", "value_usd": True}]},
        {"items": [{"title": "TV", "value_usd": -1}]},
        {"items": [{"title": "TV", "raw": []}]},
        {"items": [{"title": "TV", "raw": None}]},
    ],
)
def test_invalid_item_schema_is_400(live_server, payload):
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(live_server, payload)

    assert exc.value.code == 400
    assert json.loads(exc.value.read())["error"]


def test_mixed_valid_and_invalid_items_are_rejected_atomically(live_server):
    valid = {**ITEM, "item_id": "ip-valid-before-invalid"}
    payload = {"items": [valid, {"title": "bad", "value_usd": False}]}

    with pytest.raises(urllib.error.HTTPError) as exc:
        post(live_server, payload)

    assert exc.value.code == 400
    assert server.Handler.store.is_new(valid["item_id"]) is True
    assert not [sent for notifier in RecordingNotifier.instances for sent in notifier.sent]

    runtime = get(live_server)[1]["runtime"]
    assert runtime["last_relay_attempt"]["error_type"] == "InvalidIngestPayload"


def test_oversized_item_array_is_rejected_instead_of_truncated(live_server):
    items = [
        {"item_id": f"item-{index}", "title": f"Item {index}", "value_usd": 25}
        for index in range(MAX_ITEMS + 1)
    ]

    with pytest.raises(urllib.error.HTTPError) as exc:
        post(live_server, {"items": items})

    assert exc.value.code == 400
    assert len(server.Handler.store) == 0


def test_preflight_allows_the_extension_origin(live_server):
    req = urllib.request.Request(f"{live_server}/ingest", method="OPTIONS")
    req.add_header("Origin", "chrome-extension://abcdefghijklmnop")
    req.add_header("Access-Control-Request-Private-Network", "true")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 204
        assert resp.headers["Access-Control-Allow-Origin"] == \
            "chrome-extension://abcdefghijklmnop"
        assert resp.headers["Access-Control-Allow-Private-Network"] == "true"


def test_ingest_rejects_a_web_origin_even_without_a_token(live_server):
    body = json.dumps({"items": [ITEM]}).encode()
    req = urllib.request.Request(f"{live_server}/ingest", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Origin", "https://example.com")

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 403


def test_ingest_requires_json_content_type(live_server):
    body = json.dumps({"items": [ITEM]}).encode()
    req = urllib.request.Request(f"{live_server}/ingest", data=body, method="POST")
    req.add_header("Content-Type", "text/plain")

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 415


@pytest.mark.parametrize("host", ["127.0.0.1", "127.1.2.3", "::1", "localhost"])
def test_loopback_bind_does_not_require_a_token(host):
    server._require_safe_bind(host, "")


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "relay.local"])
def test_non_loopback_bind_requires_a_token(host):
    with pytest.raises(ValueError, match="INGEST_TOKEN"):
        server._require_safe_bind(host, "")

    server._require_safe_bind(host, "sekrit")


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]"])
def test_compose_loopback_proxy_allows_only_a_wildcard_container_bind(host):
    server._require_safe_bind(host, "", container_loopback_only=True)


@pytest.mark.parametrize("host", ["192.168.1.20", "relay.local"])
def test_compose_loopback_proxy_does_not_exempt_lan_addresses(host):
    with pytest.raises(ValueError, match="INGEST_TOKEN"):
        server._require_safe_bind(host, "", container_loopback_only=True)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_container_loopback_flag_accepts_only_explicit_true_values(monkeypatch, value):
    monkeypatch.setenv("CONTAINER_LOOPBACK_ONLY", value)
    assert server._env_flag("CONTAINER_LOOPBACK_ONLY") is True


@pytest.mark.parametrize("value", ["", "0", "false", "anything-else"])
def test_container_loopback_flag_rejects_other_values(monkeypatch, value):
    monkeypatch.setenv("CONTAINER_LOOPBACK_ONLY", value)
    assert server._env_flag("CONTAINER_LOOPBACK_ONLY") is False


def test_serve_refuses_non_loopback_bind_with_blank_env_token(monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", "   ")
    monkeypatch.delenv("CONTAINER_LOOPBACK_ONLY", raising=False)

    with pytest.raises(ValueError, match="Refusing to bind to non-loopback"):
        server.serve("0.0.0.0", state_path="")


def test_seed_mode_records_without_sending(live_server, monkeypatch):
    monkeypatch.setenv("SEED_MODE", "true")
    _, summary = post(live_server, {"items": [ITEM]})
    assert summary["seeded"] == 1 and summary["notified"] == 0
    assert not [s for n in RecordingNotifier.instances for s in n.sent]


# --- rules API ---------------------------------------------------------------


def request(base, method, path="/rules", payload=None, token=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{base}{path}", data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("X-Ingest-Token", token)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


# --- delivery and failure diagnostics ---------------------------------------


def test_successful_relay_summary_survives_runtime_restart(live_server):
    post(live_server, {"items": [ITEM]})

    runtime = server.RuntimeStatus(SeenStore(server.Handler.store.path))
    last = runtime.snapshot()["last_successful_relay"]

    assert last["age_seconds"] >= 0
    assert last["summary"]["seen"] == 1
    assert last["summary"]["notified"] == 1


def test_failed_delivery_does_not_replace_last_successful_relay(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    runtime = server.RuntimeStatus(store)
    runtime.record_relay({"seen": 2, "notified": 1, "failed": 0, "pending": 0})
    runtime.record_relay({"seen": 2, "notified": 0, "failed": 1, "pending": 0})

    restarted = server.RuntimeStatus(SeenStore(store.path)).snapshot()
    assert restarted["last_successful_relay"]["summary"]["notified"] == 1
    assert runtime.snapshot()["last_relay_attempt"]["summary"]["failed"] == 1


def test_phone_status_text_reports_relay_filter_and_seen_count(monkeypatch, tmp_path):
    monkeypatch.setenv("RULES_JSON", json.dumps({"rules": [{
        "name": "phone-test",
        "min_value_usd": 49,
        "alert_on_unknown_value": False,
    }]}))
    store = SeenStore(tmp_path / "seen.json")
    store.mark_seen("ip-1", "TV", 99)
    runtime = server.RuntimeStatus(store)
    runtime.record_relay({
        "seen": 36,
        "new": 1,
        "matched": 1,
        "notified": 1,
        "failed": 0,
        "pending": 0,
    })

    text = server._telegram_status_text(runtime, store)

    assert "Notifier process + Telegram: online" in text
    assert "Last successful reviewer relay:" in text
    assert "36 seen, 1 new, 1 matched, 1 alerted" in text
    assert "Filter: $49.00+; unknown-value alerts off" in text
    assert "Recorded items: 1" in text


def test_health_exposes_command_worker_diagnostics(live_server, monkeypatch):
    class CommandHealth:
        @staticmethod
        def health():
            return {
                "enabled": True,
                "running": True,
                "diagnostic": "polling okay",
            }

    monkeypatch.setattr(server.Handler, "telegram_commands", CommandHealth())

    commands = get(live_server)[1]["telegram_commands"]
    assert commands == {
        "enabled": True,
        "running": True,
        "diagnostic": "polling okay",
    }


def test_health_reports_safe_observed_value_stats(live_server):
    post(live_server, {"items": [ITEM]})
    mystery = {**ITEM, "item_id": "ip-mystery", "value_usd": None}
    post(live_server, {"items": [mystery]})

    body = get(live_server)[1]
    assert body["seen_items"] == 2
    assert body["observed_values"] == {
        "value_known": 1,
        "value_unknown": 1,
        "min_value_usd": 25.99,
        "max_value_usd": 25.99,
    }
    assert "title" not in body["observed_values"]
    assert "item_id" not in body["observed_values"]


def test_test_notification_bypasses_rules_and_dedupe(live_server):
    before = len(server.Handler.store)

    first = request(live_server, "POST", path="/test-notification")
    second = request(live_server, "POST", path="/test-notification")

    assert first[0] == second[0] == 200
    assert first[1] == {
        "ok": True,
        "message": "test notification sent",
        "notifier": "RecordingNotifier",
    }
    assert len(server.Handler.store) == before
    sent_items = [item for notifier in RecordingNotifier.instances for item in notifier.items]
    assert [item.title for item in sent_items] == [
        "TEST: Reviewer notifier is working",
        "TEST: Reviewer notifier is working",
    ]


def test_test_notification_honours_ingest_token(live_server, monkeypatch):
    monkeypatch.setattr(server.Handler, "token", "sekrit")
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(live_server, "POST", path="/test-notification", token="wrong")
    assert exc.value.code == 403

    status, body = request(
        live_server, "POST", path="/test-notification", token="sekrit"
    )
    assert status == 200 and body["ok"] is True


def test_test_notification_false_result_is_503(live_server, monkeypatch):
    class FailingNotifier:
        last_diagnostic = "provider returned HTTP 429"

        def send(self, item, priority="normal"):
            return False

    monkeypatch.setattr(server.Handler, "notifier", FailingNotifier())
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(live_server, "POST", path="/test-notification")

    assert exc.value.code == 503
    body = json.loads(exc.value.read())
    assert body["ok"] is False
    assert body["error"] == "test notification delivery failed"
    assert body["detail"] == "provider returned HTTP 429"
    health_status, health = get(live_server)
    assert health_status == 200
    assert health["notifier_diagnostic"] == "provider returned HTTP 429"


def test_test_notification_exception_is_503_and_server_survives(live_server, monkeypatch):
    class RaisingNotifier:
        def send(self, item, priority="normal"):
            raise RuntimeError("boom")

    monkeypatch.setattr(server.Handler, "notifier", RaisingNotifier())
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(live_server, "POST", path="/test-notification")

    assert exc.value.code == 503
    body = json.loads(exc.value.read())
    assert body["error_type"] == "RuntimeError"
    assert get(live_server)[0] == 200


def test_ingest_notifier_exception_is_500_retriable_and_server_survives(
    live_server, monkeypatch
):
    class RaisingNotifier:
        def send(self, item, priority="normal"):
            raise RuntimeError("boom")

    monkeypatch.setattr(server.Handler, "notifier", RaisingNotifier())
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(live_server, {"items": [ITEM]})

    assert exc.value.code == 500
    body = json.loads(exc.value.read())
    assert body == {"error": "ingest processing failed", "error_type": "RuntimeError"}
    assert server.Handler.store.is_new(ITEM["item_id"]) is True
    assert get(live_server)[0] == 200


def test_unexpected_processing_exception_is_500_and_server_survives(
    live_server, monkeypatch
):
    def explode(*args, **kwargs):
        raise ValueError("bad runtime configuration")

    monkeypatch.setattr(server, "process", explode)
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(live_server, {"items": [ITEM]})

    assert exc.value.code == 500
    assert json.loads(exc.value.read()) == {
        "error": "ingest processing failed",
        "error_type": "ValueError",
    }
    assert get(live_server)[0] == 200


@pytest.fixture
def isolated_rules(monkeypatch, tmp_path):
    """Point the user-rules file somewhere disposable."""
    monkeypatch.delenv("RULES_JSON", raising=False)
    monkeypatch.setenv("USER_RULES_PATH", str(tmp_path / "rules.json"))
    return tmp_path / "rules.json"


def test_get_rules_reports_the_active_set(live_server, isolated_rules):
    status, body = request(live_server, "GET")
    assert status == 200
    assert body["source"] == "bundled"
    assert [r["name"] for r in body["rules"]] == ["expensive", "watched-keywords"]


def test_saved_rules_take_effect_on_the_next_relay(live_server, isolated_rules):
    """The whole point of server-side rules: no restart between save and use."""
    cheap = {**ITEM, "item_id": "ip-999", "value_usd": 6.0}
    _, before = post(live_server, {"items": [cheap]})
    assert before["notified"] == 0          # $6 is under the bundled $25 floor

    request(live_server, "PUT", payload={"rules": [
        {"name": "my-filters", "min_value_usd": 5.0, "priority": "high"}
    ]})

    cheap2 = {**cheap, "item_id": "ip-998"}
    _, after = post(live_server, {"items": [cheap2]})
    assert after["notified"] == 1


def test_delete_reverts_to_bundled_rules(live_server, isolated_rules):
    request(live_server, "PUT", payload={"rules": [{"name": "mine"}]})
    assert request(live_server, "GET")[1]["source"] == "user"

    status, body = request(live_server, "DELETE")
    assert status == 200 and body["removed"] is True
    assert body["source"] == "bundled"


def test_delete_rules_rejects_a_web_origin(live_server, isolated_rules):
    req = urllib.request.Request(f"{live_server}/rules", method="DELETE")
    req.add_header("Origin", "https://example.com")

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)

    assert exc.value.code == 403


def test_invalid_rules_are_rejected_with_400(live_server, isolated_rules):
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(live_server, "PUT", payload={"rules": []})
    assert exc.value.code == 400
    # The previous configuration is untouched.
    assert request(live_server, "GET")[1]["source"] == "bundled"


def test_rules_endpoint_honours_the_token(live_server, isolated_rules, monkeypatch):
    monkeypatch.setattr(server.Handler, "token", "sekrit")
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(live_server, "PUT", payload={"rules": [{"name": "x"}]}, token="wrong")
    assert exc.value.code == 403

    with pytest.raises(urllib.error.HTTPError) as exc:
        request(live_server, "GET", token="wrong")
    assert exc.value.code == 403

    assert request(live_server, "GET", token="sekrit")[0] == 200


def test_health_endpoint_honours_the_token(live_server, monkeypatch):
    monkeypatch.setattr(server.Handler, "token", "sekrit")
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(live_server)
    assert exc.value.code == 403

    assert request(
        live_server, "GET", path="/health", token="sekrit"
    )[0] == 200


def test_put_to_an_unknown_path_is_404(live_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(live_server, "PUT", path="/nope", payload={})
    assert exc.value.code == 404
