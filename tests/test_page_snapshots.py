"""Page snapshot log (extension 2.32.7 freshness study): validation, storage, route."""

from __future__ import annotations

import gzip
import http.client
import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import page_snapshots  # noqa: E402
import server  # noqa: E402
from page_snapshots import PageSnapshotLog, parse_batch, validate_record  # noqa: E402

READ = {
    "kind": "read", "source": "fast", "at": 1_795_000_000_000, "durationMs": 812,
    "lane": "value80", "page": 2, "outcome": "items", "sort": "price_high", "minPrice": 79,
    "status": 200, "totalPages": 8, "products": 41, "ageSeconds": 0,
    "items": [["21155107170", 1, 14_999, "i", {"c": "0:4044:103150"}], ["1234567", 2, None, "o"]],
}
SEARCH = {
    "kind": "read", "source": "fast", "at": 1_795_000_000_000, "lane": "requests", "page": 1,
    "outcome": "items", "sort": "new", "searchHash": 2332232888, "hot": True, "targetIndex": 3,
    "items": [["21500000001", 1, 15_000, "i", {"c": "0:4044", "d": {"releaseDate": "2026-11-18"}}]],
}
TAB = {
    "kind": "read", "source": "tab", "at": 1_795_000_000_000, "lane": "departments", "page": 4,
    "outcome": "items", "catId": "4044_103150", "intakePhase": "late", "items": [],
}
START = {
    "kind": "probe", "event": "start", "probe": "0a1b2c3d0001", "itemId": "ip-21500000001",
    "valueCents": 15_000, "at": 1_795_000_000_100, "startedAt": 1_795_000_000_000,
    "trigger": {"lane": "requests", "page": 1, "searchHash": 2332232888, "position": 1},
    "predictedPage": 2, "pages": [2, 3], "plannedReads": 5, "category": "0:4044:103150",
    "department": "4044_103150",
}
END = {
    "kind": "probe", "event": "end", "probe": "0a1b2c3d0001", "itemId": "ip-21500000001",
    "valueCents": 15_000, "at": 1_795_000_300_000, "startedAt": 1_795_000_000_000,
    "outcome": "appeared", "lagMs": 230_000, "extraReads": 4, "droppedReads": 0, "pages": [2],
    "first": {"value80": {"at": 1_795_000_230_000, "page": 2, "position": 6},
              "new80": {"at": 1_795_000_010_000, "page": 1, "position": 3, "extra": True}},
    "checks": [{"shape": "department", "page": 1, "at": 1_795_000_012_000, "found": False,
                "extra": True, "catId": "4044_103150"}],
}
SKIPPED = {
    "kind": "probe", "event": "skipped", "itemId": "ip-21500000003", "valueCents": 17_000,
    "at": 1_795_000_000_100, "reason": "active",
    "trigger": {"lane": "priority", "page": 1, "searchHash": 7, "hot": True},
}


@pytest.mark.parametrize("record", [READ, SEARCH, TAB, START, END, SKIPPED])
def test_valid_records_pass_unchanged(record):
    assert validate_record(json.loads(json.dumps(record))) == record


@pytest.mark.parametrize("change", [
    {"q": "desk lamp"},                      # never query text
    {"query": "desk"},
    {"url": "https://www.walmart.com/reviews/claim-product?q=desk"},
    {"memberId": "123456789012345678"},      # never a member's Discord id
    {"lane": "research"},
    {"source": "page"},
    {"outcome": "ok"},
    {"searchHash": -1},
    {"searchHash": 2**32},
    {"hot": False},
    {"catId": "4044;1"},
    {"page": True},
    {"status": 42},
    {"sort": "price high"},
    {"sort": "x" * 25},
    {"items": [["ip-21155107170", 1, 100, "i"]]},
    {"items": [["21155107170", 1, 100, "in"]]},
    {"items": [["21155107170", 1, 1.5, "i"]]},
    {"items": [["21155107170", 1, 100, "i", {"title": "Sofa"}]]},
    {"items": [["21155107170", 1, 100, "i", {"d": {"releaseDate": "<script>"}}]]},
    {"items": [["21155107170", 1, 100, "i"]] * 201},
])
def test_anything_outside_the_allowlist_drops_the_record(change):
    with pytest.raises(ValueError):
        validate_record({**READ, **change})


@pytest.mark.parametrize("record,change", [
    (START, {"trigger": {"lane": "requests", "page": 1, "query": "desk"}}),
    (START, {"trigger": {"lane": "value80", "page": 1}}),
    (START, {"outcome": "appeared"}),
    (END, {"outcome": "done"}),
    (END, {"first": {"search": {"at": 1, "page": 1}}}),
    (END, {"checks": [{"shape": "value80", "page": 1, "at": 1, "found": 1}]}),
    (END, {"probe": "XYZ"}),
    (SKIPPED, {"reason": "budget"}),
    (SKIPPED, {"probe": "0a1b2c3d0001"}),
    (START, {"itemId": "21500000001"}),
])
def test_probe_records_are_checked_per_event(record, change):
    with pytest.raises(ValueError):
        validate_record({**record, **change})


