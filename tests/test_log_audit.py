"""Bounded, read-only diagnostics summaries must not echo historical secrets."""

import gzip
import json
import sqlite3

import pytest
from tools import log_audit

import browser_diagnostics

NOW = 1_793_500_000
REQUEST = "a" * 32


def test_browser_reader_covers_the_released_event_and_metadata_contract():
    assert browser_diagnostics._EVENTS <= log_audit.EVENTS
    assert log_audit.LABELS["lane"] >= browser_diagnostics._LANES
    assert (
        log_audit.NUMBERS | log_audit.DURATIONS.keys()
    ) >= browser_diagnostics._NUMERIC_FIELDS | browser_diagnostics._CARD_COUNTS
    assert log_audit.LABELS["reason"] >= browser_diagnostics._REASONS
    assert log_audit.LABELS["mode"] >= browser_diagnostics._MODES
    assert log_audit.LABELS["stage"] >= browser_diagnostics._PAGE_STATE_STAGES
    assert log_audit.LABELS["outcome"] >= browser_diagnostics._VERIFICATION_OUTCOMES
    for field, choices in browser_diagnostics._VERIFICATION_LABELS.items():
        assert choices <= log_audit.LABELS[field]


def test_priority_lane_jsonl_trace_preserves_lane_and_drops_private_data(tmp_path):
    event = {
        "at": NOW * 1000,
        "event": "auxiliary-target-start",
        "lane": "priority",
        "requestId": REQUEST,
        "round": 1,
        "targetIndex": 0,
        "query": "never-echo-private-query",
        "url": "never-echo-private-url",
    }
    write_events(tmp_path / "browser-diagnostic-events.jsonl", [event])
    result = audit(tmp_path, request_id=REQUEST, trace=True)
    assert result["events"] == 1
    assert result["trace"][0]["lane"] == "priority"
    assert "never-echo" not in json.dumps(result)


@pytest.mark.parametrize(
    "metadata",
    [
        {
            "event": "ingest-stage",
            "stage": "complete",
            "outcome": "accepted-stale",
            "documentId": 123,
            "generation": 7,
            "tabId": 8,
            "queueMs": 40,
            "batchIndex": 2,
            "batchCount": 3,
            "intakePhase": "late",
        },
        {
            "event": "verification-state",
            "outcome": "input-error",
            "statusCode": "input-error",
            "errorStage": "move",
            "errorReason": "timeout",
            "releaseReason": "deadline",
            "cleanupConfirmed": False,
            "transport": "debugger",
            "resultAt": NOW * 1000,
            "episodeAt": NOW * 1000 - 1000,
            "attempt": 2,
        },
        {
            "event": "verification-state",
            "outcome": "status",
            "statusCode": "native-waiting-idle",
            "cleanupConfirmed": True,
            "transport": "native",
        },
        {
            "event": "auxiliary-target-complete",
            "outcome": "unresolved",
            "lane": "keywords",
            "round": 2,
            "targetIndex": 10,
            "pages": 5,
        },
        {"event": "browser-error", "component": "initialization", "outcome": "error"},
        {"event": "sync-state", "component": "diagnostics", "outcome": "recovered"},
        {"event": "navigation-outcome", "outcome": "http-error", "responseStatus": 520},
    ],
)
def test_released_browser_events_retain_safe_diagnostic_meaning(metadata):
    row = browser_diagnostics._event({"at": NOW * 1000, **metadata})
    clean, recognized = log_audit._safe_event({**row, "private": "never-echo-this"}, "browser")
    assert recognized is True
    for key, expected in metadata.items():
        assert clean[key] == expected
    assert "never-echo-this" not in json.dumps(clean)


