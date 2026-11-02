# Operational logging

The local logs connect scanner decisions, HTTP intake, product routing, queued
jobs and Discord delivery. They also record service/worker lifecycle, Gateway
recovery, personal alerts, membership/welcome/referral outcomes, browser recovery
and native-helper sessions. SQLite remains the authority for delivery state.

## Find a problem

Run these commands from the repository root:

```powershell
.venv\Scripts\python.exe tools/log_audit.py summary
.venv\Scripts\python.exe tools/log_audit.py trace --item ip-12345678 --ledger
.venv\Scripts\python.exe tools/log_audit.py trace --request aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --ledger
```

The request example is a placeholder: use an actual `X-Request-ID` from a response
or a recorded event. The default window is the last 24 hours. Narrow it with
`--since 2026-10-31T12:00:00-05:00 --until 2026-10-31T13:00:00-05:00`.
`--source browser` selects one source; `--data-dir` selects another data directory.

The summary reports event/outcome counts, source availability and p50/p95/p99/max
timings in separate cohorts. It does not treat a successful intake as a confirmed
Discord send. A request trace follows recorded product/job associations into
asynchronous dispatch; an item trace excludes unrelated products in the same
batch. `--ledger` adds read-only, bounded SQLite metadata without item payloads,
member identities or private watch phrases. Queue counts cover jobs scheduled
within the selected window; sent latency covers jobs sent within that window.
Inspect the returned cohort labels when interpreting old or future jobs.
Request traces distinguish exact job links from broader product associations.
Missing links remain unknown.

Always inspect `quality` and `sources` before interpreting totals. The tool
reports malformed/partial records, unknown labels, sequence gaps, duplicate or
conflicting browser records and read/output/relationship truncation. Defaults
bound reads to 512 files, 128 MiB, 500,000 records, 64 KiB per line, 250,000
relationship candidates, 10,000 relationships and 100 output trace events.
`--limit` changes displayed trace size within the tool's hard bounds. Archives
are included; compressed input also has a decoded-size bound.

The Docker backend also contains the tool:

```powershell
docker exec reviewer-item-notifier python /app/tools/log_audit.py summary
```

That container can read its mounted data. The Windows host can additionally read
the recovery/native files written outside Docker.

## Files and retention

All paths below are under `data/` unless stated otherwise.

| Source | File | Active / total size budget |
| --- | --- | --- |
| HTTP, backend lifecycle, configuration and non-Discord pipeline | `operations.jsonl` | 2 MiB / 16 MiB |
| Discord routing, sends, workers, personal alerts and referrals | `discord_bridge/delivery.jsonl` | 10 MiB / 64 MiB |
| Browser event history | `browser-diagnostic-events.jsonl` | 2 MiB / 16 MiB |
| Latest browser snapshot | `browser-diagnostics.json` | 1 MiB report limit |
| Windows recovery supervisor | `browser-recovery.log` | 256 KiB / 2 MiB |
| Native helper | `native-verification/host.log` | 64 KiB plus two rotated segments |

The audit tool also accepts the optional legacy
`discord_bridge/requests.jsonl`. JSONL/recovery archives rotate by size/day and
use the existing 60-day age target with a hard space backstop. This is **not a
guaranteed 60-day history**: size limits can remove older records sooner. Native
logs use their existing numbered rotation.

The browser keeps at most 300 events for seven days and queues at most 512
recorder writes. Version 2.26.12 assigns stable stream/sequence IDs. The backend
acknowledges an uploaded sequence only after writing its archive and snapshot;
retries preserve the sequence and are deduplicated. Overwrite, rejected-event,
storage-error, sync-failure and sequence-gap counters expose loss rather than
assuming every buffered event survived.

## Identities and health

`X-Request-ID` is 32 lowercase hexadecimal characters. Invalid or duplicate
headers receive a generated ID. Browser records call it `requestId`, HTTP records
`request_id`, and Discord records `http_request_id`. The existing numeric Discord
`request_id` remains the saved personal-watch ID. Public `item_id` values and
internal job/dispatch IDs connect committed routing receipts to later deliveries.
Document/lane/target/generation IDs explain which scanner work produced a record.

Authenticated `/health` includes `logging.operations` with backend logging
configuration, written/dropped counters, last sanitized error and run ID;
`logging.browser` includes browser archive/snapshot/logging health.
Existing delivery-worker health exposes monitoring failures, queue age
and worker liveness. Browser diagnostic reports carry recorder/sync counters;
native protocol status and recovery records carry their own dropped-event count.
Counters are process-local unless included in a persisted report.

## Scope and failure behavior

Logs use fixed labels, numeric IDs/counts/timings and sanitized error classes or
provider codes. They exclude raw request/response/page bodies, headers, query
strings, credentials, profile/account names, private watch phrases and coupon or
checkout data. Idle polls are summarized or logged on state change; repeated
unchanged referral outcomes do not create repeated change records.

Logging is best effort. A failed write cannot undo committed intake, resend a
confirmed message or prevent native input cleanup. Disk failure can also prevent
the failure counter itself from being persisted. Bounded buffers can lose history
during a long outage; historical missing events cannot be reconstructed. The
Whop client exposes sanitized provider codes/status, but does not retain the
underlying socket exception class. The alternate standalone port 8788 records
request identity and accepted intake/routing; the main port 8787 supplies the
complete HTTP start/completion audit, including rejected requests.

Install the compatible backend before reloading **Reviewer Item Relay 2.26.12**
and refreshing scanner tabs. Keep saved sweeps, settings and coverage. Older
extensions remain accepted but cannot supply the new browser sequence/context
history. A version label alone does not prove the running modules refreshed:
new stream counters and a durable receipt in `logging.browser` confirm activation.
The release evidence is in
[the October 31 logging record](../tasks/logging-overhaul-20261031.md).
