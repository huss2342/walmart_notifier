# System logging overhaul — October 31, 2026

Owner authorized improving logging across the system so operational data can be
tracked. **Backend, recovery and browser logging verified live.** Existing
scan/delivery behavior, saved watches, settings, verification controls and search
depth remain the contract; instrumentation must not change an operation's outcome.

## Questions the logs must answer

1. What did each scanner attempt/read, and why did it wait, recover, finish or
   leave coverage unresolved?
2. What happened to every product in an intake batch, including deduplication,
   filters, new routes, dispatch, retries and confirmed/uncertain delivery?
3. Did a service, worker, dependency or log stream stop reporting, and were any
   events rejected, overwritten or lost?
4. Can one request/product be traced and a time window summarized using local,
   read-only tooling, including archived logs?

## Accepted implementation contract

- Retain local structured JSON/JSONL and existing bounded compressed rotation;
  add no external telemetry service or new paid dependency.
- HTTP `X-Request-ID` is exactly 32 lowercase hex characters or replaced with a
  generated identity. It is echoed in responses and carried across context.
  Browser events use `requestId`; main API logs use `request_id`; Discord events
  use `http_request_id`. Existing numeric Discord `request_id` remains the saved
  watch ID. Product/job IDs join intake to the durable delivery ledger.
- Record significant state/decision changes, request starts/completions,
  classifications, domain outcomes, errors and durations. Do not log raw page,
  account, request/response bodies, credentials, query strings, private watch
  phrases, checkout/coupon data or every repeated idle poll.
- Browser streams gain stable identity/sequence, durable sync receipts and
  bounded recorder/loss/sync counters. A receipt cannot acknowledge unarchived
  events. Older reports remain accepted. A bounded buffer is not described as
  lossless during arbitrary outages; gaps are visible.
- Logs are best effort; failures expose counters/health and cannot roll back a
  committed observation, retry a confirmed send or fabricate successful work.
- Every fresh/changed committed channel route is recorded after the transaction.
  Group receipts preserve all bounded product/job IDs, avoiding per-product disk
  writes during a burst. SQLite remains authoritative for exact per-job state.

## Coverage and ownership

| Area | Instrumentation / responsibility |
| --- | --- |
| HTTP/service/configuration | New `operations.jsonl`, sanitized start/end receipts, request context, lifecycle, counts and successful configuration changes; root |
| Non-Discord pipeline | Per-product filter/dedup/seed/pending/delivery decisions plus final intake receipt; root |
| Browser discovery/navigation/coverage | Common document/lane/target context, intake batches/outcomes, late/stale acceptance, verification state, sequence/receipts/loss health; browser worker |
| Discord intake/routing/delivery | All batch IDs, post-commit route receipts, safe transport outcomes/classes/bytes, intent/result and worker transitions; delivery worker |
| Personal alerts/Gateway | Matching, request lifecycle, private delivery stages and connection/recovery transitions; delivery worker |
| Welcome/membership/referrals | Distinct component labels, domain-change summaries and failures/recovery with safe references; delivery worker |
| Host recovery/native helper | Structured decision/session/operation outcomes, durations and logging-failure visibility; audit worker |
| Data inspection | Bounded `tools/log_audit.py` summary/trace, compressed history, explicit truncation and optional read-only ledger metadata; audit worker |

No worker may revert concurrent edits. Their exact before/after snapshots and
focused evidence are retained under `data/logging-overhaul-20261031/`.

## Verification and performance

- Main HTTP missing correlation receipts first reproduced; current focused
  HTTP/context/privacy/failure-isolation/retention checks pass. Existing HTTP and
  pipeline integration: 210 passed before the final additive receipt tests.
- NTFS burst profiling caught an instrumentation cost: 500 new items produced
  2,001 separate routing log rows and took 1.743s median versus 0.060s without
  tracing. Grouping retains all IDs in five rows: 0.0695s versus 0.0582s baseline
  (three samples/variant). This is a local benchmark, not production throughput.
- Browser buffers/archives retain their current size bounds; explicit overwrite,
  sync, archive and sequence gap accounting replaces silent loss.

- Final full Python suite: **2,602 passed, one existing platform skip** in
  216.79s. Full JavaScript suite: **962 passed**, zero failures. Final CLI
  regressions: **38 passed**, independently repeated. Ruff, protocol generation,
  server startup/help, extension/helper syntax and whitespace checks pass.
- Independent reviews approved browser/server contracts, HTTP/pipeline context,
  delivery/referral instrumentation, native/recovery failure isolation, CLI
  correctness/privacy/bounds and release/runtime-preservation helpers. Required
  findings were fixed: unchanged referral outcomes no longer repeat change
  records; log-open failure cannot prevent native operation; bootstrap failures
  are recorded; CLI includes new browser fields, preserves HTTP vs watch IDs,
  and requires real links for combined request/item filters.
