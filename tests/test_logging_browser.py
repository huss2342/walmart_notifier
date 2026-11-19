"""Browser log correlation, bounded loss accounting and durable receipts."""

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

import browser_diagnostics as module
from browser_diagnostics import BrowserDiagnosticsStore

STREAM = "a" * 32
NOW = 1793462400000


def logging_state(sequence=1):
    return {
        "streamId": STREAM,
        "lastSequence": sequence,
        "recorded": sequence,
        "rejected": 0,
        "storageErrors": 0,
        "ringOverwrites": 0,
        "unacknowledgedOverwrites": 0,
        "pending": 0,
        "lastRecordedAt": NOW,
        "lastSyncAt": 0,
        "syncAttempts": 0,
        "syncFailures": 0,
        "consecutiveSyncFailures": 0,
        "lastSyncStatus": 0,
        "lastSyncError": "none",
        "acknowledgedSequence": 0,
    }


def report(sequences=(1,)):
    return {
        "schemaVersion": 1,
        "extensionVersion": "2.26.11",
        "exportedAt": NOW,
        "settings": {"refreshMinutes": 5, "pageDelaySeconds": 15, "sweepMode": "none"},
        "verification": {"count": 0, "lastAt": 0, "pausedUntil": 0},
        "logging": logging_state(max(sequences, default=0)),
        "events": [
            {
                "at": NOW,
                "event": "page-loaded",
                "documentId": 100,
                "lane": "home",
                "streamId": STREAM,
                "eventSeq": seq,
            }
            for seq in sequences
        ],
    }


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(module.time, "time", lambda: NOW / 1000)


def test_legacy_reports_stay_exactly_compatible():
    incoming = report()
    incoming.pop("logging")
    incoming["events"] = [{"at": NOW, "event": "page-loaded"}]
    store = BrowserDiagnosticsStore(":memory:")
    assert store.record_report(incoming) is None
    assert store.snapshot() == {"receivedAt": NOW, "report": incoming}


def test_same_timestamp_distinct_sequences_archive_separately_and_retry_dedupes(tmp_path):
    p = tmp_path / "snapshot.json"
    store = BrowserDiagnosticsStore(p)
    receipt = store.record_report(report((1, 2)))
    assert receipt == {"streamId": STREAM, "lastSequence": 2, "gapEvents": 0}
    store.record_report(report((1, 2)))
    lines = p.with_name("browser-diagnostic-events.jsonl").read_text().splitlines()
    assert [json.loads(line)["eventSeq"] for line in lines] == [1, 2]
    restarted = BrowserDiagnosticsStore(p)
    assert restarted.record_report(report((1, 2))) == receipt
    assert len(p.with_name("browser-diagnostic-events.jsonl").read_text().splitlines()) == 2


def test_missing_sequences_are_reported_once_and_survive_restart(tmp_path):
    p = tmp_path / "snapshot.json"
    store = BrowserDiagnosticsStore(p)
    store.record_report(report((1, 2)))
    incoming = report((5, 6))
    incoming["logging"]["ringOverwrites"] = 4
    incoming["logging"]["unacknowledgedOverwrites"] = 2
    receipt = store.record_report(incoming)
    assert receipt == {"streamId": STREAM, "lastSequence": 6, "gapEvents": 2}
    assert store.record_report(incoming) == receipt
    assert BrowserDiagnosticsStore(p).record_report(incoming) == receipt


