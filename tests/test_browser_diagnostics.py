"""Strict, private browser telemetry persists independently of item dedupe."""

import gzip
import json
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

import atomic_json
import browser_diagnostics
from browser_diagnostics import BrowserDiagnosticsStore

NOW = 1_792_555_200_000
DAY_MS = 86_400_000


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(browser_diagnostics.time, "time", lambda: NOW / 1000)


def report():
    return {
        "schemaVersion": 1,
        "extensionVersion": "2.6.0",
        "exportedAt": NOW,
        "settings": {"refreshMinutes": 10, "pageDelaySeconds": 15, "sweepMode": "none"},
        "verification": {"count": 1, "lastAt": NOW - 1000, "pausedUntil": NOW + 60_000},
        "events": [{"at": NOW - 1000, "event": "challenge", "reason": "dom-marker"}],
    }


@pytest.mark.parametrize("event", ["auxiliary-target-retry", "auxiliary-target-skipped"])
@pytest.mark.parametrize("failure_code", ["document-http-error", "navigation-scope-changed"])
def test_recovery_identity_and_failure_code_survive_strict_boundary(event, failure_code):
    incoming = report()
    incoming["events"] = [{"at": NOW, "event": event, "lane": "departments",
                           "tabId": 7, "generation": 9, "documentId": NOW - 1000,
                           "round": 4, "targetIndex": 3, "attempt": 2,
                           "failureCode": failure_code, "responseStatus": 520}]
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert store.snapshot()["report"]["events"] == incoming["events"]


@pytest.mark.parametrize("outcome", ["ready", "interrupted", "timeout", "http-error"])
def test_navigation_outcome_survives_strict_boundary(outcome):
    incoming = report()
    incoming["events"] = [{"at": NOW, "event": "navigation-outcome", "outcome": outcome,
                           "documentId": NOW - 1000, "targetPage": 4, "responseStatus": 520}]
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert store.snapshot()["report"]["events"] == incoming["events"]


