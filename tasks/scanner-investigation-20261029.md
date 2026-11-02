# Scanner reliability investigation — October 29, 2026

This is the initial investigation record. For the subsequently authorized logging
release, deeper reproductions and current fix proposal, see
[scanner-fix-proposal-20261029.md](scanner-fix-proposal-20261029.md).

## Scope and current state

Owner requested deeper investigation of all four health findings before adding fixes.
This record establishes observed failures, reproduced defects, proposed fixes and
remaining evidence gates. **No application code, tests, settings, container,
production database records or Discord messages were changed.** Implementation
and deployment remain pending. Existing dirty checkout work is preserved.

Investigation used retained rotating diagnostic archives, authenticated container
health, Docker logs, read-only in-container database inspection, live Chrome DOM
and console inspection, source review, and isolated offline reproductions.
Evidence and probes are under `data/investigation-20261029/` (ignored).
All times below are America/Chicago, CDT.

Live browser reports identify extension 2.26.5. Seven inspected local files match
the saved October 28 activation hashes exactly, including content, workspace,
diagnostics and manifest. This establishes consistency with that activation
record; the supported browser interface cannot hash the in-memory extension.
Seven inspected deployed backend module hashes match the current checkout.

At approximately 13:24, all scanner documents were reachable and department and
keyword targets had progressed. No active verification pause was recorded.
The long-lived notifier container remained healthy. A healthy container alone
does not prove useful scan coverage.

## I1 — Overnight zero-throughput loop

**Confirmed impact:** no delivered product page from October 28 **22:58:19.712**
through October 29 **03:14:39.125**, a **256.32-minute gap**. Docker intake logs
independently show the same interval, within normal acknowledgement timing.
The backend watchdog logged relay silence at 01:02:53; this was not an invisible
container outage.

The preserved interval contains 6,822 diagnostic events:

- 1,837 page loads, 1,384 empty-result retries and 112 auxiliary skips.
- 1,493 extraction observations; 1,491 have neither accepted nor rejected links.
  These are extraction observations, not a complete DOM inventory.
- Nine main sweep completions: one with a previously acknowledged page and
  **eight with zero pages**. The zero-page rounds lasted approximately 24–26
  minutes apiece.
- Main restarts in saved mode None repeatedly switch to the high-value fallback
  after confirmed no-results. Finite per-query retries are repeated across
  queries and subsequent scheduled rounds. There is no useful-throughput gate
  stopping this whole cycle.
- There was no recorded challenge episode during this gap. Focus grants
  continued, so the 21-minute FIFO problem in I3 does not explain this entire gap.

**Reproduced behavior:** actual `content.js` in the existing offline VM fixture
can finish a high-value fallback with zero acknowledged pages, emit
`reviewer:sweep-complete`, and set phase `complete`. It also correctly writes a
local warning explaining zero-page completion. Thus the warning exists, while
completion and useful health remain separate concepts. This is a reproduction
of the recovery/status mechanism, not a reproduction of Walmart's overnight
failure.

Source: `extension/content.js:683` (body-wide no-results recognition), `:1774`
(query advancement/completion), `:1824` (zero-page warning), `:2018`
(empty-page confirmation); `extension/background.js:1425` (fallback) and `:1673`
(completion/refresh scheduling).