def test_a_batch_keeps_the_good_records_and_counts_the_rest():
    body = json.dumps({"schemaVersion": 1, "dropped": 3,
                       "records": [READ, {**READ, "q": "sofa"}, START, "x"]}).encode()
    records, rejected, dropped = parse_batch(body)
    assert records == [READ, START]
    assert (rejected, dropped) == (2, 3)
    for bad in (b"[]", b'{"schemaVersion": 2, "records": []}', b'{"schemaVersion": 1}',
                b'{"schemaVersion": 1, "records": [], "extra": 1}', b"not json",
                json.dumps({"schemaVersion": 1, "records": [READ] * 501}).encode()):
        with pytest.raises(ValueError):
            parse_batch(bad)


def test_the_log_writes_one_line_per_record_and_a_gap_line(tmp_path):
    log = PageSnapshotLog(tmp_path, clock=lambda: 1_795_000_001.5, background=False)
    assert log.record([READ, END], rejected=1, dropped=2)
    saved = (tmp_path / "page-snapshots.jsonl").read_text().splitlines()
    lines = [json.loads(line) for line in saved]
    assert lines[0] == {"kind": "gap", "receivedAt": 1_795_000_001_500, "dropped": 2, "rejected": 1}
    assert lines[1] == {"receivedAt": 1_795_000_001_500, **READ}
    assert lines[2]["kind"] == "probe" and lines[2]["outcome"] == "appeared"
    status = log.status()
    assert (status["accepted"], status["rejected"], status["dropped_by_extension"]) == (2, 1, 2)
    assert status["total_max_bytes"] == 200 * 1024 * 1024


def test_the_log_rotates_into_gzip_segments_within_its_total(tmp_path):
    log = PageSnapshotLog(tmp_path, segment_bytes=4096, total_bytes=16_384, background=False)
    for _ in range(60):
        assert log.record([READ] * 5)
    archives = sorted(tmp_path.glob("page-snapshots.jsonl.*.gz"))
    assert archives
    total = sum(path.stat().st_size for path in archives) + 4096
    assert total <= 16_384
    with gzip.open(archives[-1], "rt") as handle:
        assert json.loads(handle.readline())["lane"] == "value80"
    assert page_snapshots.TOTAL_BYTES == 200 * 1024 * 1024


def test_without_a_directory_nothing_is_written_and_failures_are_counted():
    log = PageSnapshotLog(None)
    assert log.record([READ]) is False
    assert log.status()["enabled"] is False


@pytest.fixture
def snapshot_server(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    previous = (getattr(server.Handler, "token", ""), server.Handler.page_snapshots)
    server.Handler.token = ""
    server.Handler.page_snapshots = PageSnapshotLog(tmp_path, background=False)
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", port), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}", tmp_path
    finally:
        httpd.shutdown()
        httpd.server_close()
        server.Handler.token, server.Handler.page_snapshots = previous


def send(base, payload, headers=None, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(f"{base}/page-snapshots", data=body, method="POST",
                                     headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read())


def test_the_route_accepts_a_batch_and_reports_what_it_kept(snapshot_server):
    base, directory = snapshot_server
    status, body = send(base, {"schemaVersion": 1, "records": [READ, SEARCH, {**READ, "q": "x"}]})
    assert (status, body) == (200, {"ok": True, "accepted": 2, "rejected": 1})
    lines = (directory / "page-snapshots.jsonl").read_text().splitlines()
    assert len(lines) == 3  # gap line + two records
    assert "\"q\"" not in "".join(lines)
    with pytest.raises(urllib.error.HTTPError) as error:
        send(base, None, raw=b"{")
    assert error.value.code == 400


def test_the_route_requires_the_token_json_and_an_extension_origin(snapshot_server):
    base, _ = snapshot_server
    server.Handler.token = "existing-secret"
    batch = {"schemaVersion": 1, "records": [READ]}
    for headers in [{}, {"X-Ingest-Token": "existing-secret", "Origin": "https://evil.example"}]:
        with pytest.raises(urllib.error.HTTPError) as error:
            send(base, batch, headers)
        assert error.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as error:
        send(base, batch, {"X-Ingest-Token": "existing-secret", "Content-Type": "text/plain"})
    assert error.value.code == 415
    allowed = {"X-Ingest-Token": "existing-secret", "Origin": "chrome-extension://abc"}
    assert send(base, batch, allowed) == (200, {"ok": True, "accepted": 1, "rejected": 0})


def test_the_route_refuses_oversized_bodies(snapshot_server):
    base, _ = snapshot_server
    # Announce a body over the limit without sending it: refused before any read.
    connection = http.client.HTTPConnection(base.removeprefix("http://"), timeout=5)
    try:
        connection.putrequest("POST", "/page-snapshots")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(page_snapshots.MAX_BODY_BYTES + 1))
        connection.endheaders()
        assert connection.getresponse().status == 413
    finally:
        connection.close()