def test_browser_unknown_labels_and_boolean_lookalikes_are_not_echoed():
    clean, _ = log_audit._safe_event(
        {
            "at": NOW * 1000,
            "event": "verification-state",
            "outcome": "private-outcome",
            "errorStage": "private-stage",
            "errorReason": "private-reason",
            "cleanupConfirmed": "false",
            "documentId": True,
        },
        "browser",
    )
    assert clean["outcome"] == clean["errorStage"] == clean["errorReason"] == "other"
    assert "cleanupConfirmed" not in clean
    assert "documentId" not in clean
    assert "private" not in json.dumps(clean)


@pytest.mark.parametrize("field", ["http_request_id", "requestId"])
def test_watch_id_cannot_shadow_an_explicit_http_identity(tmp_path, field):
    write_events(
        tmp_path / "discord_bridge/requests.jsonl",
        [{"timestamp": NOW, "event": "request_changed", "request_id": 12, field: REQUEST}],
    )
    result = audit(tmp_path, request_id=REQUEST, trace=True)
    assert result["events"] == 1
    assert result["trace"][0]["http_request_id"] == REQUEST
    assert "request_id" not in result["trace"][0]


def test_combined_filters_require_an_observed_request_item_link(tmp_path, monkeypatch):
    write_events(
        tmp_path / "operations.jsonl",
        [{"timestamp": NOW, "event": "http_request", "request_id": REQUEST}],
    )
    write_events(
        tmp_path / "discord_bridge/delivery.jsonl",
        [{"timestamp": NOW, "event": "dispatch_sent", "item_ids": ["ip-12345678"]}],
    )

    def must_not_query(*_args, **_kwargs):
        pytest.fail("No ledger query is authorized by unassociated filter values")

    monkeypatch.setattr(log_audit, "_ledger", must_not_query)
    result = audit(tmp_path, request_id=REQUEST, item_id="ip-12345678", trace=True, ledger=True)
    assert result["events"] == 0
    assert result["trace"] == []
    assert result["ledger"]["available"] is False
    assert result["relationships"]["item_ids"] == 0


def test_combined_filters_keep_an_observed_item_request_link_and_its_exact_job(tmp_path):
    target, other, job, other_job = "ip-12345678", "ip-87654321", "1" * 24, "2" * 24
    write_events(
        tmp_path / "operations.jsonl",
        [{"timestamp": NOW, "event": "http_request", "request_id": REQUEST}],
    )
    write_events(
        tmp_path / "discord_bridge/delivery.jsonl",
        [
            {
                "timestamp": NOW,
                "event": "observation_routed",
                "http_request_id": REQUEST,
                "observation_item_ids": [target, other],
                "observation_job_ids": [job, other_job],
            },
            {
                "timestamp": NOW + 1,
                "event": "dispatch_sent",
                "item_ids": [target],
                "job_ids": [job],
            },
            {
                "timestamp": NOW + 1,
                "event": "dispatch_sent",
                "item_ids": [other],
                "job_ids": [other_job],
            },
        ],
    )
    result = audit(tmp_path, item_id=target, request_id=REQUEST, trace=True)
    assert result["events"] == 3
    assert result["relationships"]["item_ids"] == result["relationships"]["job_ids"] == 1
    assert other_job not in json.dumps(result)


def write_events(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in events), encoding="utf-8")


def audit(tmp_path, **options):
    return log_audit.audit(tmp_path, since=NOW - 60, until=NOW + 60, **options)


def test_summary_reads_compressed_history_and_deduplicates_repeated_browser_events(tmp_path):
    event = {
        "at": NOW * 1000,
        "event": "traffic-resumed",
        "durationMs": 2000,
        "requestId": REQUEST,
        "secret": "private-account-text",
    }
    write_events(tmp_path / "browser-diagnostic-events.jsonl", [event])
    archive = tmp_path / "browser-diagnostic-events.jsonl.20261031T120000000000-12345678.gz"
    archive.write_bytes(gzip.compress((json.dumps(event) + "\n").encode()))
    result = audit(tmp_path)
    assert result["events"] == 1
    assert result["quality"]["duplicate_rows"] == 1
    assert result["durations_seconds"]["browser:traffic-resumed:durationMs"] == {
        "samples": 1,
        "p50": 2,
        "p95": 2,
        "p99": 2,
        "max": 2,
    }
    assert "private-account-text" not in json.dumps(result)