**Cause not established:** the retained trace lacks overnight DOM state,
response status and browser console history. Site maintenance, session/render
failure, and an actually empty result state cannot be distinguished after the
fact. Live Chrome currently shows cards in all three lanes. Console inspection
also found React 418/423 errors on card-bearing pages; they cannot establish the
overnight cause. Official React references describe [hydration mismatch](https://react.dev/errors/418)
and [client-render recovery](https://react.dev/errors/423).

**Proposed fix and evidence gate:** distinguish a verified empty query, a failed
render, and a whole round with no useful coverage. Establish a bounded global
recovery/backoff policy based on correlated zero-throughput evidence; preserve
the normal handling of legitimate empty searches. Do not mark a round as useful
health merely because its query cursor finished. Before choosing a Walmart
rendering repair, capture bounded structured flags for reviewer/results
readiness, selection confirmation, no-results-panel state, visible cards,
loading/error state, requested/rendered page and document identity. Raw account
text, full DOM, credentials and product-page loads are unnecessary.

Validation must cover a real empty query among healthy queries, full-round
zero-throughput, delayed hydration, recovery after backoff, interior-page gaps,
checkpoint retention and verification/user pauses. More frequent reloads are
not justified by the current evidence.

Evidence: `overnight-summary.json`, compressed `overnight-events.jsonl.gz`,
`overnight-probe-result.json`; rerun:
`node data/investigation-20261029/overnight_probe.mjs`.

## I2 — Incomplete auxiliary coverage and lost error distinctions

At 13:20, departments round 106 had **22/37 verified complete, 8 unresolved,
8 failures**; keywords round 19 had **60/100 complete, 6 unresolved, 6 failures**.
Eight department skip events occurred this round at 07:57–08:19; six keyword
skips occurred this round at 11:01–11:34. Therefore the current warnings are not
merely old carried warnings. Exact one-to-one failed-target mapping is unavailable.

Each department skip follows three target starts/document loads with no
extraction or delivery in the preceding three-minute context. Keyword skip
classifications are four `unexpected-error` and two `content-unresponsive`.

**Reproduced defects and semantics:** six distinct known source failures collapse
to `unexpected-error`: auxiliary render timeout, blank-card timeout, reading
failure, reading-budget exhaustion, repeated-page content and unverified
pagination end. Skip emission omits tab/generation/attempt; sanitization drops
target labels. The persisted `lastError` survives a subsequent healthy target,
so it describes failure history, not necessarily the currently displayed page.
Unresolved coverage can carry into a new round until verified; this is deliberate
coverage preservation and must not be erased.

Source: `extension/auxiliary-scans.js:290` (failure/retries), `:302` (skip event),
`:670` (status); `extension/scanner-control.js:36` (classifier);
`extension/diagnostics.js:144` (sanitization);
`src/scanner_control.py:214` (backend failure-code contract).

**Established corrective design:** emit explicit fixed failure/stage codes and
numeric round/tab/generation/attempt context at the actual failure source;
update JavaScript and Python allowlists together. Separate current-target state,
current-round failures and carried unresolved coverage. Retain acknowledged
checkpoints and finite retry limits. Do not export raw errors or target queries.

**Behavioral-fix gate:** the generic historical events cannot distinguish which
rendering/reading/pagination path failed. Capture the structured state from I1
at that boundary and reproduce the identified condition before changing
selection timing, reading limits or navigation policy. Increasing retries or
removing unresolved flags would hide the failure.

Evidence: `unresolved/current-and-skip-context.json`, `unresolved/probe-result.json`,
`unresolved/probe.mjs`. Existing auxiliary/control/card suites: **70 passed**.

## I3 — Expected browsing pauses versus FIFO starvation

**Expected pause:** October 28 14:50:03–16:35:54 is dominated by
`manual-tab-active`. The intentional guard preserves an unrelated selected tab
while its Chrome window is foreground. This guard should remain.

**Actual defect:** October 28 approximately 22:35:10–22:55:09 had no focus grant.
The main scanner repeatedly received `busy` for scrolling while auxiliary
navigation received `waiting-turn`. No manual-tab or user-activity denial
explains that interval. A grant at 22:55:09 restored progress.

The actual workspace source reproduces starvation: a non-head request acquires
`focusInFlight`; the queued head arrives concurrently, refreshes its queue ticket
and gets `busy`; the non-head request sees the head and returns `waiting-turn`.
Repeated collisions keep that ticket live indefinitely. An offline fake-Chrome
probe with three same-window scanners, valid three-second deadlines, 10-second
scroll/three-second navigation slots and 200 bursts six seconds apart produces
**zero grants over 20 simulated minutes**. Letting the head run first immediately
recovers. This proves the code defect and strongly fits the live interval;
historical scheduler-internal identities were not archived, so exact live
attribution remains inferential.

Source: `extension/tab-workspace.js:357` (manual guard), `:372` (enqueue),
`:383` (request), `:389` (mutex collision), `:431` (FIFO refusal).

**Established corrective design:** arbitrate acquisition by FIFO priority, with
a guarded bounded handoff/wakeup for an eligible head. A non-head must not keep
blocking the head while itself refusing service. Preserve authorization,
request deadlines, stale-head cleanup, pause/user/manual-tab guards, exact
lease release and late-callback protections. Longer leases or removing fairness
do not solve this mechanism.

Validation: adversarial concurrent request order over repeated bursts must give
each eligible contender bounded service. Include expired/unauthorized/removed
heads, request replacement, worker restart, late Chrome callbacks, changing
selection, exact releases and all existing guard tests.

Evidence: `focus/fifo-starvation-probe.mjs`, `focus/fifo-starvation-result.json`,
`focus/archive-evidence.json`. Existing workspace/handoff suites: **69 passed**;
they do not currently cover this collision.

## I4 — Backend/Discord errors and misleading recovery health

**Corrected attribution:** October 29 10:12:51 `OperationalError` came from the
personal-request sender (`tier=pro`, `ambiguous=false`), not public feed delivery.
Intake separately failed at 10:13:28; successful intake at 10:13:24, 10:13:31
and 10:13:33 confirms recovery around that failure. Permanent loss is not proven.
The request commands also logged generic failures at 09:22:37 and 09:22:44.

This is recurrent: retained October 28 logs contain **57 earlier OperationalErrors**:
46 personal-worker errors at 10:47:22–11:41:53; 10 public-worker errors at
11:22:54–11:41:52; one public-worker error at 14:17:02. Some personal errors
are approximately 1.02 seconds apart, which does not support an explanation
that each exhausted the configured ten-second SQLite lock timeout.

**Reproduced diagnostic/health defects:** request health keeps `worker_error`
after successful idle database polls resume; it clears only after a successful
DM. Generic command logging collapses a database error and a programmer
`TypeError` into the same event. SQLite codes, failing operation and elapsed wait
are not retained. A synthetic >10-second writer lock reproduces `SQLITE_BUSY`
and the observed event shape, but does not establish the historical cause.

The one review row is **unrelated historical ambiguity**, created October 27
20:54:42, reason `ambiguous_send`, one attempt and no confirmed message ID.
It must not be blindly replayed. The currently active request was created today
at 13:08:45. A sticky error flag alone does not prove it is failing.

Source: `discord_bridge/core.py:122`/`:139` (SQLite/intake transaction),
`request_store.py:328`/`:513` (request store/claim),
`request_delivery.py:247`/`:260`/`:273`/`:280` (poll/error/health),
`request_commands.py:212` (generic failure), `monitoring.py:190` (allowlist),
`src/server.py:706` (intake rejection).

**Established corrective design:** log fixed operation and exception class,
allowlisted SQLite numeric/symbolic subtype and failure/recovery times. Keep
current successful worker polling separate from last successful DM and retained
historical errors. Do not log exception bodies, SQL parameters or member data.

**Database repair gate:** identify subtype and transaction timing first. Known
writer candidates include the atomic intake batch with request matching,
public claim/rendering within a write transaction, and idle personal-worker
claim transactions every second. No explicit VACUUM/checkpoint operation was
found. Current expiry queries used covering indexes and ran in 17 ms/1.7 ms,
so a current unindexed full scan is not established. If BUSY/LOCKED is confirmed,
measure and shorten/serialize the responsible transaction or add bounded
pre-submission retry; IOERR/READONLY/CORRUPT requires a different repair. Do not
increase timeouts or change journal settings without that evidence.

Validation must cover distinct SQLite failures and command failures, recovery
on a successful idle poll, duplicate-safe intake/retry, restart/cancellation,
and no replay of uncertain post-submission sends.

Evidence: `backend/findings.md`, `backend/backend-evidence-summary.json`,
`backend/offline-probe-results.json`, `backend/older-error-groups.json`.
Existing request/store/command suites: **121 passed** with normal filesystem
permissions; initial sandbox temp-directory errors were environmental.

## Proposed execution order; nothing implemented

1. Add the bounded diagnostic and truthful health distinctions established in
   I1/I2/I4, with compatible backend schemas and isolated privacy/behavior tests.
   This is diagnostic preparation, not proof the hidden underlying faults are fixed.
2. Fix the reproduced FIFO acquisition collision from I3 and demonstrate bounded
   progress under the adversarial probe while preserving existing guards.
3. Select the overnight/auxiliary rendering and SQLite behavioral repairs only
   from newly classified evidence or a faithful isolated reproduction. Keep the
   global zero-throughput recovery policy separate from a Walmart-specific cause.
4. Verify each accepted repair locally and then in a bounded live observation;
   preserve saved scanner settings, checkpoints, card-only eligibility, existing
   pacing, three scanners, 100 keywords, brands off and verification behavior.
   Deploy only reviewed changed files over the recorded running baseline.

The first two corrective designs are concrete. The hidden Walmart rendering and
historical SQLite causes remain unproven; their evidence gates above are explicit.
No diagnostic patch, product fix, restart, extension reload or deployment was made.
Read-only investigators finished; there are no owned background processes or
new browser tabs to clean up. Existing user scanner tabs remain open.