@pytest.mark.parametrize("field,value", [("outcome", "private body"), ("responseStatus", 999)])
def test_navigation_outcome_rejects_unbounded_values(field, value):
    incoming = report()
    incoming["events"] = [{"at": NOW, "event": "navigation-outcome", "outcome": "timeout",
                           "responseStatus": 520, field: value}]
    with pytest.raises(ValueError, match="Invalid browser diagnostics report"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def page_state_event():
    return {
        "at": NOW, "event": "page-state", "lane": "home", "stage": "empty-observed",
        "tabId": 42, "generation": 7, "documentId": NOW - 1000, "page": 1,
        "attempt": 2, "items": 0, "pages": 0, "elapsedMs": 30_000, "mode": "none",
        "state": {
            "readyState": "complete", "visibility": "hidden", "route": "reviewer",
            "reviewerShell": True, "resultsLabel": False, "noResults": False,
            "loading": True, "error": False, "signIn": False, "productLinks": 0,
            "offerLines": 0, "pagerPresent": False, "renderedPage": 0,
            "navigationType": "reload", "responseStatus": 200, "failedRequests": 2,
            "readingFailures": 0, "readingWorkMs": 0, "queryIndex": 3, "queryCount": 100,
        },
    }


@pytest.mark.parametrize("stage", ["empty-observed", "empty-confirmation", "fallback",
                                  "unusable", "zero-page-complete", "recovered"])
def test_page_state_survives_archive_snapshot_restart_without_duplicates(tmp_path, stage):
    incoming = report()
    event = page_state_event()
    event["stage"] = stage
    incoming["events"] = [event]
    path = tmp_path / "browser-diagnostics.json"
    BrowserDiagnosticsStore(path).record_report(incoming)
    restarted = BrowserDiagnosticsStore(path)
    assert restarted.snapshot()["report"] == incoming
    restarted.record_report(incoming)
    events = archived_events(path)
    assert len(events) == 1
    assert {key: value for key, value in events[0].items()
            if key not in {"entry_point", "extensionVersion"}} == event
    assert path.stat().st_size <= browser_diagnostics.MAX_FILE_BYTES


@pytest.mark.parametrize("field", list(page_state_event()["state"]))
@pytest.mark.parametrize("value", [None, [], {}, "private-value"])
def test_page_state_rejects_malformed_required_facts(field, value):
    incoming = report()
    event = page_state_event()
    event["state"][field] = value
    incoming["events"] = [event]
    with pytest.raises(ValueError, match="^Invalid browser diagnostics report$"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


@pytest.mark.parametrize("field,maximum", [
    ("productLinks", 10000), ("offerLines", 10000), ("renderedPage", 100000),
    ("failedRequests", 10000), ("readingFailures", 10000), ("readingWorkMs", 10**15),
    ("queryIndex", 10000), ("queryCount", 10000),
])
@pytest.mark.parametrize("value", [True, -1, 0.5, float("nan"), float("inf"), "overflow"])
def test_page_state_rejects_noninteger_and_out_of_range_counts(field, maximum, value):
    incoming = report()
    event = page_state_event()
    event["state"][field] = maximum + 1 if value == "overflow" else value
    incoming["events"] = [event]
    with pytest.raises(ValueError, match="^Invalid browser diagnostics report$"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


@pytest.mark.parametrize("value", [True, -1, 1, 99, 600, 200.5])
def test_page_state_rejects_invalid_response_status(value):
    incoming = report()
    event = page_state_event()
    event["state"]["responseStatus"] = value
    incoming["events"] = [event]
    with pytest.raises(ValueError, match="^Invalid browser diagnostics report$"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


@pytest.mark.parametrize("value", [0, 100, 599])
def test_page_state_accepts_unknown_response_status_and_valid_boundaries(value):
    incoming = report()
    event = page_state_event()
    event["state"].update(productLinks=10000, offerLines=10000, renderedPage=100000,
                          failedRequests=10000, readingFailures=10000, readingWorkMs=10**15,
                          queryIndex=10000, queryCount=10000, responseStatus=value)
    incoming["events"] = [event]
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert store.snapshot()["report"] == incoming


@pytest.mark.parametrize("field", ["reviewerShell", "resultsLabel", "noResults", "loading",
                                  "error", "signIn", "pagerPresent"])
@pytest.mark.parametrize("value", [0, 1])
def test_page_state_does_not_coerce_numbers_to_boolean_facts(field, value):
    incoming = report()
    event = page_state_event()
    event["state"][field] = value
    incoming["events"] = [event]
    with pytest.raises(ValueError):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


@pytest.mark.parametrize("field", ["lane", "stage", "state"])
def test_page_state_requires_its_context(field):
    incoming = report()
    event = page_state_event()
    del event[field]
    incoming["events"] = [event]
    with pytest.raises(ValueError):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_page_state_rejects_missing_unknown_and_private_state_fields():
    for field in page_state_event()["state"]:
        incoming = report()
        event = page_state_event()
        del event["state"][field]
        incoming["events"] = [event]
        with pytest.raises(ValueError):
            BrowserDiagnosticsStore(":memory:").record_report(incoming)
    for field in ("url", "text", "html", "account", "unknown"):
        incoming = report()
        event = page_state_event()
        event["state"][field] = "private"
        incoming["events"] = [event]
        with pytest.raises(ValueError):
            BrowserDiagnosticsStore(":memory:").record_report(incoming)


@pytest.mark.parametrize("field", ["state", "stage"])
def test_page_state_context_cannot_attach_to_older_events(field):
    incoming = report()
    incoming["events"][0][field] = page_state_event()[field]
    with pytest.raises(ValueError):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_page_state_history_remains_under_the_existing_report_bound():
    incoming = report()
    incoming["events"] = [page_state_event() for _ in range(browser_diagnostics.MAX_EVENTS)]
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert len(store.snapshot()["report"]["events"]) == browser_diagnostics.MAX_EVENTS
    assert len(json.dumps(store.snapshot()).encode()) < browser_diagnostics.MAX_FILE_BYTES


def assert_snapshot_files(path):
    assert {entry.name for entry in path.parent.iterdir()} == {
        path.name, "browser-diagnostic-events.jsonl", ".browser-diagnostic-events.jsonl.lock",
    }


def test_empty_memory_store_has_no_report():
    assert BrowserDiagnosticsStore(":memory:").snapshot() == {"receivedAt": 0, "report": None}


@pytest.mark.parametrize("event", ["focus-granted", "focus-denied", "focus-released",
                                  "pager-clamped", "page-repeat"])
def test_performance_events_survive_restart_without_private_context(tmp_path, event):
    incoming = report()
    detail = {"at": NOW, "event": event, "page": 85, "targetPage": 25,
              "durationMs": 203, "elapsedMs": 5000}
    if event.startswith("focus-"):
        detail.update(operation="page-scroll", reason="waiting-turn")
    incoming["events"] = [detail]
    path = tmp_path / "browser-diagnostics.json"
    BrowserDiagnosticsStore(path).record_report(incoming)
    assert BrowserDiagnosticsStore(path).snapshot()["report"] == incoming


@pytest.mark.parametrize("operation", ["private-operation", True, 1, None])
def test_focus_events_reject_unrecognized_operations(operation):
    incoming = report()
    incoming["events"] = [{"at": NOW, "event": "focus-denied", "operation": operation}]
    with pytest.raises(ValueError):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_tab_recovery_event_survives_snapshot_restart(tmp_path):
    incoming = report()
    incoming["extensionVersion"] = "2.9.0"
    incoming["events"] = [{"at": NOW, "event": "tab-opened", "tabId": 12}]
    path = tmp_path / "browser-diagnostics.json"
    BrowserDiagnosticsStore(path).record_report(incoming)
    assert BrowserDiagnosticsStore(path).snapshot()["report"] == incoming


def test_native_pagination_backoff_reason_is_persisted(tmp_path):
    incoming = report()
    incoming["events"] = [{"at": NOW, "event": "navigation-fallback", "reason": "native-backoff"}]
    path = tmp_path / "browser-diagnostics.json"
    BrowserDiagnosticsStore(path).record_report(incoming)
    assert BrowserDiagnosticsStore(path).snapshot()["report"] == incoming


@pytest.mark.parametrize("reason,attempt,elapsed", [
    ("empty-results", 3, 31_000), ("blank-timeout", 0, 30_000),
])
def test_category_fallback_trigger_survives_diagnostic_restart(tmp_path, reason, attempt, elapsed):
    incoming = report()
    incoming["events"] = [{
        "at": NOW, "event": "sweep-start", "mode": "high-value", "reason": reason,
        "page": 6, "totalPages": 18, "attempt": attempt, "elapsedMs": elapsed,
    }]
    path = tmp_path / "browser-diagnostics.json"
    BrowserDiagnosticsStore(path).record_report(incoming)
    assert BrowserDiagnosticsStore(path).snapshot()["report"] == incoming


@pytest.mark.parametrize("total", [True, -1, "18", float("inf"), 1e16])
def test_fallback_total_pages_rejects_invalid_metadata(total):
    incoming = report()
    incoming["events"] = [{"at": NOW, "event": "sweep-start", "totalPages": total}]
    with pytest.raises(ValueError, match="^Invalid browser diagnostics report$"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_scan_performance_telemetry_survives_disk_roundtrip_and_restart(tmp_path):
    incoming = report()
    incoming["events"] = [
        {"at": NOW - 300, "event": "pacing", "delayMs": 7000,
         "intervalMs": 15_000, "elapsedMs": 8000},
        {"at": NOW - 200, "event": "scan-restart-native", "tabId": 11, "page": 1},
        {"at": NOW - 100, "event": "scan-restart-load", "tabId": 11, "page": 1},
        {"at": NOW, "event": "empty-boundary-reused", "page": 19, "attempt": 1},
    ]
    path = tmp_path / "browser-diagnostics.json"
    store = BrowserDiagnosticsStore(path)
    store.record_report(incoming)

    expected = {"receivedAt": NOW, "report": incoming}
    assert store.snapshot() == expected
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    assert BrowserDiagnosticsStore(path).snapshot() == expected
    assert expected["report"]["schemaVersion"] == 1


@pytest.mark.parametrize("field", ["intervalMs", "elapsedMs"])
@pytest.mark.parametrize("value", [
    True, None, "15000", "https://example.com/?token=secret", -1,
    float("nan"), float("inf"), float("-inf"), 1e16,
])
def test_scan_pacing_metadata_rejects_non_numeric_and_out_of_range_values(field, value):
    incoming = report()
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    before = store.snapshot()
    incoming["events"] = [{"at": NOW, "event": "pacing", field: value}]

    with pytest.raises(ValueError, match="^Invalid browser diagnostics report$"):
        store.record_report(incoming)

    assert store.snapshot() == before


@pytest.mark.parametrize("event", [
    "scan-restart-native", "scan-restart-load", "empty-boundary-reused",
])
def test_scan_performance_events_do_not_admit_raw_payload_fields(event):
    incoming = report()
    incoming["events"] = [{"at": NOW, "event": event,
                           "url": "https://example.com/?token=secret"}]
    with pytest.raises(ValueError, match="^Invalid browser diagnostics report$"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_pager_inspection_accepts_only_numeric_shape_counts():
    incoming = report()
    incoming["events"] = [{
        "at": NOW, "event": "pager-inspection", "targetPage": 2,
        "recognizedPagerCount": 0, "exactHrefCount": 0, "visibleExactHrefCount": 0,
        "queryMismatchCount": 2, "targetLabelButtonCount": 1, "nextLabelButtonCount": 1,
        "scopedButtonCount": 0, "roleButtonCount": 0,
        "pagerElementCount": 20, "pagerAnchorCount": 2, "pagerButtonCount": 0,
        "pagerInputCount": 0, "pagerNumericTextCount": 4, "pagerTargetTextCount": 2,
        "pagerTargetAriaCount": 0, "pagerHiddenCount": 0, "pagerMarkerCount": 2,
        "pagerPlaceholderHrefCount": 2, "pagerTargetPlaceholderCount": 1,
        "pagerPageHrefCount": 0, "pagerOtherHrefCount": 1,
    }]
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert store.snapshot()["report"]["events"] == incoming["events"]
    incoming["events"][0]["rawHtml"] = "private DOM and URL"
    with pytest.raises(ValueError, match="Invalid browser diagnostics report"):
        store.record_report(incoming)


def test_report_survives_restart_as_one_bounded_snapshot(tmp_path):
    path = tmp_path / "nested" / "browser-diagnostics.json"
    store = BrowserDiagnosticsStore(path)
    store.record_report(report())

    expected = {"receivedAt": NOW, "report": report()}
    assert store.snapshot() == expected
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    assert BrowserDiagnosticsStore(path).snapshot() == expected
    assert path.stat().st_size <= 128 * 1024
    assert_snapshot_files(path)


def test_record_and_snapshot_do_not_expose_mutable_state():
    incoming = report()
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    incoming["events"][0]["reason"] = "private incoming data"
    outgoing = store.snapshot()
    outgoing["report"]["events"].clear()
    outgoing["report"]["settings"]["sweepMode"] = "private outgoing data"

    assert store.snapshot()["report"] == report()


@pytest.mark.parametrize("value", [None, True, [], "secret-token", 1])
def test_report_must_be_an_object(value):
    with pytest.raises(ValueError, match="Invalid browser diagnostics report"):
        BrowserDiagnosticsStore(":memory:").record_report(value)


@pytest.mark.parametrize("section,field,value", [
    (None, "url", "https://example.com/?token=secret"),
    (None, "schemaVersion", True),
    (None, "schemaVersion", 2),
    (None, "extensionVersion", "<html>secret token</html>"),
    (None, "extensionVersion", "1." + "1" * 100_000),
    (None, "exportedAt", float("nan")),
    (None, "exportedAt", True),
    (None, "exportedAt", -1),
    (None, "exportedAt", 1e30),
    ("settings", "token", "secret"),
    ("settings", "refreshMinutes", 121),
    ("settings", "refreshMinutes", -1),
    ("settings", "refreshMinutes", True),
    ("settings", "pageDelaySeconds", 0.99),
    ("settings", "pageDelaySeconds", 61),
    ("settings", "pageDelaySeconds", float("inf")),
    ("settings", "sweepMode", "private-keyword"),
    ("verification", "count", 1.5),
    ("verification", "count", True),
    ("verification", "count", -1),
    ("verification", "pausedUntil", "secret"),
    ("verification", "lastAt", -1),
    ("verification", "lastAt", float("-inf")),
], ids=lambda value: "oversized" if isinstance(value, str) and len(value) > 100 else None)
def test_invalid_report_fields_are_rejected_without_echoing_values(section, field, value):
    incoming = report()
    target = incoming if section is None else incoming[section]
    target[field] = value
    store = BrowserDiagnosticsStore(":memory:")

    with pytest.raises(ValueError) as error:
        store.record_report(incoming)

    assert str(error.value) == "Invalid browser diagnostics report"
    assert store.snapshot() == {"receivedAt": 0, "report": None}


@pytest.mark.parametrize("section,field", [
    (None, "events"), (None, "settings"), (None, "verification"),
    (None, "extensionVersion"), ("settings", "sweepMode"), ("verification", "count"),
])
def test_required_report_fields_cannot_be_omitted(section, field):
    incoming = report()
    del (incoming if section is None else incoming[section])[field]
    with pytest.raises(ValueError):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


@pytest.mark.parametrize("section,value", [
    ("settings", []), ("verification", None), ("events", {}),
    ("events", [None]), ("events", [{"at": NOW}]), ("events", [{"event": "page-loaded"}]),
])
def test_nested_containers_and_required_event_fields_are_validated(section, value):
    incoming = report()
    incoming[section] = value
    with pytest.raises(ValueError, match="^Invalid browser diagnostics report$"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


@pytest.mark.parametrize("field,value", [
    ("event", "https://example.com/secret"), ("reason", "private DOM text"),
    ("mode", "private keyword"), ("url", "https://example.com/secret"),
    ("error", "secret provider error"), ("items", True), ("page", -1),
    ("delayMs", float("inf")), ("items", 1e16), ("at", -1),
    ("at", "secret"), ("at", True),
])
def test_event_allowlist_rejects_secret_bearing_and_malformed_fields(field, value):
    incoming = report()
    incoming["events"][0][field] = value
    with pytest.raises(ValueError, match="^Invalid browser diagnostics report$"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_all_supported_event_metadata_is_retained():
    incoming = report()
    metadata = dict.fromkeys([
        "tabId", "generation", "page", "targetPage", "delayMs", "count", "pages",
        "durationMs", "attempt", "items", "new", "duplicates", "matched", "notified",
        "failed", "pending",
    ], 4)
    incoming["events"] = [
        {"at": NOW, "event": event, "mode": "none", **metadata}
        for event in ("live-update", "navigation-ready", "page-delivered")
    ]
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert store.snapshot()["report"] == incoming


def test_every_current_event_and_reason_is_accepted():
    incoming = report()
    names = [
        "page-loaded", "page-delivered", "navigation-native", "navigation-fallback",
        "navigation-interrupted", "pacing", "challenge", "verification-resumed",
        "sweep-start", "sweep-complete", "ingest-retry", "empty-retry", "recovery-reload",
        "live-update", "navigation-ready",
    ]
    reasons = [
        "redirect", "dom-marker", "text", "probe", "missing-control", "native-stalled",
        "unusable", "unreachable", "scheduled", "manual", "installed", "failover",
    ]
    incoming["events"] = [{"at": NOW, "event": name} for name in names]
    incoming["events"] += [
        {"at": NOW, "event": "challenge", "reason": reason} for reason in reasons
    ]
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert store.snapshot()["report"] == incoming


def test_manifest_unknown_version_and_fractional_settings_are_supported():
    incoming = report()
    incoming["extensionVersion"] = "unknown"
    incoming["settings"]["refreshMinutes"] = 0.5
    incoming["settings"]["pageDelaySeconds"] = 5.5
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert store.snapshot()["report"] == incoming


def test_legacy_saved_one_second_page_delay_survives_validation_and_restart(tmp_path):
    incoming = report()
    incoming["settings"]["pageDelaySeconds"] = 1
    path = tmp_path / "snapshot.json"
    store = BrowserDiagnosticsStore(path)
    store.record_report(incoming)

    assert store.snapshot()["report"]["settings"]["pageDelaySeconds"] == 1
    assert BrowserDiagnosticsStore(path).snapshot()["report"] == incoming


def test_retention_keeps_only_events_from_last_seven_days(monkeypatch, tmp_path):
    incoming = report()
    incoming["events"] = [
        {"at": NOW - 7 * DAY_MS - 1, "event": "page-loaded"},
        {"at": NOW - 7 * DAY_MS, "event": "page-delivered"},
        {"at": NOW, "event": "challenge"},
        {"at": NOW + 1, "event": "page-loaded"},
    ]
    path = tmp_path / "snapshot.json"
    store = BrowserDiagnosticsStore(path)
    store.record_report(incoming)
    assert store.snapshot()["report"]["events"] == incoming["events"][1:3]

    monkeypatch.setattr(browser_diagnostics.time, "time", lambda: (NOW + DAY_MS) / 1000)
    assert store.snapshot()["report"]["events"] == incoming["events"][2:3]
    assert BrowserDiagnosticsStore(path).snapshot()["report"]["events"] == incoming["events"][2:3]


def test_event_count_is_bounded_before_retention():
    incoming = report()
    incoming["events"] = [{"at": 0, "event": "page-loaded"}] * 301
    with pytest.raises(ValueError):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_three_hundred_events_are_allowed(tmp_path):
    incoming = report()
    incoming["events"] = [{"at": NOW, "event": "page-loaded", "page": 3}] * 300
    path = tmp_path / "snapshot.json"
    store = BrowserDiagnosticsStore(path)
    store.record_report(incoming)
    assert len(store.snapshot()["report"]["events"]) == 300
    assert path.stat().st_size <= 128 * 1024


def test_oversized_valid_metadata_is_rejected_before_retention(monkeypatch):
    incoming = report()
    fields = dict.fromkeys([
        "tabId", "generation", "page", "targetPage", "delayMs", "count", "pages",
        "durationMs", "attempt", "items", "new", "duplicates", "matched", "notified",
        "failed", "pending",
    ], 1_000_000_000_000_000)
    incoming["events"] = [{"at": 0, "event": "page-delivered", **fields}] * 300
    encoded = json.dumps({"receivedAt": 0, "report": incoming}, separators=(",", ":")).encode()
    monkeypatch.setattr(browser_diagnostics, "MAX_FILE_BYTES", len(encoded) - 1)
    with pytest.raises(ValueError):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


@pytest.mark.parametrize("contents", [
    "private invalid payload", "[]", '{"receivedAt": 1, "report": null}',
    '{"receivedAt":true,"report":{}}', "x" * (1024 * 1024 + 1),
], ids=["invalid-json", "not-object", "null-report", "boolean-time", "oversized-file"])
def test_corrupt_or_oversized_snapshot_loads_empty_without_disclosing_payload(
    tmp_path, caplog, contents,
):
    path = tmp_path / "snapshot.json"
    path.write_text(contents, encoding="utf-8")
    assert BrowserDiagnosticsStore(path).snapshot() == {"receivedAt": 0, "report": None}
    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage() == (
        "Browser diagnostics snapshot unavailable; starting empty"
    )
    assert caplog.records[0].exc_info is None


def test_persisted_report_is_validated_again_on_restart(tmp_path):
    path = tmp_path / "snapshot.json"
    incoming = report()
    incoming["events"][0]["url"] = "https://example.com/?token=secret"
    path.write_text(json.dumps({"receivedAt": NOW, "report": incoming}), encoding="utf-8")
    assert BrowserDiagnosticsStore(path).snapshot() == {"receivedAt": 0, "report": None}


def test_failed_atomic_replace_preserves_prior_memory_and_disk(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.json"
    store = BrowserDiagnosticsStore(path)
    store.record_report(report())
    before_disk = path.read_bytes()
    before_memory = store.snapshot()

    def fail_replace(source, destination):
        raise OSError("disk unavailable")

    monkeypatch.setattr(atomic_json.os, "replace", fail_replace)
    incoming = report()
    incoming["verification"]["count"] = 2
    with pytest.raises(OSError):
        store.record_report(incoming)

    assert store.snapshot() == before_memory
    assert path.read_bytes() == before_disk
    assert_snapshot_files(path)


def test_invalid_new_report_does_not_replace_a_valid_snapshot(tmp_path):
    path = tmp_path / "snapshot.json"
    store = BrowserDiagnosticsStore(path)
    store.record_report(report())
    before = deepcopy(store.snapshot())
    incoming = report()
    incoming["token"] = "secret"
    with pytest.raises(ValueError):
        store.record_report(incoming)
    assert store.snapshot() == before
    assert BrowserDiagnosticsStore(path).snapshot() == before


def test_concurrent_writes_leave_a_complete_restartable_snapshot(tmp_path):
    path = tmp_path / "snapshot.json"
    store = BrowserDiagnosticsStore(path)

    def record_count(count):
        incoming = report()
        incoming["verification"]["count"] = count
        store.record_report(incoming)
        snapshot = store.snapshot()
        assert snapshot["report"]["verification"]["count"] in range(30)

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(record_count, range(30)))

    assert BrowserDiagnosticsStore(path).snapshot() == store.snapshot()
    assert_snapshot_files(path)


def scan_metrics():
    return {
        "measurementStartedAt": NOW - 10_000,
        "initialPages": 3, "successfulTransitions": 4, "challengeEpisodes": 1,
        "nativeAttempts": 6, "urlFallbacks": 3, "nativeBackoffFallbacks": 1,
        "nativeStalledFallbacks": 1, "nativeRetries": 2,
        "completedVerificationDowntimeMs": 1000, "verificationDowntimeMs": 2000,
        "verificationActiveSince": NOW - 1000, "episodesPer100Transitions": 25,
    }


def test_scan_metrics_and_new_event_metadata_survive_restart(tmp_path):
    incoming = report()
    incoming["metrics"] = scan_metrics()
    incoming["events"] = [
        {"at": NOW, "event": "results-page-ready", "tabId": 1,
         "documentId": NOW - 100, "page": 2, "generation": 4},
        {"at": NOW, "event": "navigation-retry", "reason": "native-stalled",
         "targetPage": 3, "durationMs": 15000},
    ]
    path = tmp_path / "metrics.json"
    BrowserDiagnosticsStore(path).record_report(incoming)
    assert BrowserDiagnosticsStore(path).snapshot()["report"] == incoming


@pytest.mark.parametrize("field", list(scan_metrics()))
@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), 1e16, "secret"])
def test_scan_metrics_reject_invalid_numbers_without_replacing_snapshot(field, value):
    incoming = report()
    incoming["metrics"] = scan_metrics()
    incoming["metrics"][field] = value
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(report())
    before = store.snapshot()
    with pytest.raises(ValueError, match="^Invalid browser diagnostics report$"):
        store.record_report(incoming)
    assert store.snapshot() == before


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(token="private-token"),
    lambda data: data.pop("initialPages"),
    lambda data: data.update(initialPages=1.5),
    lambda data: data.update(verificationActiveSince=NOW + 1),
    lambda data: data.update(verificationDowntimeMs=999),
    lambda data: data.update(verificationActiveSince=0),
    lambda data: data.update(episodesPer100Transitions=None),
    lambda data: data.update(episodesPer100Transitions=26),
    lambda data: data.update(successfulTransitions=0),
    lambda data: data.update(nativeBackoffFallbacks=4),
])
def test_scan_metrics_reject_inconsistent_and_secret_bearing_objects(mutation):
    incoming = report()
    incoming["metrics"] = scan_metrics()
    mutation(incoming["metrics"])
    with pytest.raises(ValueError, match="^Invalid browser diagnostics report$"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_zero_transition_metrics_use_null_rate_and_old_reports_remain_compatible(tmp_path):
    incoming = report()
    incoming["metrics"] = scan_metrics()
    incoming["metrics"].update(successfulTransitions=0, episodesPer100Transitions=None)
    path = tmp_path / "metrics.json"
    store = BrowserDiagnosticsStore(path)
    store.record_report(incoming)
    assert BrowserDiagnosticsStore(path).snapshot()["report"] == incoming
    store.record_report(report())
    assert "metrics" not in BrowserDiagnosticsStore(path).snapshot()["report"]


def card_event():
    return {
        "at": NOW, "event": "card-extraction", "lane": "keywords", "page": 2,
        "acceptedCount": 1, "rejectedCount": 1, "variantCount": 0,
        "outOfStockCount": 1, "missingValueCount": 0,
        "samples": [{"itemId": "ip-21279955832", "decision": "accepted",
                     "cardFound": True, "free": True, "outOfStock": False, "valueCents": 64999},
                    {"itemId": "ip-12345678", "decision": "out-of-stock",
                     "cardFound": True, "free": True, "outOfStock": True, "valueCents": None}],
    }


def archived_events(path):
    return [json.loads(line) for line in path.with_name("browser-diagnostic-events.jsonl")
            .read_text(encoding="utf-8").splitlines()]


def test_card_evidence_survives_snapshot_and_safe_event_archive_restart(tmp_path):
    incoming = report()
    incoming["events"] = [card_event()]
    path = tmp_path / "browser-diagnostics.json"
    BrowserDiagnosticsStore(path).record_report(incoming)

    assert BrowserDiagnosticsStore(path).snapshot()["report"] == incoming
    assert archived_events(path) == [{
        "entry_point": "browser-diagnostics", "extensionVersion": incoming["extensionVersion"],
        **card_event(),
    }]


@pytest.mark.parametrize("field,value", [
    ("itemId", "https://walmart.com/ip/12345678?token=private"),
    ("itemId", "ip-12345"), ("itemId", "ip-" + "1" * 21),
    ("itemId", "ip-１２３４５６"), ("itemId", 12345678),
    ("decision", "private error text"), ("cardFound", 1), ("cardFound", None),
    ("free", "true"), ("free", 1), ("outOfStock", "false"),
    ("valueCents", True), ("valueCents", -1), ("valueCents", 2.5),
    ("valueCents", float("inf")), ("valueCents", 1e16),
    ("url", "https://example.com/private"), ("title", "private DOM text"),
])
def test_card_sample_rejects_private_or_malformed_fields(field, value):
    incoming = report()
    incoming["events"] = [card_event()]
    incoming["events"][0]["samples"][0][field] = value
    with pytest.raises(ValueError, match="^Invalid browser diagnostics report$"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


@pytest.mark.parametrize("mutation", [
    lambda event: event.update(samples=event["samples"] * 7),
    lambda event: event.update(samples=None),
    lambda event: event["samples"][0].pop("free"),
    lambda event: event.update(lane="private-keyword"),
    lambda event: event.update(acceptedCount=True),
    lambda event: event.update(rejectedCount=-1),
    lambda event: event.update(variantCount=1.5),
    lambda event: event.update(outOfStockCount="2"),
    lambda event: event.update(missingValueCount=float("nan")),
    lambda event: event.pop("samples"),
    lambda event: event.pop("lane"),
])
def test_card_evidence_rejects_bad_shapes_counts_and_unknown_lanes(mutation):
    incoming = report()
    incoming["events"] = [card_event()]
    mutation(incoming["events"][0])
    with pytest.raises(ValueError, match="^Invalid browser diagnostics report$"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_all_fixed_card_decisions_and_nullable_evidence_are_supported():
    incoming = report()
    event = card_event()
    event["samples"] = [{"itemId": "ip-12345678", "decision": decision,
                         "cardFound": False, "free": None, "outOfStock": None, "valueCents": None}
                        for decision in ("accepted", "out-of-stock", "variant", "card-unavailable",
                                         "not-free", "missing-title")]
    incoming["events"] = [event]
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert store.snapshot()["report"] == incoming


def test_auxiliary_lane_events_keep_fixed_metadata_without_raw_failure_strings(tmp_path):
    incoming = report()
    incoming["events"] = [{"at": NOW, "event": event, "lane": lane, "tabId": 11,
                           "page": 3, "reason": "unusable"}
                          for event, lane in [("auxiliary-target-start", "departments"),
                                              ("auxiliary-page-delivered", "keywords"),
                                              ("auxiliary-target-skipped", "brands"),
                                              ("auxiliary-error", "home")]]
    path = tmp_path / "snapshot.json"
    BrowserDiagnosticsStore(path).record_report(incoming)
    assert BrowserDiagnosticsStore(path).snapshot()["report"] == incoming
    incoming["events"][0]["target"] = "private query and URL"
    with pytest.raises(ValueError, match="^Invalid browser diagnostics report$"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_auxiliary_failure_code_preserves_only_the_shared_safe_enum(tmp_path):
    incoming = report()
    incoming["events"] = [{"at": NOW, "event": event, "lane": "keywords",
                           "failureCode": "selection-render-timeout"}
                          for event in ("auxiliary-target-skipped", "auxiliary-error")]
    path = tmp_path / "snapshot.json"
    BrowserDiagnosticsStore(path).record_report(incoming)
    assert BrowserDiagnosticsStore(path).snapshot()["report"] == incoming
    for event, code in [("auxiliary-error", "private error and URL"),
                        ("auxiliary-target-start", "selection-render-timeout"),
                        ("page-loaded", "none")]:
        incoming["events"] = [{"at": NOW, "event": event, "lane": "home", "failureCode": code}]
        with pytest.raises(ValueError, match="^Invalid browser diagnostics report$"):
            BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_large_bounded_card_history_fits_one_mebibyte_snapshot(tmp_path):
    incoming = report()
    event = card_event()
    event["samples"] *= 6
    incoming["events"] = [{**deepcopy(event), "page": page} for page in range(300)]
    path = tmp_path / "snapshot.json"
    BrowserDiagnosticsStore(path).record_report(incoming)
    assert 128 * 1024 < path.stat().st_size <= 1024 * 1024
    assert BrowserDiagnosticsStore(path).snapshot()["report"] == incoming


def test_unchanged_mirrors_and_worker_restarts_do_not_repeat_archived_events(tmp_path):
    path = tmp_path / "snapshot.json"
    incoming = report()
    incoming["events"] = [card_event()]
    store = BrowserDiagnosticsStore(path)
    store.record_report(incoming)
    store.record_report(incoming)
    BrowserDiagnosticsStore(path).record_report(incoming)
    assert len(archived_events(path)) == 1
    next_report = report()
    next_report["events"] = [card_event(), {"at": NOW, "event": "page-delivered", "items": 2}]
    BrowserDiagnosticsStore(path).record_report(next_report)
    assert len(archived_events(path)) == 2
    BrowserDiagnosticsStore(path).record_report(report())
    assert archived_events(path)[0]["samples"] == card_event()["samples"], (
        "Snapshot replacement must retain prior evidence"
    )


@pytest.mark.parametrize("empty_archive", [False, True])
def test_first_mirror_archives_retained_history_without_a_populated_archive(
    tmp_path, empty_archive,
):
    path = tmp_path / "snapshot.json"
    incoming = report()
    path.write_text(json.dumps({"receivedAt": NOW, "report": incoming}), encoding="utf-8")
    if empty_archive:
        path.with_name("browser-diagnostic-events.jsonl").write_bytes(b"")
    BrowserDiagnosticsStore(path).record_report(incoming)
    assert len(archived_events(path)) == 1
    BrowserDiagnosticsStore(path).record_report(incoming)
    assert len(archived_events(path)) == 1


def test_archive_failure_cannot_acknowledge_or_replace_a_snapshot(tmp_path):
    class Archive:
        fail = False

        def append(self, encoded):
            if self.fail:
                raise OSError("archive unavailable")

    archive = Archive()
    path = tmp_path / "snapshot.json"
    store = BrowserDiagnosticsStore(path, archive=archive)
    store.record_report(report())
    before_memory, before_disk = store.snapshot(), path.read_bytes()
    incoming = report()
    incoming["events"] = [card_event()]
    archive.fail = True
    with pytest.raises(OSError, match="archive unavailable"):
        store.record_report(incoming)
    assert store.snapshot() == before_memory
    assert path.read_bytes() == before_disk


def test_snapshot_write_failure_keeps_archived_evidence_without_duplicate_retry(
    tmp_path, monkeypatch,
):
    path = tmp_path / "snapshot.json"
    store = BrowserDiagnosticsStore(path)
    store.record_report(report())
    before_memory, before_disk = store.snapshot(), path.read_bytes()
    incoming = report()
    incoming["events"] = [card_event()]
    replace = atomic_json.os.replace

    def fail_replace(source, destination):
        raise OSError("snapshot unavailable")

    monkeypatch.setattr(atomic_json.os, "replace", fail_replace)
    with pytest.raises(OSError, match="snapshot unavailable"):
        store.record_report(incoming)
    assert store.snapshot() == before_memory
    assert path.read_bytes() == before_disk
    assert len(archived_events(path)) == 2
    monkeypatch.setattr(atomic_json.os, "replace", replace)
    store.record_report(incoming)
    assert len(archived_events(path)) == 2


def test_partial_archive_append_cannot_corrupt_a_retried_diagnostic_report(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.json"
    store = BrowserDiagnosticsStore(path)
    store.record_report(report())
    before_memory, before_disk = store.snapshot(), path.read_bytes()
    before_events = archived_events(path)
    incoming = report()
    incoming["events"] = [card_event()]
    write = os.write
    calls = 0

    def short_then_fail(descriptor, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return write(descriptor, data[:16])
        raise OSError("archive append unavailable")

    monkeypatch.setattr(os, "write", short_then_fail)
    with pytest.raises(OSError, match="archive append unavailable"):
        store.record_report(incoming)
    assert store.snapshot() == before_memory
    assert path.read_bytes() == before_disk
    assert archived_events(path) == before_events
    monkeypatch.setattr(os, "write", write)
    store.record_report(incoming)
    assert store.snapshot()["report"] == incoming
    events = archived_events(path)
    for archive in path.parent.glob("browser-diagnostic-events.jsonl.*.gz"):
        events.extend(
            json.loads(line) for line in gzip.decompress(archive.read_bytes()).splitlines()
        )
    assert len(events) == 2
    assert [event for event in events if event["event"] == "card-extraction"][0]["samples"] == (
        card_event()["samples"]
    )


def test_unresolved_coverage_is_distinct_from_complete_and_survives_restart(tmp_path):
    incoming = report()
    incoming['events'] = [{'at': NOW, 'event': 'coverage-unresolved', 'page': 100, 'pages': 100}]
    path = tmp_path / 'browser-diagnostics.json'
    BrowserDiagnosticsStore(path).record_report(incoming)
    assert BrowserDiagnosticsStore(path).snapshot()['report'] == incoming
