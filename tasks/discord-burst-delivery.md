# Burst delivery improvement

Accepted scope: owner requested a significant improvement to burst delivery on
October 21, 2026. Keep $5/$20 tiers, exact Starter/free release clocks, historical
free snapshots, thumbnails, review/product buttons, channel privacy and 30-minute
paid freshness expiry. Do not revive expired/sent jobs or import old item history.

Baseline: actual local sender delivered about 317 destination posts in five
minutes; a catalog sweep accumulated thousands of due jobs and hundreds expired.
Code sends sequentially, waits two seconds between 20-job cycles, and stops all
routes on a single route throttle. urllib opens a new connection per request.

Implementation contract:

- Up to four independent channel requests in flight; never concurrent sends to
  the same route. Continue healthy channels when one is rate limited.
- Pack up to ten already-ready paid items (five for Free) from one channel into a message during a
  burst, with numbered item cards/buttons and photos. Do not wait to fill a batch.
  Single items retain the existing format. Discord message/embed limits apply.
- Persist exact dispatch body, ordered job membership and nonce before HTTP;
  settle/retry the whole leased dispatch in one SQLite transaction. Never repack
  an attempted dispatch under a new nonce. Legacy attempted jobs remain singles.
- Revalidate routing/seed/expiry before sending. If a captured dispatch becomes
  invalid, cancel its remaining work safely rather than changing its frozen body.
- Prefer freshly due paid work, fill its batch with oldest backlog where possible,
  and rotate paid channels. Due Free history waits while unexpired paid work is
  due; future Starter releases do not block it. No early release.
- Full fail-closed privacy audit before each bounded send wave; cancellation
  checks between audit requests and before HTTP. No security gate removal/cache.
- Reuse HTTPS connections, reserve a conservative shared request budget, and obey
  live bucket/global headers and Retry-After. Persist observed queue cooldowns.
- Wake immediately on intake and continue while useful work is ready. Expose
  queue age, due/future work, throttles, item/message counts and delivery timing.

Verification: failing regressions first; focused and full suites; realistic
offline burst/restart/throttle comparison using the actual dispatcher, without
flooding Discord; then rebuild the existing local service and observe real queue
progress. Report measured results and limits. Discord's nonce deduplication is
time limited; this is not an exactly-once guarantee after prolonged uncertainty.

Ownership: transport worker owns api.py and API/transport tests. Root owns queue,
dispatcher, notifier, CLI, integration tests and records. Read-only design reviewer
checks durable batches, privacy, recovery and scheduling. Preserve other app edits.