def test_request_trace_joins_only_http_request_identifiers_not_watch_ids(tmp_path):
    write_events(
        tmp_path / "operations.jsonl",
        [
            {
                "timestamp": NOW,
                "event": "http-complete",
                "request_id": REQUEST,
                "route": "/ingest",
                "duration_ms": 20,
                "url": "secret-token",
            }
        ],
    )
    write_events(
        tmp_path / "discord_bridge" / "delivery.jsonl",
        [
            {
                "timestamp": NOW + 1,
                "event": "intake",
                "request_id": 12,
                "http_request_id": REQUEST,
                "user_id": "private-member",
            },
            {"timestamp": NOW + 2, "event": "request_changed", "request_id": 12},
        ],
    )
    result = audit(tmp_path, request_id=REQUEST, trace=True)
    assert result["events"] == 2
    assert len(result["trace"]) == 2
    assert {event["source"] for event in result["trace"]} == {"system", "delivery"}
    assert "secret-token" not in json.dumps(result)
    assert "private-member" not in json.dumps(result)


def test_item_filter_matches_browser_samples_and_delivery_item_lists(tmp_path):
    write_events(
        tmp_path / "browser-diagnostic-events.jsonl",
        [{"at": NOW * 1000, "event": "card-extraction", "samples": [{"itemId": "ip-12345678"}]}],
    )
    write_events(
        tmp_path / "discord_bridge" / "delivery.jsonl",
        [
            {"timestamp": NOW, "event": "dispatch_sent", "item_ids": ["ip-12345678"]},
            {"timestamp": NOW, "event": "dispatch_sent", "item_ids": ["ip-87654321"]},
        ],
    )
    assert audit(tmp_path, item_id="ip-12345678")["events"] == 2


def test_item_trace_includes_batched_routing_receipt_without_printing_member_ids(tmp_path):
    write_events(
        tmp_path / "discord_bridge" / "delivery.jsonl",
        [
            {
                "timestamp": NOW,
                "event": "observation_routed",
                "route": "pro-all",
                "observation_item_ids": ["ip-12345678"],
                "observation_job_ids": ["a" * 24],
                "transition": "queued",
                "user_id": "private-member",
            }
        ],
    )
    result = audit(tmp_path, item_id="ip-12345678", trace=True)
    assert result["events"] == 1
    assert result["trace"][0]["transition"] == "queued"
    assert "private-member" not in json.dumps(result)


def test_timestamp_units_and_exact_window_are_source_specific(tmp_path):
    write_events(
        tmp_path / "native-verification" / "host.log",
        [
            {"at": NOW, "op": "status", "ok": True, "holdMs": 4000},
            {"at": NOW - 120, "op": "status", "ok": True},
        ],
    )
    write_events(
        tmp_path / "browser-diagnostic-events.jsonl", [{"at": NOW * 1000, "event": "page-loaded"}]
    )
    result = audit(tmp_path)
    assert result["events"] == 2
    assert result["counts"]["native:native-status"] == 1


def test_malformed_unknown_oversized_partial_lines_are_visible_without_raw_output(tmp_path):
    path = tmp_path / "operations.jsonl"
    path.write_bytes(
        b"{bad-secret}\n[]\n"
        + b"X" * 200
        + b"\n"
        + json.dumps({"timestamp": NOW, "event": "http-complete"}).encode()
    )
    result = audit(tmp_path, max_line_bytes=100)
    assert result["events"] == 0
    assert result["quality"]["malformed_rows"] == 1
    assert result["quality"]["unknown_rows"] == 1
    assert result["quality"]["oversized_lines"] == 1
    assert result["quality"]["partial_lines"] == 1
    assert "bad-secret" not in json.dumps(result)