- Actual final Docker candidate passed isolated local HTTP → durable queue →
  fake Discord send → asynchronous request trace/exact ledger checks with
  `--network none`, read-only root and temporary state. A simulated log-write
  failure preserved successful duplicate intake and durable deduplication. This
  exercised the real packaged modules; it sent no real test messages.
- Browser recorder benchmark (five paired warm synthetic runs, 300 events):
  median 5.155 → 5.877ms/event (+0.722ms), unchanged 300 reads/300 writes;
  serialized write volume 34.90 → 40.50MB (+16%). This measures synthetic
  cloning/marshalling, not Chrome IPC, disk or live throughput. Two additional
  normal intake-stage records/page are about 13/min at the sampled 6.6 pages/min.
- Exact raw final hashes for 36 source/test paths are saved in ignored
  `data/logging-overhaul-20261031/final-source-hashes.json`. The final CLI is
  `6ed6446226dd9cb66e96a1a73bb0cd90ceb2ac36b2a2fd857290531cda01e57b`.

## Operation and limits

See [logging commands, file budgets and identities](../docs/logging.md). Logs
are local, redacted and bounded, with a 60-day age target subject to space caps.
They cannot promise lossless history through an arbitrary outage or reconstruct
missing historical events. The main 8787 service has full HTTP start/completion
receipts; the optional standalone 8788 service records identity and accepted
intake/routing. Whop provider codes remain available but its underlying socket
exception class is not retained. Native watchdog behavior is unchanged; host
operation/status/session transitions provide its existing observable boundary.

Browser source is **2.26.12**, including the previous retry-race and high-traffic
waiting fixes. Backend accepts old reports. Full new browser sequence/context
history requires reloading **Reviewer Item Relay** and refreshing the scanner
tabs while keeping saved sweeps/settings/coverage. Chrome internal Extensions
controls cannot be claimed by the available browser tool; activation there must
be done by the owner. Native input remains disabled in current settings and was
not enabled for testing.

## Activation and live checks

- Exact **20-module overlay** over deployed `b4c0c4bcd336`; verified each complete
  baseline and final source hash. No whole dirty-checkout rebuild. Image
  **007848259ff4** activated healthy **2026-10-31 17:01:53 UTC / 12:01:53 CDT**.
  Full Docker host configuration, environment, mounts, network names, command,
  user/security settings and scanner/relay settings match the baseline.
- Rollback image: `reviewer-notifier:before-logging-overhaul-20261031` points to
  the exact previous image. Reviewed activation helper automatically restores it
  on a failed health/configuration check. Detailed receipts are retained under
  ignored `data/logging-overhaul-20261031/`.
- Existing recovery task `ReviewerItemNotifierRecovery-121c23d6dd01` restarted
  **17:02:16 UTC**. Exact task XML unchanged; old process tree exited and the
  replacement is running. New `recovery-started` / `healthy` JSON records show
  zero dropped events. No task reinstallation, permissions change or Chrome
  command was issued by the restart helper.
- Final live snapshot: healthy main/personal workers and connected Gateway;
  welcome/membership healthy; referrals running with zero exceptions; zero due
  channel jobs and zero failed channel jobs. Operations had 155 written events,
  zero dropped events and no last error; delivery monitor had zero dropped
  events; browser archive/snapshot failure counters were zero. All **12 saved
  watches unchanged** by SHA256
  `7b1b18b1f169c7b2b412cc413edc18772b2abbdac4990880c1d32b85231d0618`.
  Historical uncertain personal sends remain held under the existing policy.
- A natural 38-product duplicate intake joined its generated HTTP identity to
  the post-commit routing receipt and exact one-job ledger association. That
  job was expired on readback; its original schedule preceded the trace window,
  so the tool correctly returned no in-window scheduled-job counts. This is
  association evidence, **not a newly confirmed live send**. The final packaged
  fake-provider smoke separately verified confirmed asynchronous send tracing.
- The broad live archive audit reached its explicit 128 MiB decoded-read cap
  and reported truncation, 39 legacy plaintext recovery rows and one partial
  live/archive line, with zero read errors or malformed JSON rows. Those capped
  totals are not a complete history. Filter by source/window and inspect quality
  before drawing coverage or timing conclusions.
- The latest browser report labels version 2.26.12 but still lacks new stream
  counters/receipt/events; the version alone cannot prove refreshed modules.
  Reload the extension and refresh scanner tabs, preserving saved coverage.
  At that initial checkpoint, new browser recording/sync was implemented and
  tested but not yet verified active.

### Subsequent browser activation confirmation

Read-only coverage follow-up **2026-10-31 18:49 UTC / 13:49 CDT** observed
extension 2.26.12 with **9,442 sequenced events**, a durable archive receipt,
**zero gap events**, **zero sync/storage failures** and **zero unacknowledged
overwrites**. This confirms refreshed browser logging is active and supersedes
the earlier pending-reload state. Snapshot evidence:
`data/coverage-review-20261031.json`. No browser settings, scanner checkpoints,
code or production delivery state were changed for this check.
