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

The runtime image does not contain `tools/`; pipe the script into the
container instead (it uses only the standard library):

```powershell
Get-Content tools/log_audit.py -Raw | docker exec -i reviewer-item-notifier python - summary
```

That container can read its mounted data. Since November 8 the Discord SQLite
databases live on the `reviewer-notifier_discord-db` volume (`/app/db`), so
`--ledger` works only inside the container (it follows `DISCORD_QUEUE_PATH`);
delivery logs remain in `data/discord_bridge/` on the host. The Windows host can additionally read
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

Version 2.26.15 adds `targetId`, a stable 32-character hexadecimal digest of the
internal target key. Use it with lane, round, generation and document ID when
comparing searches. **`targetIndex` is an ordinal position, not a query identity:**
unresolved targets move to the front of later rounds. Historical logs without a
stable identity cannot prove the query name from its saved-list position.
The raw key, query string and URL are not retained. A digest is correlation
metadata and never authorizes a page or a scanner action.

`scanner-state` preserves runtime observations independently of the overwritten
latest status file: actual phase/focus reason/visibility, reachability, active and
busy flags, traffic/verification/enablement, coordinator progress timestamp and
next action. Observations emit on meaningful changes at status polls, plus at
most one unchanged heartbeat per minute per lane. They are not a record of every
short-lived DOM transition. Activity and next-action timestamp changes alone do
not defeat suppression.

`scanner-recovery` records requested, accepted, deferred or unavailable outcomes,
with fixed guard decisions. A requested recovery is not proof that the receiver
accepted it or that a later page completed. Numeric receipt/document guards
prevent a late request from interrupting newer progress. `interior-empty` and
`card-unreadable` distinguish confirmed missing interior results from candidate
cards the reader could not extract; neither code fabricates completed coverage.

Discord error receipts now include validated HTTP status, provider code,
effective retry delay, and (for 429) the global flag and `rate_limit_source`:
`local` means a known preflight gate, `server` a received HTTP 429, and `unknown`
means provenance was not supplied. Shared projection preserves transport and
ambiguity fields without keeping exception text, response bodies or member IDs.
Earlier records cannot be retroactively classified from missing metadata.

Every Discord REST call is recorded as `discord_api` in `delivery.jsonl`:
`caller` (channel, personal, welcome, membership, trials, gateway,
request-runtime), `method`, `endpoint` (route shape with `{major_id}`/`{id}`
placeholders; IDs and tokens are never written), `status`, `elapsed_seconds`,
`outcome`, `ratelimit_remaining`, and for 429s the `rate_limit_source`. A
`local` source means the shared client refused before sending (contention or
a learned cooldown), so no request reached Discord. Records are written by a
background thread; `discord.monitoring.api_events_dropped` in
`/health` counts any dropped under extreme backlog. The summary's
`discord_api_counts` groups calls by caller:method:endpoint:status.