def test_corrupt_gzip_and_missing_sources_do_not_become_clean_zeroes(tmp_path):
    path = tmp_path / "operations.jsonl.20261031T120000000000-12345678.gz"
    path.write_bytes(b"not-a-gzip-private-error")
    result = audit(tmp_path)
    assert result["quality"]["read_errors"] == 1
    assert result["sources"]["system"]["available"] is True
    assert result["sources"]["browser"]["available"] is False


def test_limits_are_hard_and_report_incomplete_scan(tmp_path):
    write_events(
        tmp_path / "operations.jsonl",
        [{"timestamp": NOW + i, "event": "http-complete"} for i in range(10)],
    )
    result = audit(tmp_path, max_events=2, max_output=1, trace=True)
    assert result["events"] == 2
    assert len(result["trace"]) == 1
    assert result["quality"]["scan_truncated"] is True
    assert result["quality"]["output_truncated"] is True


def test_byte_budget_bounds_decompression(tmp_path):
    path = tmp_path / "operations.jsonl.20261031T120000000000-12345678.gz"
    path.write_bytes(gzip.compress(b"X" * 10000))
    result = audit(tmp_path, max_bytes=128, max_line_bytes=100)
    assert result["quality"]["bytes_read"] <= 128
    assert result["quality"]["scan_truncated"] is True


def test_byte_budget_also_bounds_gzip_headers_before_decompression(tmp_path):
    path = tmp_path / "operations.jsonl.20261031T120000000000-12345678.gz"
    path.write_bytes(b"\x1f\x8b\x08\x08\x00\x00\x00\x00\x00\xff" + b"private" * 10000)
    result = audit(tmp_path, max_bytes=128)
    assert result["quality"]["compressed_bytes_read"] <= 128
    assert result["quality"]["scan_truncated"] is True
    assert "private" not in json.dumps(result)


def test_native_and_recovery_lifecycle_json_is_normalized(tmp_path):
    write_events(
        tmp_path / "browser-recovery.log",
        [
            {
                "timestamp": NOW,
                "event": "healthy",
                "duration_ms": 20,
                "profile": "private-profile",
                "run_id": REQUEST,
            }
        ],
    )
    result = audit(tmp_path, trace=True)
    assert result["trace"][0]["event"] == "healthy"
    assert "private-profile" not in json.dumps(result)


def test_snapshot_loss_counters_are_scoped_cumulative_and_redacted(tmp_path):
    (tmp_path / "browser-diagnostics.json").write_text(
        json.dumps(
            {
                "report": {
                    "logging": {
                        "streamId": REQUEST,
                        "recorded": 12,
                        "unacknowledgedOverwrites": 2,
                        "syncFailures": 3,
                        "lastSyncError": "http",
                    },
                    "settings": {"queries": ["private-search"]},
                }
            }
        ),
        encoding="utf-8",
    )
    result = audit(tmp_path)
    assert result["browser_logging"]["available"] is True
    assert result["browser_logging"]["counters"]["unacknowledgedOverwrites"] == 2
    assert "cumulative" in result["browser_logging"]["scope"]
    assert "private-search" not in json.dumps(result)


def test_latest_trace_is_selected_across_streams_before_sorting(tmp_path):
    write_events(tmp_path / "operations.jsonl", [{"timestamp": NOW + 10, "event": "http_request"}])
    write_events(
        tmp_path / "browser-diagnostic-events.jsonl", [{"at": NOW * 1000, "event": "page-loaded"}]
    )
    result = audit(tmp_path, trace=True, max_output=1)
    assert result["trace"][0]["source"] == "system"
    assert result["quality"]["output_truncated"] is True


