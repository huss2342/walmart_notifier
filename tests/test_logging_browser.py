"""Browser log correlation, bounded loss accounting and durable receipts."""

import json
from copy import deepcopy

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