def test_initial_ring_loss_is_visible_and_new_stream_starts_a_new_sequence():
    store = BrowserDiagnosticsStore(":memory:")
    assert store.record_report(report((5, 6)))["gapEvents"] == 4
    fresh = report((1,))
    fresh["logging"]["streamId"] = "b" * 32
    fresh["events"][0]["streamId"] = "b" * 32
    assert store.record_report(fresh) == {"streamId": "b" * 32, "lastSequence": 1, "gapEvents": 0}


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r["logging"].update(streamId="private-text"),
        lambda r: r["logging"].update(unknown="private-text"),
        lambda r: r["logging"].update(storageErrors=True),
        lambda r: r["logging"].update(lastSyncError="private-error"),
        lambda r: r["events"][0].update(requestId="private-text"),
        lambda r: r["events"][0].pop("streamId"),
        lambda r: r["events"][0].update(eventSeq=0),
    ],
)
def test_new_logging_contract_remains_strict_and_private(change):
    incoming = report()
    change(incoming)
    with pytest.raises(ValueError, match="Invalid browser diagnostics report"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_ingest_stage_correlation_is_optional_bounded_and_machine_readable():
    incoming = report()
    incoming["events"][0].update(
        event="ingest-stage",
        stage="complete",
        outcome="accepted-stale",
        intakePhase="late",
        requestId="c" * 32,
        queueMs=45,
        batchCount=2,
        round=4,
        targetIndex=3,
    )
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert store.snapshot()["report"] == incoming


@pytest.mark.parametrize("lane", ["newest", "value", "requests", "priority", "departments"])
def test_fast_read_lanes_and_fast_intake_phase_survive_strict_boundary(lane):
    incoming = report((1, 2))
    incoming["events"][0].update(
        event="fast-read", lane=lane, outcome="items", page=2, items=40, new=1,
        duplicates=39, durationMs=900, responseStatus=200, totalPages=9, targetIndex=3,
        count=1200, mode="high-value",
    )
    incoming["events"][1].update(
        event="ingest-stage", lane=lane, stage="queued", outcome="waiting",
        intakePhase="fast", requestId="d" * 32, page=2, items=40,
    )
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert store.snapshot()["report"] == incoming


@pytest.mark.parametrize(
    "change",
    [
        lambda event: event.update(event="fast-read", lane="home", outcome="items"),
        lambda event: event.update(event="fast-read", lane="keywords", outcome="items"),
        lambda event: event.update(intakePhase="burst"),
    ],
)
def test_fast_read_contract_still_rejects_unknown_lanes_and_phases(change):
    incoming = report()
    change(incoming["events"][0])
    with pytest.raises(ValueError, match="Invalid browser diagnostics report"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_failed_archive_cannot_advance_receipt_or_snapshot(tmp_path):
    class Archive:
        fail = False

        def append(self, _):
            if self.fail:
                raise OSError("private-error")

    archive = Archive()
    store = BrowserDiagnosticsStore(tmp_path / "snapshot.json", archive=archive)
    store.record_report(report((1,)))
    before = deepcopy(store.snapshot())
    archive.fail = True
    with pytest.raises(OSError):
        store.record_report(report((1, 2)))
    assert store.snapshot() == before
    assert store.health()["archiveFailures"] == 1


def test_future_tail_and_future_hole_are_not_acknowledged_or_counted_as_lost(tmp_path, monkeypatch):
    p = tmp_path / "snapshot.json"
    store = BrowserDiagnosticsStore(p)
    incoming = report((1, 2, 3))
    incoming["events"][1]["at"] = NOW + 2000
    receipt = store.record_report(incoming)
    assert receipt == {"streamId": STREAM, "lastSequence": 1, "gapEvents": 0}
    assert [e["eventSeq"] for e in store.snapshot()["report"]["events"]] == [1, 3]
    restarted = BrowserDiagnosticsStore(p)
    monkeypatch.setattr(module.time, "time", lambda: (NOW + 3000) / 1000)
    assert restarted.record_report(incoming) == {
        "streamId": STREAM,
        "lastSequence": 3,
        "gapEvents": 0,
    }
    events = [
        json.loads(line)
        for line in p.with_name("browser-diagnostic-events.jsonl").read_text().splitlines()
    ]
    assert sorted(e["eventSeq"] for e in events) == [1, 2, 3]


def test_expired_unarchived_sequences_are_counted_as_missing(monkeypatch):
    store = BrowserDiagnosticsStore(":memory:")
    incoming = report((1, 2))
    incoming["events"][0]["at"] = NOW - module.RETENTION_MS - 1
    assert store.record_report(incoming) == {"streamId": STREAM, "lastSequence": 2, "gapEvents": 1}


def test_snapshot_failure_keeps_archive_but_cannot_issue_a_new_receipt(tmp_path, monkeypatch):
    store = BrowserDiagnosticsStore(tmp_path / "snapshot.json")
    store.record_report(report((1,)))
    original = store._write
    monkeypatch.setattr(store, "_write", lambda _: (_ for _ in ()).throw(OSError("private-error")))
    with pytest.raises(OSError):
        store.record_report(report((1, 2)))
    assert store.snapshot()["receipt"]["lastSequence"] == 1
    assert store.health()["snapshotFailures"] == 1
    monkeypatch.setattr(store, "_write", original)
    assert store.record_report(report((1, 2)))["lastSequence"] == 2


def test_same_stream_sequence_rollback_is_rejected_without_erasing_accepted_state():
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(report((1, 2)))
    before = store.snapshot()
    with pytest.raises(ValueError):
        store.record_report(report((1,)))
    assert store.snapshot() == before


def test_verification_attempt_timing_and_fixed_error_class_survive_archive(tmp_path):
    incoming = report()
    incoming["events"][0].update(
        event="verification-state",
        outcome="input-error",
        attempt=2,
        durationMs=15100,
        resultAt=NOW,
        episodeAt=NOW - 20000,
        errorStage="move",
        errorReason="timeout",
        cleanupConfirmed=True,
    )
    store = BrowserDiagnosticsStore(tmp_path / "snapshot.json")
    store.record_report(incoming)
    assert store.snapshot()["report"]["events"] == incoming["events"]
    private = deepcopy(incoming)
    private["events"][0]["errorReason"] = "private-error"
    with pytest.raises(ValueError):
        store.record_report(private)


def scanner_state():
    return {"phase": "waiting-focus", "focusReason": "waiting-turn", "visibility": "hidden",
            "reachable": True, "active": True, "busy": False, "highTraffic": False,
            "paused": False, "enabled": True, "progressAt": NOW - 1000,
            "lastActivityAt": NOW, "nextActionAt": NOW + 5000}


@pytest.mark.parametrize("event", ["scanner-state", "scanner-recovery"])
def test_scanner_state_and_recovery_archive_typed_context(tmp_path, event):
    incoming = report()
    incoming["events"][0].update(event=event, state=scanner_state(), targetId="c" * 32)
    if event == "scanner-recovery":
        incoming["events"][0].update(reason="progress-timeout", outcome="deferred",
                                     decision="progress-advanced")
    store = BrowserDiagnosticsStore(tmp_path / "snapshot.json")
    store.record_report(incoming)
    assert store.snapshot()["report"] == incoming
    archive = tmp_path / "browser-diagnostic-events.jsonl"
    archived = json.loads(archive.read_text().splitlines()[0])
    assert archived["state"] == scanner_state()
    assert archived["targetId"] == "c" * 32


@pytest.mark.parametrize("field,value", [
    ("phase", "private-error"), ("focusReason", "private-url"), ("visibility", "secret"),
    ("busy", 1), ("reachable", None), ("progressAt", 1.5), ("nextActionAt", -1),
    ("lastActivityAt", 10**15 + 1), ("url", "private-url"), ("targetKey", "private-query"),
])
def test_scanner_state_rejects_untyped_or_extra_nested_fields(field, value):
    incoming = report()
    incoming["events"][0].update(event="scanner-state", state={**scanner_state(), field: value})
    with pytest.raises(ValueError):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


@pytest.mark.parametrize("change", [
    {"reason": "private-error"}, {"outcome": "private-response"}, {"decision": "private-text"},
    {"targetId": "invalid"}, {"targetId": "A" * 32}, {"targetKey": "private-query"},
    {"lane": "private-lane"}, {"state": {}},
])
def test_scanner_recovery_rejects_invalid_labels_and_raw_keys(change):
    incoming = report()
    incoming["events"][0].update({"event": "scanner-recovery", "state": scanner_state(),
                                 "reason": "progress-timeout", "outcome": "accepted", **change})
    with pytest.raises(ValueError):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_scanner_recovery_requires_labels_and_lane_but_state_cannot_gain_decision():
    for removed in ("reason", "outcome", "lane"):
        incoming = report()
        incoming["events"][0].update(event="scanner-recovery", state=scanner_state(),
                                     reason="progress-timeout", outcome="accepted")
        incoming["events"][0].pop(removed)
        with pytest.raises(ValueError):
            BrowserDiagnosticsStore(":memory:").record_report(incoming)
    incoming = report()
    incoming["events"][0].update(event="scanner-state", state=scanner_state(), decision="none")
    with pytest.raises(ValueError):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_real_javascript_scanner_event_projection_matches_python_contract():
    incoming = report()
    event = incoming["events"][0]
    event.update(event="scanner-recovery", reason="passive-rebind", outcome="accepted",
                 decision="none", state=scanner_state(), targetId="b" * 32)
    event["url"] = "private-url"
    event["state"]["token"] = "private-token"
    script = """
const fs = require('node:fs'), vm = require('node:vm');
const c = vm.createContext({ Date });
vm.runInContext(fs.readFileSync('extension/diagnostics.js', 'utf8') +
  ';this.clean = cleanDiagnostic;this.schema = SCANNER_STATE_LABELS;', c);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const output = { event: c.clean(input.event, input, input.at), schema: c.schema };
process.stdout.write(JSON.stringify(output));
"""
    result = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1],
                            input=json.dumps(event), capture_output=True, text=True, check=True)
    output = json.loads(result.stdout)
    incoming["events"] = [output["event"]]
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert store.snapshot()["report"] == incoming
    assert {field: set(choices) for field, choices in output["schema"].items()} == {
        field: set(choices) for field, choices in module._SCANNER_STATE_LABELS.items()
    }
    assert "private" not in json.dumps(output)