def test_untrusted_field_types_do_not_break_the_reader(tmp_path):
    write_events(
        tmp_path / "operations.jsonl",
        [
            {"timestamp": NOW, "event": {"secret": "never-print-this"}},
            {"timestamp": NOW, "event": "http_request", "duration_ms": 10**200},
        ],
    )
    result = audit(tmp_path)
    assert result["quality"]["unknown_rows"] == 1
    assert result["events"] == 2
    assert "never-print-this" not in json.dumps(result)


def test_legacy_recovery_text_is_counted_as_unsupported_instead_of_echoed(tmp_path):
    (tmp_path / "browser-recovery.log").write_text(
        "2026-10-31 12:00:00 INFO chrome-profile-selected profile=private\n", encoding="utf-8"
    )
    result = audit(tmp_path)
    assert result["quality"]["unknown_format_rows"] == 1
    assert result["sources"]["recovery"]["issues"]["unknown_format_rows"] == 1
    assert "private" not in json.dumps(result)


def test_request_trace_expands_to_exact_async_jobs_without_other_same_item_routes(tmp_path):
    job, other_job, dispatch = "1" * 24, "2" * 24, "3" * 24
    write_events(
        tmp_path / "operations.jsonl",
        [{"timestamp": NOW, "event": "http_request_started", "request_id": REQUEST}],
    )
    write_events(
        tmp_path / "discord_bridge/delivery.jsonl",
        [
            {
                "timestamp": NOW,
                "event": "observation_routed",
                "http_request_id": REQUEST,
                "observation_item_ids": ["ip-12345678"],
                "observation_job_ids": [job],
            },
            {
                "timestamp": NOW + 1,
                "event": "dispatch_sent",
                "job_ids": [job],
                "item_ids": ["ip-12345678"],
                "dispatch_id": dispatch,
            },
            {
                "timestamp": NOW + 2,
                "event": "dispatch_sent",
                "job_ids": [other_job],
                "item_ids": ["ip-12345678"],
            },
        ],
    )
    result = audit(tmp_path, request_id=REQUEST, trace=True)
    assert result["events"] == 3
    assert any(row.get("dispatch_id") == dispatch for row in result["trace"])
    assert result["relationships"]["physical_passes"] == 1
    assert result["relationships"]["job_ids"] == 1


def test_item_trace_keeps_shared_http_context_without_expanding_other_batch_items(tmp_path):
    target, other, job, other_job = "ip-12345678", "ip-87654321", "1" * 24, "2" * 24
    write_events(
        tmp_path / "operations.jsonl",
        [
            {"timestamp": NOW, "event": "http_request", "request_id": REQUEST},
            {"timestamp": NOW, "event": "pipeline_item", "request_id": REQUEST, "item_id": other},
        ],
    )
    write_events(
        tmp_path / "discord_bridge/delivery.jsonl",
        [
            {
                "timestamp": NOW,
                "event": "observation_routed",
                "http_request_id": REQUEST,
                "observation_item_ids": [target, other],
                "observation_job_ids": [job, other_job],
            },
            {
                "timestamp": NOW + 1,
                "event": "dispatch_sent",
                "item_ids": [target],
                "job_ids": [job],
            },
            {
                "timestamp": NOW + 1,
                "event": "dispatch_sent",
                "item_ids": [other],
                "job_ids": [other_job],
            },
        ],
    )
    result = audit(tmp_path, item_id=target, trace=True)
    assert result["events"] == 3
    assert result["relationships"]["job_ids"] == 1
    assert other_job not in json.dumps(result)
    assert sum(row.get("trace_context") is True for row in result["trace"]) == 1


def test_stable_browser_identity_ignores_envelope_changes_and_reports_conflicts(tmp_path):
    row = {"at": NOW * 1000, "event": "page-loaded", "streamId": "f" * 32, "eventSeq": 1}
    write_events(
        tmp_path / "browser-diagnostic-events.jsonl",
        [
            {**row, "extensionVersion": "old"},
            {**row, "extensionVersion": "new"},
            {**row, "page": 2},
        ],
    )
    result = audit(tmp_path)
    assert result["events"] == 1
    assert result["quality"]["duplicate_rows"] == 1
    assert result["quality"]["identity_conflicts"] == 1