Personal requests: each intake emits one `request_match` per watch that a
product touched: `matched`, `search_matched` (found by Walmart's search for that
watch's own query), `queued`, `deduplicated` (already sent), `held`, and near
misses `below_minimum` and `unavailable` (text matched but the value was under
the member's minimum, or the card was out of stock) with up to ten
`near_item_ids`. `intake` records `value_missing`, `unavailable` and `searched`
(items that arrived with their Walmart search query). The summary's
`request_totals` sums these per watch ID. Browser `card-extraction` events keep
`missingValueCount` and per-card samples, so a missing value can be traced to
the page that produced it.

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

## Investigating a false "Back in stock"

Since 2.32.3 a restock alert needs two in-stock reads of an $80+ offer the
extension had seen out of stock, at least 9 s apart. On November 12 both restock
alerts were single-read blips: Walmart's list data showed offers that had been out
of stock for days as `IN_STOCK` for one read (every stock field agreeing, the rest
of the page unchanged) and out of stock again 10-40 s later. The first in-stock
read is now held (not sent to the backend at all), its page is re-read 10 s later
and re-sent even if unchanged, and only a confirming read announces it.

Since 2.32.4 only a re-read of the same page (same lane, page number and, for
searches and departments, target) confirms. On November 19 ip-20755219869 was held
on `value` page 1 and "confirmed" 9.15 s later by an incidental `value80` read;
`value` page 1's own re-read 1.3 s after that showed it out of stock. An in-stock
read of another page now only starts that page's own check, and an out-of-stock
read from any lane or page rejects the hold. An offer that blipped 3+ times in 24
hours (held then out before confirming, or confirmed then out within 5 minutes;
kept in the extension's session storage, so reloading the extension clears it)
must read in stock on one page for 60 s, at least 4 times, no more than 30 s
apart. Browser `restock-watch` events (`--source browser`, `trace --item ip-...`)
record each step. All fields are item IDs, fixed labels, booleans or numbers.

- `stage: returned` is the first in-stock read, now held. `awayMs` is the time
  since the offer was first seen out of stock, `sinceOutMs` since the last
  out-of-stock sighting, `outReads` how many such sightings this worker made
  (timings and counts reset if the worker restarted; `awayMs` survives).
  `count` (2.32.4) is how many times it blipped in the last 24 hours; 3 or more
  means the 60 s steady check applies.
  `lane`, `page` and `requestId` identify the read; the same `requestId` is the
  backend `http_request_id`, so the `observation_routed` / `dispatch_*` records
  of that alert join to it.
- Stock evidence on the event: `stockSource` (`data` = Walmart's list data,
  `card` = visible card text), `signalV2` (`availabilityStatusV2`, the field the
  alert trusts), `signalStatus` (`availabilityStatus`), `signalOos`
  (`isOutOfStock`), `variant`, and `conflict` (Walmart's own fields disagree on
  this read). `canAddToCart` and `showAtc` are false for every free offer and
  are not logged.
- `stage: confirmed` is the read that announced it (`sinceReturnMs` after the
  first in-stock read; since 2.32.4 `reads` is how many in-stock reads its own
  page made, and `count` the recent blips). `stage: rejected` means it was never
  announced: `stockState` `out-of-stock` (out again on any page before
  confirming) or `absent` (gone from every page that held it, or not confirmed
  within 2 minutes; 3 for a steady check).
- `stage: followup` records a confirmed item on later reads: `stockState`
  `in-stock` (up to three sightings, so a real restock shows as steady),
  `out-of-stock` (it went straight back: `sinceReturnMs` is how long the return
  lasted) or `absent` (not on the page it returned on; only the `value` and
  `value80` lanes can say this). No follow-up within five minutes means the page
  was unchanged and was not re-sent.
- `fast-read` events now carry the read's stock mix (`stockIn`, `stockOut`,
  `stockOther`: free offers by `availabilityStatusV2`), `products`, `bytes` (page
  size) and `ageSeconds` (the CDN `Age` header, when present).
- Since 2.32.6 a search read (`requests` or `priority` lane) also carries
  `searchHash`, a 32-bit FNV-1a hash of its normalised query (trimmed, spaces
  collapsed, lower case; never the query text), `count` (its $80+ items), `hot:
  true` when it was one of the six hot searches read every minute, and the
  upload's `requestId` (any lane), which joins the read to the `ingest-stage`
  events and the items it delivered. To name a hash, hash the known queries:
  `h = 0x811c9dc5; for b in key.encode(): h = ((h ^ b) * 0x01000193) & 0xffffffff`.

Reading the evidence:

| What you see | Likely cause |
| --- | --- |
| `returned`, then `rejected out-of-stock` seconds later; neighbouring reads of the page have the same `stockIn`±1 | Walmart's list data flickered for that one offer; no alert was sent |
| `confirmed`, then `followup out-of-stock` soon after | a blip longer than the 9 s confirmation; 3 blips of an offer in a day turn on its 60 s steady check (`STEADY_MS`, `BLIP_LIMIT` in `restock-watch.js`) |
| `conflict: true`, or `signalV2` differs from `signalStatus`/`signalOos` | inconsistent data in one response |
| `ageSeconds` above 0, or `bytes`/`products` far from the neighbouring reads, or `stockIn` jumping on many offers at once | stale cache or a partial/odd page (a load problem) |
| `confirmed`, then `followup in-stock` on the next reads and `stockIn` stays up | a real restock |
| `stockSource: card` | a tab read the visible card text, not the list data |

The extension keeps these in its own 300-event ring and the archive described
above; `stock` evidence is never sent to the backend.

## Scope and failure behavior

Logs use fixed labels, numeric IDs/counts/timings and sanitized error classes or
provider codes. They exclude raw request/response/page bodies, headers, query
strings, credentials, profile/account names, private watch phrases and coupon or
checkout data. Idle polls are summarized or logged on state change; repeated
unchanged referral outcomes do not create repeated change records.

Since November 12 the running service writes `operations.jsonl` and the live
bot's `delivery.jsonl` from one background thread per file: each record is
still built (timestamp, request ID) by the caller, queued, and written in file
order, with records waiting together sent in one append. On the Windows bind
mount every append cost 8-18 ms, and `/ingest`, settings reads and Discord sends
waited for several of them (`/ingest` p50 65 -> 42 ms after the change). A failed
write is retried once, then counted in `dropped_events`/`last_error` in health;
at most 10,000 queued records per file, beyond which new ones are counted as
dropped. Shutdown flushes the queue; a hard kill can lose the last few
milliseconds of records. Tests and CLI tools keep synchronous writes.

Logging is best effort. A failed write cannot undo committed intake, resend a
confirmed message or prevent native input cleanup. Disk failure can also prevent
the failure counter itself from being persisted. Bounded buffers can lose history
during a long outage; historical missing events cannot be reconstructed. The
Whop client exposes sanitized provider codes/status, but does not retain the
underlying socket exception class. The alternate standalone port 8788 records
request identity and accepted intake/routing; the main port 8787 supplies the
complete HTTP start/completion audit, including rejected requests.

Install the compatible backend before reloading **Reviewer Item Relay 2.26.15**
and refreshing scanner tabs. Keep saved sweeps, settings and coverage. Older
extensions remain accepted but cannot supply the new browser sequence/context
history. A version label alone does not prove the running modules refreshed:
new stream counters and a durable receipt in `logging.browser` confirm activation.
The release evidence is in
[the October 31 logging record](../tasks/logging-overhaul-20261031.md).
