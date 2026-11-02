"""Traffic waiting fits the existing private diagnostic/status wire boundaries."""

import pytest

import server
from browser_diagnostics import BrowserDiagnosticsStore, _validate_report
from scanner_control import validate_status
from state import SeenStore
from test_browser_diagnostics import NOW
from test_browser_diagnostics import report as diagnostic_report
from test_scanner_control import report as scanner_report


@pytest.mark.parametrize("event", ["traffic-wait", "traffic-resumed"])
def test_traffic_events_survive_strict_validation_without_new_schema_fields(event):
    incoming = diagnostic_report()
    incoming["events"] = [{"at": NOW, "event": event, "lane": "keywords", "tabId": 7,
                           "generation": 9, "documentId": NOW - 1000, "durationMs": 30_000}]
    assert _validate_report(incoming) == incoming


@pytest.mark.parametrize("event", ["traffic-wait", "traffic-resumed"])
def test_traffic_events_persist_across_backend_restart(tmp_path, monkeypatch, event):
    monkeypatch.setattr("browser_diagnostics.time.time", lambda: NOW / 1000)
    incoming = diagnostic_report()
    incoming["events"] = [{"at": NOW, "event": event,
                           "documentId": NOW - 1000, "durationMs": 30_000}]
    path = tmp_path / "browser-diagnostics.json"
    BrowserDiagnosticsStore(path).record_report(incoming)
    assert BrowserDiagnosticsStore(path).snapshot()["report"] == incoming


@pytest.mark.parametrize("field,value", [
    ("durationMs", True), ("durationMs", -1), ("durationMs", float("nan")),
    ("documentId", "private content"), ("text", "private content"),
    ("url", "https://private.invalid"), ("highTraffic", True),
])
def test_traffic_diagnostics_keep_existing_numeric_and_privacy_limits(field, value):
    incoming = diagnostic_report()
    incoming["events"] = [{"at": NOW, "event": "traffic-wait", "durationMs": 0, field: value}]
    with pytest.raises(ValueError, match="^Invalid browser diagnostics report$"):
        _validate_report(incoming)


def test_waiting_traffic_is_valid_for_both_primary_and_lane_runtime():
    incoming = scanner_report()
    incoming["primary"]["phase"] = "waiting-traffic"
    incoming["lanes"][0]["runtime"] = {
        "phase": "waiting-traffic", "reachable": True, "active": True, "busy": False,
        "visibility": "visible", "focusReason": "none",
        "lastActivityAt": 100_000, "nextActionAt": 110_000,
    }
    assert validate_status(incoming) == incoming


def test_probe_only_high_traffic_boolean_is_not_added_to_persisted_status():
    incoming = scanner_report()
    incoming["primary"]["highTraffic"] = True
    with pytest.raises(ValueError, match="^Invalid scanner status$"):
        validate_status(incoming)


def test_older_reports_keep_their_exact_wire_shape():
    assert _validate_report(diagnostic_report()) == diagnostic_report()
    assert validate_status(scanner_report()) == scanner_report()


def traffic_heartbeat():
    return {"phase": "waiting-traffic", "tab_count": 3, "reachable": True,
            "refresh_minutes": 5, "paused_until": 0}


def test_traffic_heartbeat_is_reachable_without_fabricating_delivery(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    try:
        runtime = server.RuntimeStatus(store)
        runtime.record_browser(traffic_heartbeat())
        snapshot = runtime.snapshot()
        assert snapshot["browser"]["phase"] == "waiting-traffic"
        assert snapshot["browser"]["reachable"] is True
        assert snapshot["browser"]["paused_until"] == 0
        assert snapshot["last_successful_relay"] is None
        assert snapshot["last_relay_attempt"] is None
        assert snapshot["last_notification"] is None
    finally:
        store.close()


def test_human_status_explains_traffic_wait_without_verification_or_reload_prompt():
    text = "\n".join(server._browser_status_lines({**traffic_heartbeat(), "age_seconds": 0}))
    assert "Waiting for Walmart high traffic to clear; scanning resumes automatically" in text
    assert "verification" not in text
    assert "No reviewer tab is responding" not in text
    assert "reload" not in text