Primary sources checked:
[rate limits](https://docs.discord.com/developers/topics/rate-limits),
[message embeds and nonces](https://docs.discord.com/developers/resources/message).

## October 23 priority follow-up

Owner explicitly accepts delay for Free history and requested faster Pro and
expensive alerts. The current contract above supersedes the original five-item
and reserved-Free-slot behavior documented historically below. Paid envelopes
hold ten already-due items with no fill timer, within ten embeds, 6,000 UTF-16
text units and five component rows. New expensive Pro arrivals lead eligible
envelopes; oldest-paid fairness remains. Existing ten-item frozen dispatches can
retry intact even through callers requesting a smaller new batch size.

The sender checks recorded provider cooldowns before auditing/claiming. Full
fail-closed privacy audit still runs before each bounded four-channel wave.
Free waits while due paid retry/lease/cooldown work remains; filtering the next
deadline to paid work avoids wasted audits or busy wakes behind a Free job.
A synchronized regression reproduces and repairs the slow-Free request barrier
that formerly held the next ready same-channel Pro envelope. Future Starter jobs
and expired paid jobs do not block Free. Starter's 300s and Free's 86400s clocks
are unchanged; old expired/sent jobs stay retired.

Controlled final offline receipt: `data/discord_bridge/paid-throughput-wire-20261023.json`.
At 500 initial + 50 fresh arrivals, five→ten paid envelopes reduced paid posts
430→278 and audit GETs 990→565. Initial Pro drain improved 117.32→58.27s and new
high-value Pro p95 improved 8.66→2.76s. This is a virtual provider model, not a
live SLA. The separate slow-Free regression verifies mixed-tier isolation; that
model's paid phase had no due Free jobs. Final verification/deployment is linked
from `tasks/scan-freshness-20261022.md`.

Live follow-up also found oversized real ten-card bodies returning HTTP 413 and
a correlated SQLite lookup scanning future Free history under the writer lock.
The deployed follow-up uses a conservative 10 KiB body cap for new envelopes,
fresh definitive-rejection recovery with durable ambiguity tracking, and a
covering retry-batch index. Full privacy audits remain. Legacy uncertain attempts
are held for evidence review rather than being repackaged and possibly duplicated.

## Completion and verification

Implemented and deployed to the existing local service on October 21. Up to
five destination items share one message, with each photo, review-linked title
and numbered action row preserved. Singles have no fill delay. Four independent
channels can send in parallel. Fresh paid work, persistent route fairness,
bounded free service, expiry cohorts, intake wakeups and restart-safe cooldowns
replace the serial queue's head-of-line blocking and polling pauses.

All 934 Python tests pass (88.56s), repository Ruff passes and all nine changed
Python runtime/test files pass scoped formatting. Regression coverage includes
parallel overlap, route/global throttles, privacy failure, shutdown, immutable
restart retries, schema-3 legacy nonces, expiry/seed invalidation, atomic rollback,
overdue Starter fairness and the original tier clocks. Independent queue and
dispatcher review has no remaining required findings. The transport's 46 focused
API/transport tests and an actual live read-only privacy audit pass.

The active schema-3 SQLite queue was backed up consistently before the additive
schema-4 migration to
`data/discord_bridge/collector-pre-burst-1792617562.sqlite3`. The rebuilt Docker
service is healthy on the existing loopback-only port. Authenticated health
confirms schema 4 and live sending with no failures or privacy block. Receipts:
`data/discord_bridge/burst-live-before.json`, `burst-live-after.json` and
`burst-backup.json`. There were no due paid jobs immediately before deployment;
the new build did not clear the earlier backlog. All 2100 already expired jobs
remain expired.

A single labeled format preview in private #collector-health was read back from
Discord: five embeds, five thumbnails and ten numbered buttons. Each title opens
review context. Screenshot: `data/discord_bridge/burst-preview-20261021.jpg`;
receipt: `data/discord_bridge/burst-preview-verified.json`. No public test burst
or item claim was sent.

The repeatable offline comparison is `tests/benchmark_discord_burst.py`. It uses
the real JobStore SQL and dispatcher with virtual HTTP; fsync is disabled only
for its disposable databases. Assumed latency, channel/global quotas and nonce
cache are a test model, not live Discord observations or disk capacity evidence.
Real burst latency and member/mobile behavior still need a live pilot before
advertising an instant-delivery guarantee.

### Completed offline comparison before value priority

This comparison predates the value-priority follow-up below. Its fresh-item
latency figures are historical; the rerun below measures the updated policy.

Run: `.venv/Scripts/python.exe tests/benchmark_discord_burst.py --items 500`.
Receipt: `data/discord_bridge/burst-benchmark.json`. The old replica stops its
serial pass on the first retry and waits two seconds between passes. The new
scenario invokes the actual dispatcher. Both receive 500 initial items plus
50 fresh arrivals in ten batches, all arriving while paid backlog exists.

| Modeled measure | Old serial replica | New dispatcher |
| --- | ---: | ---: |
| Initial Pro destinations drained | 640.6s | 114.1s |
| Fresh Pro destination delay, p50 | 590.1s | 1.2s |
| Fresh Pro destination delay, p95 | 597.6s | 3.6s |
| Paid messages for 1925 destination jobs | 1925 | 477 |
| Paid HTTP requests including audit reads | 3565 | 1662 |
| All paid terminal, including Starter's 300s wait | 1005.1s | 410.6s |

That is 5.614x faster initial Pro drain and 75.221% fewer paid messages in this
model. Assumptions are 100ms per response, five posts per channel per five
seconds, 25 global requests/second including five sequential GETs per privacy
audit, and a retained remote nonce cache during restart. These are test inputs;
production learns Discord's actual limits. The policy decision itself is mocked;
the actual privacy implementation is covered by regression/live audits.

The fault scenario adds one route 429, one global 429 and a crash after remote
acceptance before local acknowledgement. Initial Pro drain is 123.6s; fresh Pro
p95 is 7.3s and worst is 51.1s while a lease recovers. Reopening SQLite preserves
the dispatch nonce and the modeled remote cache suppresses one duplicate retry.
In every scenario all 2480 intended destinations, including delayed free
history, eventually send. No tier releases early, no send occurs after expiry,
no destination item duplicates and none of the 18 deliberately expired paid
sentinels revives. Five-member immutable retry and expiry invalidation also have
separate real-SQL regressions.

Actual benchmark wall time was 76.297s, separate from all virtual times above.
Disposable databases keep a WAL connection open and disable fsync; these results
make no production disk durability or throughput claim. The modeled improvement
supports the deployment but does not establish an instant paid-alert SLA.

## Follow-up: value priority and persistent monitoring

Accepted October 21: owner requested expensive Pro items ahead of cheaper items
and other tiers, current throttle/unsent status, and logs for ongoing monitoring.
Priority slots now choose Pro before Starter/free and sort Pro destinations and
their newly formed bundles by descending observed retail value, reserving the
last position of a full Pro bundle for oldest same-channel work. Equal values
retain freshness/age tie breaks, with Pro all-items preferred for the first
notification. Existing chronological catch-up slots, per-route serialization,
immutable attempted bundles, cooldowns, tier clocks and expiry remain intact.
No send already in flight is interrupted or repackaged for price priority.
Catch-up uses the same expiry cohort as its leader; near-expiring records remain
separate and can still expire under overload. This is not an every-item guarantee.

Monitoring must answer: is ready work blocked; what happened to a specific
dispatch; how long did confirmed delivery take after its scheduled release; and
which unsent records are intentional waits versus retries/terminal outcomes?
Add allowlisted rotating JSONL delivery events in the existing private data
directory plus periodic queue summaries from the existing sender. No new public
messages, external telemetry vendor, open listener or autonomous scheduled chat.
Health metrics separate Pro/Starter/free due/future/retry/throttle/lease counts,
historical terminal reasons and recent p50/p95/max delivery latency. Intentional
tier waits are excluded from latency; legacy sends without sent_at are excluded.

Read-only baseline receipt: `data/discord_bridge/priority-status-before.json`.
At that check there were zero active cooldowns, due jobs or failed jobs; all
2367 pending jobs were future free history. There were 2100 expired destination
jobs across 1371 items and 64 routing skips. Expired destination jobs do not mean
every destination of each affected item failed. Keep these historical outcomes
visible and never replay stale alerts into the live feeds.

New priority regression first reproduced expensive backlog losing to fresh cheap
work. A read-only review also identified within-channel catch-up being excluded
by an active priority batch. The actual-dispatcher continuous-intake regression
reproduced this before reserving the final card for oldest work. Thirty focused
priority/burst regressions pass after the fixes. No required review finding
remains. The monitoring tests exercise real JSONL files, bounded rotation,
partial writes, correlated retries, startup privacy failures and sink-error
isolation; no raw page/provider text survives the allowlist.

The updated offline run is `data/discord_bridge/priority-benchmark.json`, using
the same 500+50 scenario and assumptions. Initial Pro drain remains 114.1s
versus 640.6s, and paid messages remain 477 versus 1925. Fresh Pro destination
p95 is now 22.4s (worst 47.4s), reflecting deliberate value priority. With
injected route/global throttles and an accept-before-ack restart, p95 is 96.5s
and worst 121.9s; initial drain is 181.9s. The model still asserts no duplicate
destination items, early tier releases, expired revivals or late-expiry sends.
These are simulated results, not a live SLA. Detailed operation and log commands:
[delivery monitoring](discord-delivery-monitoring.md).

Follow-up completion: all 957 Python tests pass; repository Ruff, nine-file scoped
formatting, server-start help and whitespace checks pass. Independent final
review has no required findings. The actual read-only Discord audit is clean.
The local image was rebuilt and the existing service replaced; it is healthy
with the same loopback-only listener and schema 4. Authenticated health reports
no active cooldown, due alert, failure or log-write error; pending free history
and terminal history remain unchanged. New operational queue heartbeat JSONL
was read from the live data volume. No public test messages were generated.
Receipt: `data/discord_bridge/priority-status-after.json`.
Two live idle summaries were read back 63.152s apart, verifying the ongoing
worker heartbeat rather than only startup logging. Receipt:
`data/discord_bridge/priority-monitoring-verified.json`.