def test_search_reads_keep_query_hash_hot_flag_and_request_id():
    incoming = report((1,))
    incoming["events"][0].update(
        event="fast-read", lane="requests", outcome="items", page=1, items=4, count=1,
        targetIndex=3, searchHash=3_405_691_582, hot=True, requestId="c" * 32,
        durationMs=900, responseStatus=200, totalPages=1,
    )
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert store.snapshot()["report"] == incoming


FAST_SEARCH_READ = {"event": "fast-read", "lane": "priority", "outcome": "items", "page": 1,
                    "searchHash": 7, "hot": True}
INGEST_QUEUED = {"event": "ingest-stage", "lane": "requests", "stage": "queued",
                 "outcome": "waiting", "intakePhase": "fast", "requestId": "d" * 32, "page": 1,
                 "items": 4}


@pytest.mark.parametrize(
    ("valid", "bad"),
    [
        (FAST_SEARCH_READ, {"hot": False}),
        (FAST_SEARCH_READ, {"hot": "yes"}),
        (FAST_SEARCH_READ, {"hot": 1}),
        (FAST_SEARCH_READ, {"searchHash": -1}),
        (FAST_SEARCH_READ, {"searchHash": "a1b2c3d4"}),
        (INGEST_QUEUED, {"hot": True}),
    ],
)
def test_hot_flag_and_search_hash_are_strict(valid, bad):
    incoming = report((1,))
    incoming["events"][0].update(valid)
    BrowserDiagnosticsStore(":memory:").record_report(deepcopy(incoming))
    incoming["events"][0].update(bad)
    with pytest.raises(ValueError, match="Invalid browser diagnostics report"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_research_reads_are_logged_as_their_own_fast_read_lane():
    incoming = report((1,))
    incoming["events"][0].update(
        event="fast-read", lane="research", outcome="items", page=1, items=40,
        durationMs=900, responseStatus=200, totalPages=25,
    )
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert store.snapshot()["report"] == incoming


def restock_event(**changes):
    event = {"at": NOW, "event": "restock-watch", "itemId": "ip-20755219869", "stage": "returned",
             "stockState": "in-stock", "stockSource": "data", "signalV2": "in",
             "signalStatus": "in", "signalOos": False, "variant": True, "conflict": False,
             "valueCents": 19999, "awayMs": 240000, "sinceOutMs": 60000, "outReads": 2,
             "lane": "value80", "page": 2, "requestId": "d" * 32, "streamId": STREAM, "eventSeq": 1}
    event.update(changes)
    return event


def test_restock_watch_and_read_stock_summary_survive_the_strict_boundary():
    incoming = report((1, 2))
    incoming["events"][0] = restock_event()
    incoming["events"][1].update(
        event="fast-read", lane="value80", outcome="items", page=2, items=41, new=0, duplicates=31,
        durationMs=1881, responseStatus=200, totalPages=8, count=20, stockIn=31, stockOut=10,
        stockOther=0, bytes=1203425, ageSeconds=37, products=41,
    )
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert store.snapshot()["report"] == incoming


@pytest.mark.parametrize(
    "change",
    [
        {"itemId": "20755219869"},
        {"itemId": "ip-1"},
        {"stage": "armed"},
        {"stockState": "gone"},
        {"signalV2": "IN_STOCK"},
        {"signalStatus": "yes"},
        {"stockSource": "page"},
        {"conflict": "false"},
        {"variant": 1},
        {"signalOos": None},
        {"title": "Mitonos weights"},
        {"awayMs": -1},
    ],
)
def test_restock_watch_rejects_free_text_and_unknown_labels(change):
    incoming = report((1,))
    incoming["events"][0] = restock_event(**change)
    with pytest.raises(ValueError, match="Invalid browser diagnostics report"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


def test_restock_fields_are_refused_on_other_events():
    incoming = report((1,))
    incoming["events"][0]["itemId"] = "ip-20755219869"
    with pytest.raises(ValueError, match="Invalid browser diagnostics report"):
        BrowserDiagnosticsStore(":memory:").record_report(incoming)


@pytest.mark.parametrize("stage, state", [("returned", "in-stock"), ("confirmed", "in-stock"),
                                          ("rejected", "out-of-stock"), ("rejected", "absent"),
                                          ("followup", "absent")])
def test_restock_hold_and_confirmation_stages_survive_the_strict_boundary(stage, state):
    incoming = report((1,))
    incoming["events"][0] = restock_event(stage=stage, stockState=state, sinceReturnMs=10600)
    store = BrowserDiagnosticsStore(":memory:")
    store.record_report(incoming)
    assert store.snapshot()["report"] == incoming
