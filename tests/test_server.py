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
from datetime import UTC, datetime, timedelta

import pytest

import server
from scanner_control import ScannerCommandsStore, ScannerSettingsStore, ScannerStatusStore
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
    server.Handler.browser_diagnostics = server.BrowserDiagnosticsStore(
        tmp_path / "browser-diagnostics.json"
    )
    server.Handler.relay_settings = server.RelaySettingsStore(tmp_path / "relay-settings.json")
    server.Handler.scanner_settings = ScannerSettingsStore(tmp_path / "scanner-settings.json")
    server.Handler.scanner_commands = ScannerCommandsStore(tmp_path / "scanner-commands.json")
    server.Handler.scanner_status = ScannerStatusStore(tmp_path / "scanner-status.json")
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


def test_scan_settings_json_roundtrip_and_manual_file_edit(live_server):
    assert get(live_server, "/relay-settings") == (200, {"settings": None})
    scan = {"schemaVersion": 1, "refreshMinutes": 10,
            "pageDelaySeconds": 15, "sweepMode": "none"}
    request = urllib.request.Request(
        f"{live_server}/relay-settings", json.dumps(scan).encode(),
        {"Content-Type": "application/json"}, method="PUT",
    )
    with urllib.request.urlopen(request) as response:
        assert response.status == 200
        assert json.load(response) == {"settings": scan}
    assert get(live_server, "/relay-settings") == (200, {"settings": scan})
    changed = {**scan, "refreshMinutes": 0}
    server.Handler.relay_settings.path.write_text(json.dumps(changed), encoding="utf-8")
    assert get(live_server, "/relay-settings") == (200, {"settings": changed})
    assert len(server.Handler.store) == 0
    assert server.Handler.notifier.sent == []
    assert server.Handler.runtime_status.snapshot()["last_successful_relay"] is None


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_scan_settings_require_existing_api_token(live_server, method):
    server.Handler.token = "correct-token"
    request = urllib.request.Request(
        f"{live_server}/relay-settings", b"{}" if method == "PUT" else None,
        {"Content-Type": "application/json"}, method=method,
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 403


@pytest.mark.parametrize("headers,status", [
    ({"Origin": "https://evil.example", "Content-Type": "application/json"}, 403),
    ({"Content-Type": "text/plain"}, 415),
])
def test_scan_settings_reject_web_origins_and_non_json(live_server, headers, status):
    request = urllib.request.Request(
        f"{live_server}/relay-settings", b"{}", headers, method="PUT",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == status


def test_scan_settings_invalid_input_and_bad_file_are_generic(live_server):
    request = urllib.request.Request(
        f"{live_server}/relay-settings", b'{"token":"private-data"}',
        {"Content-Type": "application/json"}, method="PUT",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 400
    assert b"private-data" not in error.value.read()
    server.Handler.relay_settings.path.write_text('{"token":"private-data"}', encoding="utf-8")
    with pytest.raises(urllib.error.HTTPError) as error:
        get(live_server, "/relay-settings")
    assert error.value.code == 400
    assert b"private-data" not in error.value.read()


def test_scan_settings_write_error_is_generic_and_size_limit_precedes_body_read(
    live_server, monkeypatch,
):
    def fail_save(value):
        raise OSError("private path and error details")

    monkeypatch.setattr(server.Handler.relay_settings, "save", fail_save)
    request = urllib.request.Request(
        f"{live_server}/relay-settings", b"{}",
        {"Content-Type": "application/json"}, method="PUT",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 500
    assert b"private" not in error.value.read()
    request = urllib.request.Request(
        f"{live_server}/relay-settings", b"{}",
        {"Content-Type": "application/json", "Content-Length": str(16 * 1024 + 1)},
        method="PUT",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 413


ITEM = {
    "item_id": "ip-20583371838",
    "title": "Kojic Acid Cleansing Soap Bar",
    "value_usd": 25.99,
    "url": "https://www.walmart.com/ip/Kojic-Acid-Soap/20583371838",
    "claims_remaining": 3,
}


def diagnostic_report():
    now = int(datetime.now(UTC).timestamp() * 1000)
    return {
        "schemaVersion": 1, "extensionVersion": "2.7.0", "exportedAt": now,
        "settings": {"refreshMinutes": 10, "pageDelaySeconds": 15, "sweepMode": "none"},
        "verification": {"count": 1, "lastAt": now, "pausedUntil": now + 3600_000},
        "events": [{"at": now, "event": "page-delivered", "page": 2, "new": 1,
                    "items": 24, "durationMs": 120}],
    }


@pytest.mark.parametrize("page_delay", [1, 15])
def test_browser_diagnostics_mirror_is_inspectable_and_does_not_count_as_item_relay(
    live_server, page_delay,
):
    assert get(live_server, "/browser-diagnostics")[1]["report"] is None
    before = get(live_server)[1]["runtime"]["last_successful_relay"]
    report = diagnostic_report()
    report["settings"]["pageDelaySeconds"] = page_delay
    status, body = request(live_server, "POST", "/browser-diagnostics", report)
    assert status == 200 and body["ok"] is True
    mirrored = get(live_server, "/browser-diagnostics")[1]
    assert mirrored["report"] == report
    assert mirrored["receivedAt"] > 0
    assert get(live_server)[1]["runtime"]["last_successful_relay"] == before


def test_browser_diagnostics_require_auth_and_reject_web_origins(live_server, monkeypatch):
    monkeypatch.setattr(server.Handler, "token", "sekrit")
    for method in ("GET", "POST"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            request(live_server, method, "/browser-diagnostics", diagnostic_report())
        assert exc.value.code == 403
    req = urllib.request.Request(f"{live_server}/browser-diagnostics",
                                 data=json.dumps(diagnostic_report()).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Ingest-Token", "sekrit")
    req.add_header("Origin", "https://example.org")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 403


def test_browser_diagnostics_reject_secret_fields_and_large_bodies(live_server):
    report = diagnostic_report()
    report["token"] = "must-not-be-echoed"
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(live_server, "POST", "/browser-diagnostics", report)
    assert exc.value.code == 400
    assert b"must-not-be-echoed" not in exc.value.read()
    # The route rejects the declared size before reading. A small body avoids
    # Windows aborting a simultaneous large send when the server closes early.
    req = urllib.request.Request(f"{live_server}/browser-diagnostics",
                                 data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Content-Length", str(128 * 1024 + 1))
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 413


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


def browser_report(**changes):
    return {
        "phase": "ready",
        "tab_count": 1,
        "reachable": True,
        "refresh_minutes": 10,
        "paused_until": 0,
        **changes,
    }


def test_browser_heartbeat_is_visible_but_does_not_count_as_an_item_relay(live_server):
    runtime = server.Handler.runtime_status
    runtime.started_monotonic -= 7200
    status, body = request(
        live_server, "POST", path="/relay-status", payload=browser_report()
    )

    assert status == 200 and body == {"ok": True}
    snapshot = get(live_server)[1]["runtime"]
    assert snapshot["browser"]["phase"] == "ready"
    assert snapshot["browser"]["last_sweep_pages"] == 0
    assert snapshot["browser"]["last_sweep_at"] == 0
    assert snapshot["browser"]["age_seconds"] >= 0
    assert datetime.fromisoformat(snapshot["browser"]["at"]).tzinfo is not None
    assert snapshot["last_successful_relay"] is None
    assert snapshot["last_relay_attempt"] is None
    assert snapshot["last_notification"] is None
    watchdog = server.RelayWatchdog(runtime, server.Handler.notifier)
    assert watchdog.relay_age_seconds() >= 7200
    assert server.RuntimeStatus(server.Handler.store).snapshot()["browser"] is None


def test_browser_heartbeat_accepts_alphabet_sweep_page_counts(live_server):
    status, _ = request(live_server, "POST", path="/relay-status", payload=browser_report(
        phase="complete", last_sweep_pages=2600,
    ))
    assert status == 200
    assert get(live_server)[1]["runtime"]["browser"]["last_sweep_pages"] == 2600


def test_browser_heartbeat_accepts_high_value_sweep_page_counts(live_server):
    status, _ = request(live_server, "POST", path="/relay-status", payload=browser_report(
        phase="complete", last_sweep_pages=6200,
    ))
    assert status == 200
    assert get(live_server)[1]["runtime"]["browser"]["last_sweep_pages"] == 6200


@pytest.mark.parametrize("phase", [
    "passive", "waiting-verification", "ready", "sweeping", "waiting-user",
    "ingest-retry", "complete", "unreachable",
])
def test_browser_heartbeat_accepts_the_shared_phase_contract(live_server, phase):
    status, _ = request(live_server, "POST", path="/relay-status", payload=browser_report(
        phase=phase, last_sweep_pages=7, last_sweep_at=1000.5,
    ))
    assert status == 200
    snapshot = get(live_server)[1]["runtime"]["browser"]
    assert snapshot["phase"] == phase
    assert snapshot["last_sweep_pages"] == 7
    assert snapshot["last_sweep_at"] == 1000.5


@pytest.mark.parametrize("field,value", [
    ("phase", "invented"), ("phase", None), ("phase", []),
    ("tab_count", True), ("tab_count", 1.5), ("tab_count", -1), ("tab_count", 101),
    ("reachable", 1), ("reachable", "true"),
    ("refresh_minutes", True), ("refresh_minutes", "10"),
    ("refresh_minutes", -1), ("refresh_minutes", 121),
    ("refresh_minutes", float("nan")), ("refresh_minutes", float("inf")),
    ("paused_until", True), ("paused_until", -1),
    ("paused_until", float("inf")),
    ("last_sweep_pages", True), ("last_sweep_pages", 1.5),
    ("last_sweep_pages", 6201),
    ("last_sweep_at", True), ("last_sweep_at", -1),
    ("last_sweep_at", float("inf")), ("last_sweep_at", 10**400),
    ("extra", "injected"), ("at", "client-controlled"),
])
def test_browser_heartbeat_rejects_invalid_fields(live_server, field, value):
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(live_server, "POST", path="/relay-status", payload=browser_report(
            **{field: value}
        ))
    assert exc.value.code == 400
    assert server.Handler.runtime_status.snapshot()["browser"] is None


@pytest.mark.parametrize("field", [
    "phase", "tab_count", "reachable", "refresh_minutes", "paused_until",
])
def test_browser_heartbeat_requires_core_fields(live_server, field):
    payload = browser_report()
    del payload[field]
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(live_server, "POST", path="/relay-status", payload=payload)
    assert exc.value.code == 400


def test_browser_heartbeat_rejects_a_pause_beyond_one_week(live_server):
    far_future = (datetime.now(UTC) + timedelta(days=8)).timestamp() * 1000
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(live_server, "POST", path="/relay-status", payload=browser_report(
            paused_until=far_future,
        ))
    assert exc.value.code == 400


def test_browser_heartbeat_requires_token_extension_origin_and_json(live_server, monkeypatch):
    monkeypatch.setattr(server.Handler, "token", "sekrit")
    payload = json.dumps(browser_report()).encode()
    headers = {"Content-Type": "application/json", "X-Ingest-Token": "sekrit"}
    for changed, expected in [
        ({"X-Ingest-Token": "wrong"}, 403),
        ({"Origin": "https://example.com"}, 403),
        ({"Content-Type": "text/plain"}, 415),
    ]:
        req = urllib.request.Request(f"{live_server}/relay-status", data=payload,
                                     headers={**headers, **changed}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == expected
    headers["Origin"] = "chrome-extension://extension"
    req = urllib.request.Request(f"{live_server}/relay-status", data=payload,
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200


def test_browser_heartbeat_bounds_body_and_rejects_malformed_json(live_server, monkeypatch):
    for payload in (b"not-json", b"[]", b"{}", b"\xff"):
        req = urllib.request.Request(f"{live_server}/relay-status", data=payload,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 400
    monkeypatch.setattr(server, "MAX_INGEST_BYTES", 20)
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(live_server, "POST", path="/relay-status", payload=browser_report())
    assert exc.value.code == 413


def test_last_item_notification_survives_restart_and_silent_relays(tmp_path, monkeypatch):
    store = SeenStore(tmp_path / "seen.json")
    runtime = server.RuntimeStatus(store)
    runtime.record_relay({"seen": 2, "notified": 1, "failed": 1, "pending": 0})
    delivered_at = runtime.snapshot()["last_notification"]["at"]
    future = datetime.now(UTC) + timedelta(hours=1)

    class LaterDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return future

    monkeypatch.setattr(server, "datetime", LaterDatetime)
    runtime.record_relay({"seen": 2, "notified": 0, "duplicates": 2})
    store.flush()
    restarted = server.RuntimeStatus(SeenStore(store.path)).snapshot()
    assert restarted["last_notification"]["at"] == delivered_at
    assert restarted["last_notification"]["age_seconds"] >= 3599
    assert restarted["last_successful_relay"]["summary"]["duplicates"] == 2


def test_test_notification_does_not_record_an_item_notification(live_server):
    request(live_server, "POST", path="/test-notification")
    assert server.Handler.runtime_status.snapshot()["last_notification"] is None


@pytest.mark.parametrize("destination", ["group", "private"])
def test_status_reports_verification_disabled_refresh_and_alert_destination(
    tmp_path, monkeypatch, destination
):
    store = SeenStore(tmp_path / "seen.json")
    runtime = server.RuntimeStatus(store)
    runtime.record_browser(browser_report(phase="waiting-verification", refresh_minutes=0))

    class CommandHealth:
        @staticmethod
        def health():
            return {"delivery_chat": destination}

    monkeypatch.setattr(server.Handler, "telegram_commands", CommandHealth())
    text = server._telegram_status_text(runtime, store)
    assert "waiting-verification" in text
    assert "Auto-refresh is disabled" in text
    assert "resumes automatically" in text
    assert f"Alerts destination: {destination}" in text
    assert "Last item notification: unknown" in text


def test_status_reports_disconnected_heartbeat(tmp_path, monkeypatch):
    store = SeenStore(tmp_path / "seen.json")
    runtime = server.RuntimeStatus(store)
    runtime.record_browser(browser_report())
    future = datetime.now(UTC) + timedelta(seconds=181)

    class LaterDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return future

    monkeypatch.setattr(server, "datetime", LaterDatetime)
    assert "Browser heartbeat disconnected" in server._telegram_status_text(runtime, store)


def test_browser_status_describes_user_activity_and_missing_tabs_accurately():
    waiting = {**browser_report(phase="waiting-user"), "age_seconds": 0}
    text = "\n".join(server._browser_status_lines(waiting))
    assert "stop clicking or scrolling" in text
    assert "sign-in" not in text
    missing = {**browser_report(phase="unreachable", reachable=False), "age_seconds": 0}
    text = "\n".join(server._browser_status_lines(missing))
    assert "No reviewer tab is responding" in text
    assert "connection problem with the local notifier" not in text


@pytest.mark.parametrize("summary,reason", [
    ({"seen": 3, "new": 0, "duplicates": 3, "notified": 0}, "only already-seen items"),
    ({"seen": 3, "new": 2, "filtered": 2, "matched": 0, "notified": 0},
     "all new items were filtered out"),
])
def test_status_explains_silent_batches_and_all_rules(tmp_path, monkeypatch, summary, reason):
    monkeypatch.setenv("RULES_JSON", json.dumps({"rules": [
        {"name": "expensive", "min_value_usd": 49}, {"name": "other", "keywords": ["tv"]},
    ]}))
    store = SeenStore(tmp_path / "seen.json")
    runtime = server.RuntimeStatus(store)
    runtime.record_relay(summary)
    text = server._telegram_status_text(runtime, store)
    assert reason in text
    assert "/filters" in text
    assert "2 active rules" in text
    assert "first of" not in text
    assert "Last item notification: unknown" in text


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
    # Saving is debounced now, so the marker reaches disk on an explicit flush
    # -- which the ingest route performs once per request.
    store.save()

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
    assert "Last successful item notification:" in text
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


# --- Walmart bot-check alert -------------------------------------------------


def _sent_items():
    return [item for notifier in RecordingNotifier.instances for item in notifier.items]


def test_bot_check_alert_uses_fixed_text_and_urgent_priority(live_server):
    before = len(server.Handler.store)
    status, body = request(
        live_server, "POST", path="/bot-check", payload={"paused_minutes": 60}
    )
    assert status == 200 and body == {"ok": True}

    item = _sent_items()[-1]
    assert item.title.startswith("Walmart bot check: solve it by hand")
    assert "paused for 60 min" in item.title
    sent = [s for n in RecordingNotifier.instances for s in n.sent]
    assert sent[-1][1] == "urgent"
    # A system alert is not a catalogue item and must not touch dedupe state.
    assert len(server.Handler.store) == before


def test_bot_check_ignores_anything_but_a_clamped_pause_length(live_server):
    request(
        live_server, "POST", path="/bot-check",
        payload={"paused_minutes": 10**9, "title": "injected text"},
    )
    title = _sent_items()[-1].title
    assert "injected" not in title
    assert "paused for 10080 min" in title   # one week cap

    request(live_server, "POST", path="/bot-check", payload={"paused_minutes": "junk"})
    assert _sent_items()[-1].title.endswith("Automation paused.")


def test_bot_check_honours_ingest_token(live_server, monkeypatch):
    monkeypatch.setattr(server.Handler, "token", "sekrit")
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(live_server, "POST", path="/bot-check", payload={}, token="wrong")
    assert exc.value.code == 403


def test_bot_check_rejects_a_web_origin(live_server):
    req = urllib.request.Request(
        f"{live_server}/bot-check", data=b"{}", method="POST",
        headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 403


# --- rescanning recorded items ----------------------------------------------


def _record_items(store, entries):
    for item_id, title, value in entries:
        store.mark_seen(item_id, title, value)
    store.flush()


def test_rescan_reports_without_sending_by_default(live_server, monkeypatch):
    """Dedupe means a changed filter never revisits recorded items."""
    monkeypatch.setenv("RULES_JSON", json.dumps({"rules": [
        {"name": "hunt", "min_value_usd": 80.0, "keywords": ["vanity", "mirror"]}
    ]}))
    _record_items(server.Handler.store, [
        ("ip-1", "Hollywood Vanity Mirror with Lights", 99.0),
        ("ip-2", "Bulife GPS Wireless Collar", 83.99),
        ("ip-3", "Bathroom Vanity Light", 49.99),
    ])

    status, body = request(live_server, "POST", path="/rescan", payload={})
    assert status == 200
    assert body["applied"] is False
    assert body["matched"] == 1
    assert body["sample"][0]["item_id"] == "ip-1"
    # Reporting must not notify: a loosened filter can match thousands.
    assert not [s for n in RecordingNotifier.instances for s in n.sent]


def test_rescan_alerts_only_when_asked(live_server, monkeypatch):
    monkeypatch.setenv("RULES_JSON", json.dumps({"rules": [
        {"name": "hunt", "min_value_usd": 80.0, "keywords": ["mirror"]}
    ]}))
    _record_items(server.Handler.store, [
        ("ip-1", "Hollywood Vanity Mirror with Lights", 99.0),
        ("ip-2", "Unrelated item", 120.0),
    ])

    status, body = request(
        live_server, "POST", path="/rescan", payload={"apply": True}
    )
    assert status == 200 and body["alerted"] == 1
    sent = [s for n in RecordingNotifier.instances for s in n.sent]
    assert [item_id for item_id, _, _ in sent] == ["ip-1"]


def test_rescan_caps_the_alert_burst_and_prefers_high_values(live_server, monkeypatch):
    monkeypatch.setenv("RULES_JSON", json.dumps({"rules": [{"name": "everything"}]}))
    _record_items(server.Handler.store, [
        (f"ip-{index}", f"item {index}", float(index)) for index in range(40)
    ])

    _, body = request(
        live_server, "POST", path="/rescan", payload={"apply": True, "limit": 5}
    )
    assert body["alerted"] == 5
    assert body["suppressed"] == body["matched"] - 5

    sent = [s for n in RecordingNotifier.instances for s in n.sent]
    # The budget goes to the most valuable items, not to arbitrary ones.
    assert [value for _, _, value in sent] == [39.0, 38.0, 37.0, 36.0, 35.0]


def test_rescan_limit_cannot_exceed_the_hard_cap(live_server, monkeypatch):
    monkeypatch.setenv("RULES_JSON", json.dumps({"rules": [{"name": "everything"}]}))
    _record_items(server.Handler.store, [
        (f"ip-{index}", f"item {index}", 1.0) for index in range(60)
    ])

    _, body = request(
        live_server, "POST", path="/rescan", payload={"apply": True, "limit": 10_000}
    )
    assert body["alerted"] == server.RESCAN_ALERT_LIMIT


def test_rescan_rejects_a_non_object_body(live_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(live_server, "POST", path="/rescan", payload=["not", "an", "object"])
    assert exc.value.code == 400


def test_rescan_requires_the_token_and_extension_origin(live_server, monkeypatch):
    monkeypatch.setattr(server.Handler, "token", "sekrit")
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(live_server, "POST", path="/rescan", payload={}, token="wrong")
    assert exc.value.code == 403


def scanner_settings_payload():
    return {
        "schemaVersion": 1, "auxiliaryDepartments": True, "auxiliaryKeywords": True,
        "auxiliaryBrands": False, "auxiliaryKeywordQueries": ["Chair", "Pet"],
        "groupScannerTabs": True, "rotateScannerTabs": True,
        "verificationAssistEnabled": True, "verificationNativeEnabled": False,
        "pathPattern": "^/reviews/claim-product",
    }


def scanner_status_payload():
    return {
        "schemaVersion": 1, "extensionVersion": "2.22.0", "exportedAt": 100_000,
        "settings": scanner_settings_payload(),
        "primary": {"tabId": None, "generation": 0, "phase": "unknown", "reachable": False,
                    "page": 0, "totalPages": None, "visitedPages": 0, "lastActivityAt": 0,
                    "nextActionAt": 0},
        "lanes": [],
        "verification": {"pausedUntil": 0, "count": 0, "lastAt": 0, "assistEnabled": True,
                         "nativeEnabled": False, "statusCode": "watching", "attempt": 0,
                         "attemptLimit": 10, "lastOutcome": "", "errorStage": "",
                         "errorReason": "", "pressSent": None, "cleanupConfirmed": None,
                         "retryAt": 0},
        "sync": {"settingsOk": True, "settingsSyncedAt": 100_000, "commandsOk": True},
        "commands": {"restartHome": None, "restartAdditional": None}, "errors": [],
    }


@pytest.mark.parametrize("path,key,payload,store", [
    ("/scanner-settings", "settings", scanner_settings_payload(), "scanner_settings"),
    ("/scanner-commands", "commands", {"schemaVersion": 1, "restartHomeId": "new_1",
                                      "restartAdditionalId": None}, "scanner_commands"),
])
def test_scanner_control_roundtrip_observes_current_disk_and_keeps_relay_contract(
    live_server, path, key, payload, store,
):
    assert get(live_server, path) == (200, {key: None})
    assert request(live_server, "PUT", path, payload) == (200, {key: payload})
    assert get(live_server, path) == (200, {key: payload})
    updated = dict(payload)
    updated["rotateScannerTabs" if key == "settings" else "restartHomeId"] = (
        False if key == "settings" else "next_2"
    )
    getattr(server.Handler, store).path.write_text(json.dumps(updated), encoding="utf-8")
    assert get(live_server, path) == (200, {key: updated})
    assert get(live_server, "/relay-settings") == (200, {"settings": None})
    assert len(server.Handler.store) == 0 and server.Handler.notifier.sent == []


def test_scanner_status_roundtrip_is_safe_health_traffic_and_reads_current_file(live_server):
    assert get(live_server, "/scanner-status") == (
        200, {"receivedAt": 0, "report": None, "stale": True},
    )
    before = server.Handler.runtime_status.snapshot()["last_successful_relay"]
    report = scanner_status_payload()
    assert request(live_server, "POST", "/scanner-status", report) == (200, {"ok": True})
    snapshot = get(live_server, "/scanner-status")[1]
    assert snapshot["report"] == report and snapshot["stale"] is False
    assert snapshot["receivedAt"] > 0
    snapshot["report"]["primary"]["phase"] = "waiting-user"
    snapshot.pop("stale")
    server.Handler.scanner_status.path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert get(live_server, "/scanner-status")[1]["report"]["primary"]["phase"] == "waiting-user"
    assert server.Handler.runtime_status.snapshot()["last_successful_relay"] == before
    assert server.Handler.notifier.sent == []


@pytest.mark.parametrize("path,method", [
    ("/scanner-settings", "GET"), ("/scanner-settings", "PUT"),
    ("/scanner-commands", "GET"), ("/scanner-commands", "PUT"),
    ("/scanner-status", "GET"), ("/scanner-status", "POST"),
])
def test_all_scanner_routes_require_the_existing_token_and_reject_web_origins(
    live_server, path, method,
):
    server.Handler.token = "existing-secret"
    for headers in [{}, {"X-Ingest-Token": "existing-secret", "Origin": "https://evil.example"}]:
        req = urllib.request.Request(f"{live_server}{path}", None if method == "GET" else b"{}",
                                     {**headers, "Content-Type": "application/json"}, method=method)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req, timeout=5)
        assert error.value.code == 403


@pytest.mark.parametrize("path,method,maximum", [
    ("/scanner-settings", "PUT", 16 * 1024), ("/scanner-commands", "PUT", 16 * 1024),
    ("/scanner-status", "POST", 64 * 1024),
])
def test_scanner_mutations_require_json_and_reject_oversize_before_reading_body(
    live_server, path, method, maximum,
):
    for headers, expected in [
        ({"Content-Type": "text/plain"}, 415),
        ({"Content-Type": "application/json", "Content-Length": str(maximum + 1)}, 413),
    ]:
        req = urllib.request.Request(f"{live_server}{path}", b"{}", headers, method=method)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req, timeout=5)
        assert error.value.code == expected


@pytest.mark.parametrize("path,method", [
    ("/scanner-settings", "PUT"), ("/scanner-commands", "PUT"), ("/scanner-status", "POST"),
])
def test_scanner_route_validation_and_duplicate_keys_do_not_disclose_payloads(
    live_server, path, method,
):
    for raw in [b'{"secret":"private-payload"}', b'{"schemaVersion":1,"schemaVersion":1}',
                b"private-payload invalid JSON", b"\xff"]:
        req = urllib.request.Request(f"{live_server}{path}", raw,
                                     {"Content-Type": "application/json"}, method=method)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req, timeout=5)
        assert error.value.code == 400
        assert b"private" not in error.value.read()


@pytest.mark.parametrize("path,method,store,payload", [
    ("/scanner-settings", "PUT", "scanner_settings", scanner_settings_payload()),
    ("/scanner-commands", "PUT", "scanner_commands",
     {"schemaVersion": 1, "restartHomeId": None, "restartAdditionalId": "next-1"}),
    ("/scanner-status", "POST", "scanner_status", scanner_status_payload()),
])
def test_scanner_store_io_failures_return_generic_errors_without_mutating_files(
    live_server, monkeypatch, path, method, store, payload,
):
    target = getattr(server.Handler, store)
    original = target.path.read_bytes() if target.path.exists() else None

    def fail_write(*args):
        raise OSError("private filesystem path and details")

    monkeypatch.setattr(target, "_write", fail_write)
    with pytest.raises(urllib.error.HTTPError) as error:
        request(live_server, method, path, payload)
    assert error.value.code == 500
    assert b"private" not in error.value.read()
    assert (target.path.read_bytes() if target.path.exists() else None) == original


@pytest.mark.parametrize("path,store", [
    ("/scanner-settings", "scanner_settings"), ("/scanner-commands", "scanner_commands"),
    ("/scanner-status", "scanner_status"),
])
def test_scanner_get_rejects_invalid_current_files_with_fixed_errors(live_server, path, store):
    getattr(server.Handler, store).path.write_text("private invalid json", encoding="utf-8")
    with pytest.raises(urllib.error.HTTPError) as error:
        get(live_server, path)
    assert error.value.code == 400
    assert b"private" not in error.value.read()


@pytest.mark.parametrize("path,store,method", [
    ("/scanner-settings", "scanner_settings", "read"),
    ("/scanner-commands", "scanner_commands", "read"),
    ("/scanner-status", "scanner_status", "snapshot"),
])
def test_scanner_get_io_errors_are_fixed_and_payload_free(
    live_server, monkeypatch, path, store, method,
):
    def denied_read():
        raise PermissionError("private path and OS details")

    monkeypatch.setattr(getattr(server.Handler, store), method, denied_read)
    with pytest.raises(urllib.error.HTTPError) as error:
        get(live_server, path)
    assert error.value.code == 500
    assert b"private" not in error.value.read()


def test_scanner_settings_accept_existing_extension_origin_and_correct_token(live_server):
    server.Handler.token = "existing-secret"
    req = urllib.request.Request(
        f"{live_server}/scanner-settings", json.dumps(scanner_settings_payload()).encode(),
        {"Content-Type": "application/json", "Origin": "chrome-extension://current-extension",
         "X-Ingest-Token": "existing-secret"}, method="PUT",
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        assert response.status == 200
        assert json.load(response) == {"settings": scanner_settings_payload()}


def test_scanner_status_mirrors_safe_optional_verification_visibility_metadata(live_server):
    report = scanner_status_payload()
    report["verification"].update(tabActive=False, tabFrozen=True, windowFocused=None)
    assert request(live_server, "POST", "/scanner-status", report) == (200, {"ok": True})
    assert get(live_server, "/scanner-status")[1]["report"] == report


def test_scanner_status_mirrors_safe_optional_lane_failure_code(live_server):
    report = scanner_status_payload()
    report["lanes"] = [{
        "lane": "departments", "tabId": 2, "generation": 1, "status": "partial",
        "targetLabel": "", "completedTargets": 1, "totalTargets": 1, "pages": 0,
        "failures": 1, "truncatedTargets": 0, "coverageLimitedTargets": 0, "nextAt": 0,
        "round": 1, "lastErrorCode": "target-error", "owned": True, "enabled": True,
        "lastFailureCode": "missing-panel",
    }]
    assert request(live_server, "POST", "/scanner-status", report) == (200, {"ok": True})
    assert get(live_server, "/scanner-status")[1]["report"] == report
