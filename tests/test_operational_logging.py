"""Real HTTP receipts are correlated and exclude request contents."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from discord_bridge.telemetry_context import bind_request, current_request_id

import server
from operational_logging import OperationalLog
from state import SeenStore


@pytest.fixture
def api(tmp_path):
    class Notifier:
        last_diagnostic = "fixture"

        def send(self, *args, **kwargs):
            return True

    class Handler(server.Handler):
        store = SeenStore(tmp_path / "seen.json")
        token = "fixture-auth"
        notifier = Notifier()
        runtime_status = server.RuntimeStatus(store)
        browser_diagnostics = server.BrowserDiagnosticsStore(":memory:")
        relay_settings = server.RelaySettingsStore(":memory:")
        scanner_settings = server.ScannerSettingsStore(":memory:")
        scanner_commands = server.ScannerCommandsStore(":memory:")
        scanner_status = server.ScannerStatusStore(":memory:")

    class RecordedLog(OperationalLog):
        def __init__(self):
            super().__init__(tmp_path)
            self.completed = threading.Event()

        def emit(self, event, **fields):
            result = super().emit(event, **fields)
            if event == "http_request":
                self.completed.set()
            return result

    Handler.operations = RecordedLog()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", Handler
    finally:
        httpd.shutdown()
        httpd.server_close()
        Handler.store.close()


def test_authenticated_request_echoes_correlation_identity(api):
    base, _ = api
    request_id = "a" * 32
    request = urllib.request.Request(
        base + "/health", headers={"X-Ingest-Token": "fixture-auth", "X-Request-ID": request_id}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.headers.get("X-Request-ID") == request_id
        assert json.load(response)["status"] == "ok"


def test_rejected_request_still_has_a_safe_receipt_id(api):
    base, _ = api
    request = urllib.request.Request(
        base + "/health?private=do-not-log", headers={"X-Request-ID": "private-not-an-identity"}
    )
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(request, timeout=5)
    assert raised.value.code == 403
    request_id = raised.value.headers.get("X-Request-ID", "")
    assert len(request_id) == 32 and all(c in "0123456789abcdef" for c in request_id)


def receipts(handler):
    assert handler.operations.completed.wait(3), "Expected a completed request log receipt"
    return [json.loads(line) for line in handler.operations._writer.path.read_text().splitlines()]


def test_http_log_contains_timing_and_status_without_headers_query_or_path_secrets(api):
    base, handler = api
    request = urllib.request.Request(
        base + "/not-a-route-secret?token=query-secret",
        headers={"X-Ingest-Token": "fixture-auth", "Authorization": "private-bearer"},
    )
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(request, timeout=5)
    rows = receipts(handler)
    completed = [row for row in rows if row["event"] == "http_request"]
    assert len(completed) == 1
    assert completed[0]["route"] == "other"
    assert completed[0]["status"] == 404 and completed[0]["duration_ms"] >= 0
    assert completed[0]["severity"] == "warning"
    assert all(
        secret not in json.dumps(rows)
        for secret in ("fixture-auth", "private-bearer", "query-secret", "not-a-route-secret")
    )


def test_logging_disk_failure_does_not_change_successful_http_response(api, monkeypatch):
    base, handler = api

    def fail(_value):
        raise OSError("private disk detail")

    monkeypatch.setattr(handler.operations._writer, "append", fail)
    request = urllib.request.Request(base + "/health", headers={"X-Ingest-Token": "fixture-auth"})
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200
    assert handler.operations.completed.wait(3)
    health = handler.operations.status()
    assert health["dropped_events"] == 2 and health["last_error"] == "storage"
    assert "private disk detail" not in json.dumps(health)


def test_context_is_nested_and_resets_after_exceptions():
    assert current_request_id() is None
    with bind_request("a" * 32):
        assert current_request_id() == "a" * 32
        with pytest.raises(RuntimeError), bind_request("b" * 32):
            assert current_request_id() == "b" * 32
            raise RuntimeError("private message")
        assert current_request_id() == "a" * 32
    assert current_request_id() is None
    with (
        pytest.raises(ValueError, match="invalid telemetry request identity"),
        bind_request("not-a-valid-id"),
    ):
        pytest.fail("An invalid identity must never be bound")


def test_operational_schema_omits_arbitrary_fields_and_preserves_request_context(tmp_path):
    sink = OperationalLog(tmp_path)
    with bind_request("c" * 32):
        assert sink.emit(
            "service_error",
            error_type="TimeoutError",
            token="private-token",
            body={"account": "private-account"},
            route="/private-query?secret=1",
        )
    row = json.loads(sink._writer.path.read_text())
    assert row["request_id"] == "c" * 32 and row["error_type"] == "TimeoutError"
    assert row["severity"] == "error"
    assert not {"body", "token", "route"} & row.keys()
    assert "private" not in json.dumps(row)


def test_operational_log_rotation_remains_bounded(tmp_path):
    sink = OperationalLog(tmp_path, max_bytes=512, total_bytes=2048)
    for _ in range(60):
        assert sink.emit("service_started", component="backend")
    files = [p for p in tmp_path.glob("operations.jsonl*") if p.is_file()]
    assert sum(p.stat().st_size for p in files) <= 2048
    assert sink.status()["events_written"] == 60


def test_configuration_mutation_receipt_carries_no_settings_values(api):
    base, handler = api
    settings = {
        "schemaVersion": 1,
        "refreshMinutes": 5,
        "pageDelaySeconds": 15,
        "sweepMode": "none",
    }
    request = urllib.request.Request(
        base + "/relay-settings",
        json.dumps(settings).encode(),
        {"X-Ingest-Token": "fixture-auth", "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200
    # The HTTP completion is written just before the separate config receipt.
    assert handler.operations.completed.wait(3)
    handler.operations.completed.clear()
    followup = urllib.request.Request(base + "/health", headers={"X-Ingest-Token": "fixture-auth"})
    with urllib.request.urlopen(followup, timeout=5) as response:
        response.read()
    rows = receipts(handler)
    changed = [row for row in rows if row["event"] == "configuration_changed"]
    assert len(changed) == 1 and changed[0]["component"] == "relay-settings"
    assert changed[0]["outcome"] == "saved"
    assert "pageDelaySeconds" not in json.dumps(rows)


def test_ingest_trace_links_http_pipeline_and_durable_acceptance(api, monkeypatch):
    base, handler = api
    monkeypatch.setenv("RULES_JSON", '{"rules":[{"name":"fixture","min_value_usd":1}]}')
    request_id = "d" * 32
    payload = {
        "items": [
            {
                "item_id": "ip-12345678",
                "title": "Fixture product",
                "value_usd": 25,
                "url": "https://www.walmart.com/ip/12345678",
            }
        ]
    }
    request = urllib.request.Request(
        base + "/ingest",
        json.dumps(payload).encode(),
        {
            "X-Ingest-Token": "fixture-auth",
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
        },
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert json.load(response)["notified"] == 1
    rows = receipts(handler)
    assert {row["event"] for row in rows} == {
        "http_request_started",
        "pipeline_item",
        "intake_result",
        "http_request",
    }
    assert {row["request_id"] for row in rows} == {request_id}
    assert {row["entry_point"] for row in rows} == {"http"}
    item = next(row for row in rows if row["event"] == "pipeline_item")
    assert item["item_id"] == "ip-12345678" and item["decision"] == "sent"
    assert next(row for row in rows if row["event"] == "http_request")["request_bytes"] > 0


def test_pipeline_instrumentation_cannot_change_dedup_or_delivery_on_logger_error():
    from filters import Rule
    from models import Item
    from pipeline import process

    store = SeenStore("")
    delivered = []

    class Notifier:
        def send(self, item, priority="normal"):
            delivered.append(item.item_id)
            return True

    def broken_sink(*args, **kwargs):
        raise OSError("simulated logging failure")

    item = Item(title="Fixture", url="https://www.walmart.com/ip/12345678", value_usd=25)
    first = process([item], [Rule(min_value_usd=1)], store, Notifier(), event_sink=broken_sink)
    second = process([item], [Rule(min_value_usd=1)], store, Notifier(), event_sink=broken_sink)
    assert first.notified == 1 and second.duplicates == 1
    assert delivered == ["ip-12345678"]


def test_failed_log_initialization_is_observable_and_nonfatal(tmp_path, monkeypatch):
    import operational_logging

    def unavailable(*args, **kwargs):
        raise OSError("private initialization detail")

    monkeypatch.setattr(operational_logging, "RollingLog", unavailable)
    sink = OperationalLog(tmp_path)
    assert sink.emit("service_started") is False
    assert sink.emit("service_error") is False
    status = sink.status()
    assert status["configured"] is True and status["enabled"] is False
    assert status["dropped_events"] == 2 and status["last_error"] == "storage"
    assert "private initialization detail" not in json.dumps(status)


def test_startup_failure_is_logged_before_the_server_loop(tmp_path, monkeypatch):
    def failed_setup():
        raise ValueError("private startup data")

    monkeypatch.setattr(server, "build_notifier", failed_setup)
    monkeypatch.setattr(server.Handler, "operations", None)
    with pytest.raises(ValueError, match="private startup data"):
        server.serve("127.0.0.1", 8787, str(tmp_path / "seen.json"))
    rows = [json.loads(line) for line in (tmp_path / "operations.jsonl").read_text().splitlines()]
    assert [row["event"] for row in rows] == ["service_starting", "service_error"]
    assert rows[-1]["error_type"] == "ValueError"
    assert "private startup data" not in json.dumps(rows)
    server.Handler.store.close()
