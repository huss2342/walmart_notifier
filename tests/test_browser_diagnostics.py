"""Strict, private browser telemetry persists independently of item dedupe."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

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


def test_empty_memory_store_has_no_report():
    assert BrowserDiagnosticsStore(":memory:").snapshot() == {"receivedAt": 0, "report": None}


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
    assert list(path.parent.iterdir()) == [path]


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


def test_oversized_valid_metadata_is_rejected():
    incoming = report()
    fields = dict.fromkeys([
        "tabId", "generation", "page", "targetPage", "delayMs", "count", "pages",
        "durationMs", "attempt", "items", "new", "duplicates", "matched", "notified",
        "failed", "pending",
    ], 1_000_000_000_000_000)
    incoming["events"] = [{"at": NOW, "event": "page-delivered", **fields}] * 300
    with pytest.raises(ValueError):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


@pytest.mark.parametrize("contents", [
    "private invalid payload", "[]", '{"receivedAt": 1, "report": null}',
    '{"receivedAt":true,"report":{}}', "x" * (128 * 1024 + 1),
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

    monkeypatch.setattr(browser_diagnostics.os, "replace", fail_replace)
    incoming = report()
    incoming["verification"]["count"] = 2
    with pytest.raises(OSError):
        store.record_report(incoming)

    assert store.snapshot() == before_memory
    assert path.read_bytes() == before_disk
    assert list(tmp_path.iterdir()) == [path]


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
    assert list(tmp_path.iterdir()) == [path]


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
