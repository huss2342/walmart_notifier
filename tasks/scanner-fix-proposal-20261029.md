# Scanner logging release and remaining fix proposal — October 29, 2026

## Accepted scope and delivery state

Owner authorized logging improvements for incident I1 and further investigation
and proposals for I2–I4. This pass implements logging only. No FIFO, retry,
eligibility, verification, pacing or delivery behavior was changed.

Extension source **2.26.6** contains the new capture/emission and sanitization.
The backend validator is live on image **e8a9f88c011b**, healthy at **13:44:18 CDT**.
Only `src/browser_diagnostics.py` was overlaid on exact live image
`60bd0ba9a16b`; runtime configuration, environment, mounts, networks and saved
scanner/relay settings match their predeployment snapshots.

**Browser activation remains required:** reload Reviewer Item Relay in
`chrome://extensions`, verify version 2.26.6, then refresh the scanner tabs.
The available browser interface cannot operate Chrome's internal extension page.
Do not reset saved settings or restart sweeps solely to enable this logging.
Until reloaded, the live 2.26.5 browser cannot emit these new records.

## I1 logging — implemented and verified

New `page-state` records capture these six lifecycle stages:
`empty-observed`, `empty-confirmation`, `fallback`, `unusable`,
`zero-page-complete`, and `recovered`.

Each contains lane, document/generation/page identity, confirmation attempt,
empty duration, observed item count, acknowledged sweep-page count and mode.
The exact typed state object contains:

- Document ready/visibility/route classifications and presence of the reviewer
  heading, results label, no-results message, visible loader, error wording and
  password input.
- Product-link and valuation-line counts, pager presence and recognized rendered
  page, to distinguish no cards, rejected cards and pagination mismatch.
- Navigation type and document HTTP status when the browser exposes it, plus
  a count of observable same-origin fetch/XHR HTTP failures in the last 1,000
  retained resource entries. No new requests or permissions are introduced.
- Reading failure/work counters and numeric fallback query position/count.
  Search strings are not exported.

The snapshot is observational: an error flag means matching visible wording,
not a proven site exception. HTTP status 0 means unknown/unavailable; zero
recorded request failures does not establish that every request succeeded.
Performance entries can be absent, evicted or unobservable. Redirects outside
the extension's content-script routes may produce no page-state event.

Records contain no raw DOM, page/account text, URLs, resource addresses,
credentials, exception bodies or response bodies. JavaScript allowlisting
and strict Python validation enforce the same fixed contract. Capture/storage
failure cannot alter scan decisions. A stage is limited to one event per ten
seconds per document scope, and unchanged evidence is limited to once a minute.
Lifecycle changes can therefore be recorded without polling noise.

Events use the existing rotating `data/browser-diagnostic-events.jsonl` archive,
including its space/retention bounds, restart deduplication and snapshot
acknowledgement behavior. A new logging service was unnecessary. Empty-query
handling, completion semantics and retry policy remain unchanged in this release.
Together with existing focus, extraction, navigation and intake events, these
facts provide the missing context if the zero-throughput cycle recurs; they
cannot reconstruct the lost overnight DOM or promise every external cause.

Verification:

- **843 JavaScript tests passed.** The first complete run exposed a stale exact
  version assertion; updating it to the new release version produced the final
  passing suite. No assertions were weakened or tests skipped.
- **2,148 Python tests passed, one existing Windows-platform skip.** Initial
  sandbox execution could not access pytest temporary directories and was
  stopped. The normal-permission full run exited successfully with 2,149
  collected tests.
- **Nine real Chrome local replay checks passed**, with no console errors or
  warnings. Real DOM no-results/card transition, hidden loading state,
  throttling, sanitization and privacy were exercised. This was an isolated
  replay, not a claim of live Walmart activation.
- All six stages from actual JavaScript sanitization passed the deployed
  Python validator in memory, without posting synthetic events to live logs.
- Independent review found no required findings; lint, syntax, protocol
  generation and server startup checks passed.
- Postactivation health: relay age 18 seconds, no overdue items, zero active
  Pro jobs, personal sender running.