def test_relationship_limits_are_explicit_and_request_ledger_is_not_unfiltered(tmp_path):
    write_events(
        tmp_path / "operations.jsonl",
        [{"timestamp": NOW, "event": "http_request", "request_id": REQUEST}],
    )
    result = audit(tmp_path, request_id=REQUEST, ledger=True)
    assert result["ledger"]["available"] is False
    assert result["ledger"]["scope"] == "request-linked identifiers unavailable; ledger not queried"
    write_events(
        tmp_path / "discord_bridge/delivery.jsonl",
        [
            {
                "timestamp": NOW,
                "event": "observation_routed",
                "http_request_id": REQUEST,
                "observation_item_ids": ["ip-12345678", "ip-87654321"],
                "observation_job_ids": ["1" * 24, "2" * 24],
            }
        ],
    )
    assert (
        audit(tmp_path, request_id=REQUEST, max_related=1)["quality"]["relationships_truncated"]
        is True
    )
    assert (
        audit(tmp_path, request_id=REQUEST, max_candidates=1)["quality"]["relationships_truncated"]
        is True
    )


def test_request_ledger_filters_exact_related_job_ids_not_the_whole_window(tmp_path):
    job, other_job = "1" * 24, "2" * 24
    write_events(
        tmp_path / "discord_bridge/delivery.jsonl",
        [
            {
                "timestamp": NOW,
                "event": "observation_routed",
                "http_request_id": REQUEST,
                "observation_item_ids": ["ip-12345678"],
                "observation_job_ids": [job],
            }
        ],
    )
    path = tmp_path / "discord_bridge/collector.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE jobs(id TEXT,item_id TEXT,route TEXT,state TEXT,due REAL,sent_at REAL)"
        )
        db.executemany(
            "INSERT INTO jobs VALUES(?,'ip-12345678','pro-all','sent',?,?)",
            [(job, NOW, NOW + 2), (other_job, NOW, NOW + 30)],
        )
    result = audit(tmp_path, request_id=REQUEST, ledger=True)
    assert result["ledger"]["scope"] == "exact job IDs linked by routing receipts"
    assert result["ledger"]["counts"] == {"pro-all:sent": 1}
    assert result["ledger"]["sent_latency_seconds"]["pro-all"]["p99"] == 2


def test_sqlite_is_opt_in_read_only_metadata_without_payloads(tmp_path):
    path = tmp_path / "discord_bridge" / "collector.sqlite3"
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE jobs(item_id TEXT,route TEXT,state TEXT,due REAL,sent_at REAL)")
        db.execute("CREATE TABLE personal_requests(keyword TEXT)")
        db.execute("INSERT INTO personal_requests VALUES('private-watch-phrase')")
        db.execute("INSERT INTO jobs VALUES('ip-12345678','pro-all','sent',?,?)", (NOW, NOW + 2))
    before = path.read_bytes()
    assert "ledger" not in audit(tmp_path)
    result = audit(tmp_path, ledger=True, item_id="ip-12345678")
    assert result["ledger"]["available"] is True
    assert result["ledger"]["counts"] == {"pro-all:sent": 1}
    assert result["ledger"]["sent_latency_seconds"]["pro-all"]["p95"] == 2
    assert "private-watch-phrase" not in json.dumps(result)
    assert path.read_bytes() == before
    assert not path.with_name(path.name + "-journal").exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"item_id": "private"},
        {"request_id": "12"},
        {"max_events": 0},
        {"since": NOW + 100, "until": NOW},
    ],
)
def test_invalid_filters_or_limits_fail_without_echoing_values(tmp_path, kwargs):
    with pytest.raises(ValueError, match="Invalid audit options"):
        log_audit.audit(tmp_path, **kwargs)