Exact before copies, task-only diff, hashes, test logs, browser replay, image
baseline and activation receipt are ignored under
`data/page-state-logging-20261029/`. Rollback image:
`reviewer-notifier:before-page-state-20261029`. Use the same configuration-
preserving overlay process; do not rebuild the unrelated dirty checkout.
Temporary replay tab/server and test processes are closed.

## Remaining proposed fixes — no behavioral patches installed

### I1: prevent repeated zero-throughput rounds from looking like useful health

The overnight root cause remains unknown. Current code can finish a fallback
round after reading zero product pages, write a warning, then schedule another
round that repeats all empty-query confirmations. The mechanism is reproduced;
the new telemetry will identify the observed page state around the next event.

Proposed repair: retain separate timestamps/counts for successful intake,
verified coverage and cursor completion. A zero-page cursor completion must not
advance useful-health or verified-coverage indicators. Correlated persistent
empty/failed rounds should enter bounded, jittered recovery/backoff, preserving
checkpoints and issuing one clear health notice per episode. Exit backoff on
positive recovery evidence. Do not treat an isolated legitimate empty query as
an outage, shorten pacing, or add product-page requests. Choose the precise
global trigger from the new cross-lane evidence rather than guessing a site
maintenance cause.

Required proof: valid empty query among healthy ones; persistent cross-lane
emptiness; delayed hydration; recovered cards; no notification storm;
checkpoint/interior-gap retention; verification and manual-browsing guards.

### I2: correct auxiliary timeout accounting and failure classification

Further probes established pre-existing defects on exact 2.26.5 source and the
current logging source:

1. A blank, already-bound department reports render timeout after 31 seconds
   with **zero focus requests**. It can spend its budget before receiving an
   initial foreground rendering opportunity.
2. The same timeout occurs **1 ms after a trusted keydown event**. This readiness
   budget does not pause during the existing user-input grace period.
3. Coordinator stale detection consumes retries despite a responding probe
   reporting `waiting-focus`, or `busy: true` during `waiting-content` or
   `waiting-selection`. It correctly defers `waiting-user` and future action
   timers, but can label other deliberate delays as unresponsive content.

These are deterministic timeout/guard defects. They fit parts of the historical
skip pattern but do not prove why each of the eight department pages lacked
cards. The new state records should separate that external rendering question.

Proposed patches:

- Separate active rendering/selection work time from user, verification and
  scheduler waiting time. Suspend the active budget during documented guards.
  Give a blank document one bounded initial readiness opportunity through the
  existing guarded focus coordinator before consuming its render-failure budget.
- Treat a fresh, generation/document-matched probe as a responding document.
  Distinguish delayed, stalled and missing receivers. Do not exempt `busy` or
  wait labels indefinitely: require bounded opportunity/progress accounting,
  fresh heartbeats and finite recovery for actual lost/stalled content.
- Emit explicit fixed failure/stage codes for render timeout, reading failure,
  reading-budget exhaustion, repeated-page content and unverified pagination.
  Retain numeric round/tab/generation/attempt context at skip time. Update both
  JavaScript and Python contracts together; do not export raw error/query text.
- Separate active-target health, round failures and carried unresolved coverage
  in status. Preserve unresolved work until verified; do not clear warnings by
  counting failed targets complete.

Ownership: `content.js` readiness paths, `auxiliary-scans.js` coordinator and
status, `scanner-control.js`, `diagnostics.js`, backend validator contracts and
options status. Required regressions include real input during a blank wait,
initial hidden rendering opportunity, waiting-focus/busy probe liveness,
unresponsive receivers, stale generations, resume after guards and bounded retry
expiry. Preserve existing acknowledged receipts and target retry order.

Proof artifacts: `data/investigation-20261029/unresolved/` readiness-timeout
baseline/current probes and coordinator-wait-budget results. No behavior fix
has been applied to these paths.

### I3: repair FIFO acquisition starvation

The scheduler defect now has a concrete isolated candidate, not just a general
recommendation. A non-head request must not acquire the global acquisition mutex
while it is itself deferring to the queue head. It can perform bounded guards
without owning acquisition, then return before lease or tab mutation. The
eligible head remains able to acquire; existing lease owners retain their
original finite lease, without extension. Late non-owner callbacks cannot clear
a newer acquisition because identity checks remain mandatory.

All six fixed concurrent request orders starve the baseline for 20 simulated
minutes. The candidate gives every scanner **49–50 distinct leases**, with a
maximum sampled service gap of **30 seconds**. Rotating/changing orders also
progress. The 69 existing workspace/handoff checks pass unchanged. Additional
probes preserve pause, manual-tab, user-activity, deadline and stale-generation
guards, invalid-head cleanup, and newer leases after late non-head callbacks.

Proposed implementation: promote these adversarial probes into maintained
regressions, apply/review the narrow `tab-workspace.js` acquisition change, then
observe real three-scanner progress. The isolated times are not a universal live
latency guarantee. Removing FIFO, the browsing guard or increasing lease length
does not fix the demonstrated mechanism.

Candidate and results: `data/investigation-20261029/focus/candidate-design.md`,
`tab-workspace.candidate.js`, adversarial and timeout results. Production FIFO
source remains unchanged.

### I4: contain sender recovery failures, classify SQLite errors and report recovery

New confirmed defect: after an initial database error, retry/settlement can also
raise. That second exception can escape `RequestWorker.tick` before its diagnostic
is emitted; the sender loop has no outer exception boundary and can terminate.
A synthetic recovery-persistence failure reproduced the escape in 28 ms with
zero product POSTs. This is separate from choosing the historical SQLite cause.

Proposed patches:

- Emit bounded original failure classification before recovery writes. Guard
  recovery persistence independently and preserve both error classifications.
  Contain unexpected worker-loop failures with cancellable bounded backoff so a
  fast repeated database failure cannot kill the sender or create a tight loop.
- Preserve durable lease/sending state when recovery cannot be persisted.
  Pre-submission work may resume safely under existing lease rules; uncertain
  post-submission delivery must remain review rather than automatically resend.
- Record fixed component/operation, exception class, numeric primary/extended
  SQLite code, allowlisted symbolic subtype, elapsed operation time, failure
  time and whether submission/recovery persistence occurred. Never log SQL,
  exception bodies, credentials, member data or request keywords.
- Track successful worker polling separately from successful DM delivery,
  including current error, historical error time and recovery time. The stale
  error flag was reproduced. A real DM at 13:22:55 cleared it as predicted;
  delivery is currently recovered.
- Select transaction/storage remediation only after the subtype is recorded.
  Missing schema, read-only and stale-snapshot errors all reproduced in
  milliseconds, demonstrating why one-second recurrence does not identify lock
  exhaustion. Measure connection/begin/claim/intake/commit/close boundaries.
  If contention is confirmed, shorten/serialize the responsible writer or add
  bounded safe pre-submission retry. If filesystem/schema errors are confirmed,
  address that specific cause. Larger timeouts and journal changes are not
  presently justified.
- Keep the October 27 ambiguous review row separate. Reconcile it only against
  authorized provider send history; do not blindly replay it.

Ownership: request worker/store/commands, public intake/delivery boundaries,
bounded monitoring sanitizer and health projection. Required tests cover original
plus recovery failures, idle poll recovery, cancellable backoff, worker survival,
duplicate-safe retries, uncertain sends, restarts and privacy. Detailed proposed
contract and proofs: `data/investigation-20261029/backend/patch-contract.md`,
`fast-failure-results.json` and `offline-probe-results.json`.

## Proposed implementation order

1. Activate the accepted browser logging and observe natural state records.
2. Land independently reviewable FIFO and auxiliary-budget repairs with their
   failing maintained regressions; preserve all existing safety/coverage rules.
3. Land sender error containment, classified diagnostics and truthful health as
   a separate delivery patch with duplicate/uncertainty regressions.
4. Choose the underlying Walmart recovery and database transaction/storage
   remedies from classified evidence; validate matched useful coverage and
   delivery latency in bounded live observations.

All items in this section remain proposals. This pass activates only the
compatible logging backend and prepares browser logging 2.26.6.
